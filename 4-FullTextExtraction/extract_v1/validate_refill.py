"""
validate_refill.py - Validate OCR-augmented B/C re-extraction replies and
overlay them into extractions_merged.jsonl (Phase 3/4 of the quote-grounding
fix).

Difference from the production validator
----------------------------------------
The production validator (validate_extraction.validate_record) only fails a
quote when a NON-null quote string is not a substring of the source. A null
quote on a positive value PASSES (because is_null_equivalent(None) is True),
and an `_image_only: true` wrapper is explicitly skipped. That leniency is
exactly how Pass B/C reached ~0% quote coverage while "passing".

This refill validator enforces the stricter, locked contract:
  POSITIVE value  -> MUST carry a non-null quote that grounds as a verbatim
                     substring of the OCR-augmented TEXT (ocr_mode).
  ABSENCE value   -> false / not_reported / none / none_reported /
                     not_applicable / empty-list. Value present, no quote.
  `_image_only`   -> NOT accepted; if present with a positive value and no
                     groundable quote, the field FAILS.

Merge + overlay
---------------
Accepted per-chunk records for a paper/pass are merged with the production
chunk-merge policy (merge_passes.merge_chunks_for_paper). A pass is overlaid
into a paper ONLY if every chunk of that pass for that paper was accepted
(no partial overlay). Overlay replaces paper["passes"][pass] WHOLESALE with
the merged refill block; each field is tagged `_refill: true`; an
`_audit.refills[]` entry is appended. Backup to
extractions_merged.jsonl.pre_refill.bak; atomic tmp+rename. Dry-run default.

Usage
-----
  python3 validate_refill.py --pass B            # validate B (dry-run merge)
  python3 validate_refill.py --pass C
  python3 validate_refill.py --pass B --pass C   # both
  python3 validate_refill.py --pass B --pass C --commit   # write overlay
  python3 validate_refill.py --selftest

Python 3.8 compatible.
"""

import argparse
import json
import shutil
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

import paths_step4 as P
import extraction_schema as S
from validate_extraction import (
    is_null_equivalent,
    validate_quote,
    normalise_ocr,
    normalise_nospace,
    _validate_value_type,
    _entry_value_quote,
    _condition_met,
)
from merge_passes import merge_chunks_for_paper


REFILL_PENDING = P.EXTRACT_DIR / "pending_step4_bc"
VERDICTS_REFILL = P.EXTRACT_DIR / "verdicts_refill.jsonl"
QUARANTINE_REFILL = P.EXTRACT_DIR / "quarantine_refill.jsonl"
EXTRACTIONS_MERGED = P.EXTRACT_DIR / "extractions_merged.jsonl"
EXTRACTIONS_BACKUP = P.EXTRACT_DIR / "extractions_merged.jsonl.pre_refill.bak"

PASSES = ("B", "C")

# Enum members that denote absence beyond the validator's null-equivalent set.
_EXTRA_ABSENCE = frozenset(["none_reported"])


# ----------------------------------------------------------------------------
# Absence test (positive => quote required)
# ----------------------------------------------------------------------------
def is_absence(value):
    if is_null_equivalent(value):
        return True
    if value is False:
        return True
    if isinstance(value, str) and value.strip().lower() in _EXTRA_ABSENCE:
        return True
    return False


# ----------------------------------------------------------------------------
# Bounded quote rescue (OCR-artifact tolerant; honesty-preserving)
# ----------------------------------------------------------------------------
# A positive value's full quote may fail to ground because the OCR text injected
# an artifact mid-span (a digit read as a letter, a split/joined token, a
# spelling drift, an [OCR-PAGE-N] boundary). We do NOT fabricate: we accept only
# a verbatim sub-span of what the agent wrote, or the value token itself when it
# grounds. Every rescue is flagged `_quote_trimmed` for human spot-check.
_FRAG_MIN_WORDS = 5
_FRAG_MIN_CHARS = 20
_VALUE_TOKEN_MIN = 4
_VALUE_NOSPACE_MIN = 12


def _longest_grounded_fragment(quote, source_norm):
    """Longest contiguous run of normalised quote words present in source_norm.
    Returns (fragment_string, word_count)."""
    words = normalise_ocr(quote).split()
    n = len(words)
    if n == 0:
        return "", 0
    for length in range(n, 0, -1):
        for i in range(0, n - length + 1):
            frag = " ".join(words[i:i + length])
            if frag in source_norm:
                return frag, length
    return "", 0


def attempt_rescue(value, quote, source_text, source_norm, source_nospace):
    """Return (rescued_quote, match_kind) or (None, None).

    Path 1 (fragment): longest contiguous grounded sub-span of the agent quote,
      accepted at >= _FRAG_MIN_WORDS words and >= _FRAG_MIN_CHARS chars.
    Path 2 (value-token): for string values (URLs / identifiers / names), the
      value itself grounds in the source under OCR normalisation, or under
      whitespace-free normalisation for URL/identifier-like values.
    """
    if quote:
        frag, flen = _longest_grounded_fragment(quote, source_norm)
        if flen >= _FRAG_MIN_WORDS and len(frag) >= _FRAG_MIN_CHARS:
            return frag, "fragment"
    if isinstance(value, str) and value.strip() and not is_absence(value):
        v_ocr = normalise_ocr(value)
        if len(v_ocr) >= _VALUE_TOKEN_MIN and v_ocr in source_norm:
            return value, "value_token"
        v_ns = normalise_nospace(value)
        if len(v_ns) >= _VALUE_NOSPACE_MIN and v_ns in source_nospace:
            return value, "value_token_nospace"
    return None, None


# ----------------------------------------------------------------------------
# Strict per-record validation (B or C, single chunk)
# ----------------------------------------------------------------------------
def validate_refill_record(record, source_text, pass_letter):
    """Return list of (field[, idx], reason) errors. Empty = accepted.

    ocr_mode is always True: the augmented corpus carries OCR text. On a
    positive-value grounding failure, attempts a bounded rescue (verbatim
    sub-span or grounded value token); a rescued entry is mutated in place
    (quote replaced, `_quote_trimmed: true`) and is NOT an error.
    """
    errors = []
    source_norm = normalise_ocr(source_text)
    source_nospace = normalise_nospace(source_text)
    for field, spec in S.SCHEMA.items():
        if spec.pass_ != pass_letter:
            continue
        if spec.derived or spec.human_only or spec.pass_ == S.PASS_META:
            continue
        if field not in record:
            # chunk legitimately may omit a field; merge assembles the set.
            continue
        entry = record[field]

        def _check_one(item, idx):
            val, quote = _entry_value_quote(item)
            ok_t, reason_t = _validate_value_type(spec, val)
            if not ok_t:
                errors.append((field, idx, reason_t) if idx is not None
                              else (field, reason_t))
                return
            if not spec.requires_quote:
                return
            if is_absence(val):
                return
            # POSITIVE value: reject the image-only escape outright.
            if isinstance(item, dict) and item.get("_image_only") is True:
                errors.append((field, idx, "image_only_not_allowed") if idx is not None
                              else (field, "image_only_not_allowed"))
                return
            # Quote must ground. A missing quote on a positive value is a
            # discipline failure and is never rescued. A non-null quote that
            # fails to ground may be rescued (OCR artifact) via a bounded
            # sub-span or grounded value token.
            if is_null_equivalent(quote):
                errors.append((field, idx, "missing_quote_for_positive") if idx is not None
                              else (field, "missing_quote_for_positive"))
                return
            if validate_quote(quote, source_text, ocr_mode=True)[0]:
                return
            rescued, kind = attempt_rescue(
                val, quote, source_text, source_norm, source_nospace)
            if rescued is not None and isinstance(item, dict):
                item["quote"] = rescued
                item["_quote_trimmed"] = True
                item["_quote_match"] = kind
                return
            errors.append((field, idx, "quote_not_in_source") if idx is not None
                          else (field, "quote_not_in_source"))

        if spec.multivalue:
            if not isinstance(entry, list):
                errors.append((field, "expected_list"))
                continue
            for i, item in enumerate(entry):
                _check_one(item, i)
        else:
            _check_one(entry, None)
    return errors


# ----------------------------------------------------------------------------
# Reply loading
# ----------------------------------------------------------------------------
def _count_trims(rec):
    """Count field entries in a record that were rescued via quote trimming."""
    n = 0
    for k, v in rec.items():
        if k == "_meta":
            continue
        items = v if isinstance(v, list) else [v]
        for it in items:
            if isinstance(it, dict) and it.get("_quote_trimmed") is True:
                n += 1
    return n


def _load_task(task_path):
    return json.loads(task_path.read_text(encoding="utf-8"))


def _read_source_text(text_path):
    p = Path(text_path)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def _done_ids():
    """(stable, pass, chunk) already in verdicts_refill.jsonl."""
    done = set()
    if not VERDICTS_REFILL.exists():
        return done
    with open(VERDICTS_REFILL, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            m = rec.get("_meta", {})
            done.add((m.get("stable_name"), m.get("pass"), m.get("chunk_id")))
    return done


def validate_pass(pass_letter):
    """Validate all replies for one pass. Append accepted to verdicts_refill,
    failures to quarantine_refill. Returns counts dict."""
    pdir = REFILL_PENDING / pass_letter
    counts = {"tasks": 0, "replies": 0, "accepted": 0,
              "quarantined": 0, "no_reply": 0, "trimmed": 0}
    if not pdir.exists():
        print("  pass {}: not prepared ({} absent)".format(pass_letter, pdir))
        return counts

    done = _done_ids()
    tasks = sorted(pdir.glob("*.task.json"))
    counts["tasks"] = len(tasks)

    with open(VERDICTS_REFILL, "a", encoding="utf-8") as vout, \
         open(QUARANTINE_REFILL, "a", encoding="utf-8") as qout:
        for tpath in tasks:
            task = _load_task(tpath)
            stable = task["stable_name"]
            cid = task["chunk_id"]
            key = (stable, pass_letter, cid)
            if key in done:
                continue
            rpath = Path(task["reply_path"])
            if not rpath.exists():
                counts["no_reply"] += 1
                continue
            counts["replies"] += 1
            try:
                rec = json.loads(rpath.read_text(encoding="utf-8"))
            except Exception as exc:
                counts["quarantined"] += 1
                qout.write(json.dumps({
                    "_meta": {"stable_name": stable, "pass": pass_letter,
                              "chunk_id": cid},
                    "_errors": [["__parse__", "json_error: {}".format(exc)]],
                }) + "\n")
                continue

            src = _read_source_text(task["text_path"])
            if src is None:
                counts["quarantined"] += 1
                qout.write(json.dumps({
                    "_meta": {"stable_name": stable, "pass": pass_letter,
                              "chunk_id": cid},
                    "_errors": [["__source__", "augmented_text_missing"]],
                }) + "\n")
                continue

            errs = validate_refill_record(rec, src, pass_letter)
            # ensure _meta carries identity for the merge step
            meta = rec.setdefault("_meta", {})
            meta["stable_name"] = stable
            meta["pass"] = pass_letter
            meta["chunk_id"] = cid
            meta["_is_refill"] = True

            if errs:
                counts["quarantined"] += 1
                rec["_errors"] = [list(e) for e in errs]
                qout.write(json.dumps(rec) + "\n")
            else:
                counts["accepted"] += 1
                counts["trimmed"] += _count_trims(rec)
                vout.write(json.dumps(rec) + "\n")

    print("  pass {}: tasks={} replies={} accepted={} quarantined={} no_reply={} trimmed_fields={}"
          .format(pass_letter, counts["tasks"], counts["replies"],
                  counts["accepted"], counts["quarantined"], counts["no_reply"],
                  counts["trimmed"]))
    return counts


# ----------------------------------------------------------------------------
# Merge accepted refills per (paper, pass)
# ----------------------------------------------------------------------------
def _accepted_by_paper_pass():
    """Return {(stable, pass): [chunk_records...]} and the set of (stable,pass)
    that have any quarantined chunk (so we skip overlaying them)."""
    by = defaultdict(list)
    if VERDICTS_REFILL.exists():
        with open(VERDICTS_REFILL, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                m = rec.get("_meta", {})
                by[(m.get("stable_name"), m.get("pass"))].append(rec)
    bad = set()
    if QUARANTINE_REFILL.exists():
        with open(QUARANTINE_REFILL, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                m = rec.get("_meta", {})
                bad.add((m.get("stable_name"), m.get("pass")))
    for k in by:
        by[k].sort(key=lambda r: r.get("_meta", {}).get("chunk_id", 0))
    return by, bad


def _tag_refill(field_value):
    if isinstance(field_value, dict):
        nv = dict(field_value)
        nv["_refill"] = True
        return nv
    if isinstance(field_value, list):
        return [_tag_refill(x) for x in field_value]
    return field_value


def _build_merged_blocks():
    """Return {stable: {pass: merged_block_dict}} for fully-accepted (paper,pass).

    A (paper, pass) with any quarantined chunk is skipped (reported by caller).
    """
    by, bad = _accepted_by_paper_pass()
    out = defaultdict(dict)
    skipped = []
    for (stable, pass_letter), chunk_recs in by.items():
        if (stable, pass_letter) in bad:
            skipped.append((stable, pass_letter))
            continue
        merged = merge_chunks_for_paper(pass_letter, chunk_recs)
        # strip _meta from the merged field block; keep only schema fields
        block = {}
        for field, spec in S.SCHEMA.items():
            if spec.pass_ != pass_letter or spec.derived or spec.human_only:
                continue
            if field in merged:
                block[field] = _tag_refill(merged[field])
        out[stable][pass_letter] = block
    return out, skipped


# ----------------------------------------------------------------------------
# Overlay into extractions_merged.jsonl
# ----------------------------------------------------------------------------
def merge_refills(commit=False):
    if not EXTRACTIONS_MERGED.exists():
        return {"error": "extractions_merged.jsonl not found"}, [], []

    blocks, skipped = _build_merged_blocks()
    if not blocks:
        return {"papers_touched": 0, "fields_written": 0, "passes_written": 0,
                "skipped_partial": len(skipped)}, [], skipped

    papers_out = []
    counts = {"papers_touched": 0, "fields_written": 0, "passes_written": 0,
              "total_papers": 0, "skipped_partial": len(skipped),
              "papers_not_found": 0}
    summaries = []

    with open(EXTRACTIONS_MERGED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            paper = json.loads(line)
            counts["total_papers"] += 1
            stable = (paper.get("stable_name")
                      or paper.get("_meta", {}).get("stable_name", ""))
            if not stable or stable not in blocks:
                papers_out.append(paper)
                continue
            passes = paper.setdefault("passes", {})
            paper_fields = 0
            paper_passes = []
            audit = paper.setdefault("_audit", {})
            refill_log = audit.setdefault("refills", [])
            for pass_letter, block in blocks[stable].items():
                passes[pass_letter] = block  # WHOLESALE replace
                paper_fields += len(block)
                paper_passes.append(pass_letter)
                refill_log.append({
                    "pass": pass_letter,
                    "fields_written": sorted(block.keys()),
                    "source": "ocr_augmented_refill",
                })
            counts["papers_touched"] += 1
            counts["fields_written"] += paper_fields
            counts["passes_written"] += len(paper_passes)
            summaries.append({
                "stable_name": stable,
                "passes": paper_passes,
                "fields_written": paper_fields,
            })
            papers_out.append(paper)

    seen = {p.get("stable_name")
            or p.get("_meta", {}).get("stable_name", "") for p in papers_out}
    for stable in blocks:
        if stable not in seen:
            counts["papers_not_found"] += 1

    if commit:
        shutil.copy2(EXTRACTIONS_MERGED, EXTRACTIONS_BACKUP)
        tmp = EXTRACTIONS_MERGED.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as out:
            for p in papers_out:
                out.write(json.dumps(p) + "\n")
        tmp.replace(EXTRACTIONS_MERGED)

    return counts, summaries, skipped


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    src = ("We propose a hybrid quantum-classical CNN trained in PennyLane. "
           "[OCR-PAGE-2] Table 2 reports accuracy 0.91 for the quantum model "
           "and 0.88 for the classical ResNet-18 baseline. Five-fold "
           "cross-validation was used.")
    # Positive value with a grounded quote -> accepted
    rec_ok = {
        "paradigm": {"value": "hybrid_quantum_classical",
                     "quote": "hybrid quantum-classical CNN", "pass_id": "B"},
        "real_or_simulator": {"value": "simulator",
                              "quote": "trained in PennyLane", "pass_id": "B"},
        "hardware_modality": {"value": "not_reported", "quote": None},
        "qubit_count": {"value": "not_reported", "quote": None},
    }
    e1 = validate_refill_record(rec_ok, src, "B")
    print("Test 1 (positive+grounded, absence no-quote):",
          "PASS" if not e1 else "FAIL " + str(e1))

    # Positive value with null quote -> must fail
    rec_bad = dict(rec_ok)
    rec_bad["paradigm"] = {"value": "hybrid_quantum_classical", "quote": None}
    e2 = validate_refill_record(rec_bad, src, "B")
    print("Test 2 (positive+null quote fails):",
          "PASS" if any("missing_quote_for_positive" in str(x) for x in e2)
          else "FAIL " + str(e2))

    # image_only escape -> must fail
    rec_io = dict(rec_ok)
    rec_io["paradigm"] = {"value": "hybrid_quantum_classical", "quote": None,
                          "_image_only": True}
    e3 = validate_refill_record(rec_io, src, "B")
    print("Test 3 (image_only rejected):",
          "PASS" if any("image_only_not_allowed" in str(x) for x in e3)
          else "FAIL " + str(e3))

    # Grounded metric in OCR block -> accepted (C)
    rec_c = {
        "classical_baseline_present": {"value": True,
                                       "quote": "classical ResNet-18 baseline",
                                       "pass_id": "C"},
        "classical_baseline_identification": {
            "value": "classical ResNet-18",
            "quote": "classical ResNet-18 baseline", "pass_id": "C"},
        "performance_metrics_quantum": [
            {"value": {"metric_name": "accuracy", "value": "0.91"},
             "quote": "accuracy 0.91 for the quantum model", "pass_id": "C"}],
        "cross_validation_strategy": {"value": "k_fold",
                                      "quote": "Five-fold cross-validation",
                                      "pass_id": "C"},
        "code_release": {"value": False, "quote": None},
        "baseline_rigour_grade": {"value": "matched_data",
                                  "quote": "for the quantum model and 0.88 for the classical",
                                  "pass_id": "C"},
    }
    e4 = validate_refill_record(rec_c, src, "C")
    print("Test 4 (C grounded incl. OCR-block quote):",
          "PASS" if not e4 else "FAIL " + str(e4))

    # Failing non-null quote but value token grounds -> rescued, not error
    rec_resc = {
        "simulator_framework": [
            {"value": "PennyLane",
             "quote": "executed the circuits on PennyLane simulator backend",
             "pass_id": "B"}],
    }
    e5 = validate_refill_record(rec_resc, src, "B")
    it = rec_resc["simulator_framework"][0]
    print("Test 5 (failing quote rescued via value token):",
          "PASS" if (not e5 and it.get("_quote_trimmed") is True) else
          "FAIL " + str(e5) + " " + str(it))


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Validate + overlay B/C refill.")
    ap.add_argument("--pass", dest="passes", action="append", default=[],
                    help="B and/or C (repeatable).")
    ap.add_argument("--commit", action="store_true",
                    help="Write the overlay into extractions_merged.jsonl.")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        _selftest()
        return 0

    passes = [p.upper() for p in args.passes] or list(PASSES)
    for p in passes:
        if p not in PASSES:
            print("ERROR: --pass must be B or C", file=sys.stderr)
            return 2

    print("Validating refill replies:")
    for p in passes:
        validate_pass(p)

    print("\nMerge preview (dry-run)" if not args.commit else "\nMerge (COMMIT)")
    counts, summaries, skipped = merge_refills(commit=args.commit)
    print(json.dumps(counts, indent=2))
    if skipped:
        print("\nSKIPPED (partial: some chunk quarantined) — re-dispatch these:")
        for stable, pl in skipped:
            print("  {}  pass {}".format(stable, pl))
    if not args.commit:
        print("\nDry run only. Re-run with --commit to write the overlay.")
    else:
        print("\nBackup:", EXTRACTIONS_BACKUP)
        print("Next: python3 compute_derived.py && python3 extractions_to_xlsx.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
