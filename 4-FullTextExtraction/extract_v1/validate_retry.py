"""
validate_retry.py — Validate retry replies from pending_step4_retry/ and
merge accepted retries into extractions_merged.jsonl.

Mirrors the validation contract of `batch_helper_step4.py cmd_validate` but:
  - Reads retry replies from pending_step4_retry/<pass>/*.retry.verdict.json
  - Writes accepted retries to verdicts_retry.jsonl (separate from production)
  - Writes re-failures to quarantine_retry.jsonl (separate)
  - Field-scoped overwrite: for each accepted retry record, replaces ONLY the
    fields named in _retry_context.failed_fields. All other fields untouched.
  - Each overwritten field gets `_retry: true` audit flag.
  - Backup-before-write: extractions_merged.jsonl.pre_retry.bak created.
  - Atomic write via tmp + rename.
  - DRY-RUN DEFAULT. Use --commit to actually write the merge.

Two-step contract:
  python3 validate_retry.py                  # validate + dry-run merge preview
  python3 validate_retry.py --commit         # validate + execute merge

The validate step is idempotent (append-only verdicts_retry.jsonl; skip
already-validated by stable+chunk+pass). Re-running re-validates anything
newly arrived in pending_step4_retry/.

Python 3.8 compatible. Reuses validate_extraction + rescue_extraction
modules from the production pipeline; no behaviour drift.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import paths_step4 as P
from validate_extraction import validate_record, resolve_source_text
from rescue_extraction import rescue_record


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
RETRY_PENDING = P.EXTRACT_DIR / "pending_step4_retry"
RETRY_PROCESSED = P.EXTRACT_DIR / "processed_step4_retry"
VERDICTS_RETRY = P.EXTRACT_DIR / "verdicts_retry.jsonl"
QUARANTINE_RETRY = P.EXTRACT_DIR / "quarantine_retry.jsonl"
EXTRACTIONS_MERGED = P.EXTRACT_DIR / "extractions_merged.jsonl"
EXTRACTIONS_BACKUP = P.EXTRACT_DIR / "extractions_merged.jsonl.pre_retry.bak"

PASSES = ["A", "B", "C", "D"]


# ----------------------------------------------------------------------------
# Already-validated tracking (resumable)
# ----------------------------------------------------------------------------
def _done_retry_ids():
    """Read verdicts_retry.jsonl and return set of (stable, chunk, pass)."""
    done = set()
    if not VERDICTS_RETRY.exists():
        return done
    with open(VERDICTS_RETRY, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            meta = rec.get("_meta", {})
            stable = meta.get("stable_name", "")
            chunk = meta.get("chunk_id", 0)
            p = meta.get("pass", "")
            if stable and p:
                done.add((stable, chunk, p))
    return done


# ----------------------------------------------------------------------------
# Validate one retry pass directory
# ----------------------------------------------------------------------------
def validate_pass(pass_letter, done_set):
    """Validate all retry replies for one pass. Returns counts dict."""
    pdir = RETRY_PENDING / pass_letter
    if not pdir.exists():
        return {"accepted": 0, "rescued": 0, "quarantined": 0, "skipped": 0, "no_reply": 0}

    processed = RETRY_PROCESSED / pass_letter
    processed.mkdir(parents=True, exist_ok=True)

    n_accepted = 0
    n_rescued = 0
    n_quarantined = 0
    n_skipped = 0
    n_no_reply = 0

    with open(VERDICTS_RETRY, "a", encoding="utf-8") as vout, \
         open(QUARANTINE_RETRY, "a", encoding="utf-8") as qout:

        for tpath in sorted(pdir.glob("*.retry.task.json")):
            task = json.loads(tpath.read_text(encoding="utf-8"))
            stable = task["stable_name"]
            cid = task.get("chunk_id", 0)
            if (stable, cid, pass_letter) in done_set:
                n_skipped += 1
                continue
            rp = Path(task["reply_path"])
            if not rp.exists():
                n_no_reply += 1
                continue
            try:
                rec = json.loads(rp.read_text(encoding="utf-8"))
            except Exception as e:
                qrec = {
                    "_meta": {"stable_name": stable, "chunk_id": cid,
                              "pass": pass_letter, "_is_retry": True},
                    "_error": "json_parse_error: {}".format(e),
                    "_raw_path": str(rp),
                }
                qout.write(json.dumps(qrec) + "\n")
                n_quarantined += 1
                continue

            # Ensure _meta + retry markers
            rec.setdefault("_meta", {})
            rec["_meta"]["stable_name"] = stable
            rec["_meta"]["chunk_id"] = cid
            rec["_meta"]["pass"] = pass_letter
            rec["_meta"]["doi"] = task.get("doi", "")
            rec["_meta"]["ocr_source"] = task.get("ocr_source", False)
            rec["_meta"]["_is_retry"] = True
            rec["_meta"]["_retry_context"] = task.get("_retry_context", {})

            # Retries are only on passes A/B/C/D. (Pass E was zero quarantine.)
            # Use the same full-text grounding rule as production.
            source_text = resolve_source_text(stable) or ""
            ocr_mode = bool(task.get("ocr_source", False))

            errs = validate_record(rec, source_text, ocr_mode=ocr_mode,
                                   pass_filter=pass_letter)
            if not errs:
                vout.write(json.dumps(rec) + "\n")
                n_accepted += 1
                shutil.move(str(rp), str(processed / rp.name))
                continue

            # Structural vs quote split (same as production)
            def _is_quote_err(e):
                parts = e if isinstance(e, tuple) else (e,)
                return any("quote_not_in_source" in str(x) for x in parts)

            structural = [e for e in errs if not _is_quote_err(e)]
            if structural:
                qrec = dict(rec)
                qrec["_error"] = [str(e) for e in errs]
                qout.write(json.dumps(qrec) + "\n")
                n_quarantined += 1
                continue

            # Quote-only errors → rescue ladder
            rescued, report = rescue_record(rec, source_text)
            unrescued = [r for r in report if r.get("level") == "unverified"]
            if not unrescued:
                rescued["_meta"]["_rescued"] = True
                vout.write(json.dumps(rescued) + "\n")
                n_rescued += 1
                shutil.move(str(rp), str(processed / rp.name))
                continue
            qrec = dict(rescued)
            qrec["_error"] = ["unrescued_quote:{}".format(r["field"]) for r in unrescued]
            qout.write(json.dumps(qrec) + "\n")
            n_quarantined += 1

    return {
        "accepted": n_accepted,
        "rescued": n_rescued,
        "quarantined": n_quarantined,
        "skipped": n_skipped,
        "no_reply": n_no_reply,
    }


# ----------------------------------------------------------------------------
# Merge step
# ----------------------------------------------------------------------------
def _retry_records_by_paper():
    """Read verdicts_retry.jsonl and return {(stable, pass_id): record}.

    If multiple retries exist for the same (stable, pass), the LATEST wins
    (file is append-only; later = later in the file)."""
    out = {}
    if not VERDICTS_RETRY.exists():
        return out
    with open(VERDICTS_RETRY, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            meta = rec.get("_meta", {})
            stable = meta.get("stable_name", "")
            p = meta.get("pass", "")
            if stable and p in PASSES:
                out[(stable, p)] = rec
    return out


def _failed_field_names(retry_rec):
    """Return list of field names the retry was supposed to fix."""
    ctx = retry_rec.get("_meta", {}).get("_retry_context", {})
    fields = ctx.get("failed_fields") or []
    out = []
    for f in fields:
        if isinstance(f, dict) and f.get("field"):
            out.append(f["field"])
    return out


def _tag_retry(field_value):
    """Apply _retry: true tag to a field value (wrapper dict or list)."""
    if isinstance(field_value, dict):
        new = dict(field_value)
        new["_retry"] = True
        return new
    if isinstance(field_value, list):
        return [_tag_retry(x) for x in field_value]
    # plain scalar — not the wrapper shape; leave untouched
    return field_value


def _overlay_retry_into_paper(paper_rec, retry_rec, failed_fields):
    """Return a NEW paper record with the named fields replaced from the retry.

    The retry record's `passes.<pass>.<field>` structure mirrors what the
    consolidate step produced for the paper. We locate the pass and overlay
    only the named fields. The original quarantine-flagged values are
    replaced; everything else in paper_rec is untouched.

    Returns (new_paper_rec, n_fields_overwritten, fields_missing_in_retry).
    """
    pass_id = retry_rec.get("_meta", {}).get("pass", "")
    if not pass_id:
        return paper_rec, 0, []

    new_paper = json.loads(json.dumps(paper_rec))  # deep copy
    passes = new_paper.setdefault("passes", {})
    pass_block = passes.setdefault(pass_id, {})

    n_overwritten = 0
    missing = []

    for field in failed_fields:
        if field not in retry_rec:
            missing.append(field)
            continue
        new_val = _tag_retry(retry_rec[field])
        pass_block[field] = new_val
        n_overwritten += 1

    # Record the retry event in the paper's audit trail
    audit = new_paper.setdefault("_audit", {})
    retry_log = audit.setdefault("retries", [])
    retry_log.append({
        "pass": pass_id,
        "chunk_id": retry_rec.get("_meta", {}).get("chunk_id", 0),
        "fields_overwritten": [f for f in failed_fields if f not in missing],
        "fields_missing_in_retry": missing,
    })

    return new_paper, n_overwritten, missing


def merge_retries(commit=False):
    """Read verdicts_retry.jsonl and overlay accepted retry fields into
    extractions_merged.jsonl. DRY-RUN by default; --commit to write.

    Returns counts dict and a list of per-paper change summaries."""
    if not EXTRACTIONS_MERGED.exists():
        return {"error": "extractions_merged.jsonl not found",
                "papers_touched": 0, "fields_overwritten": 0,
                "fields_missing": 0, "papers_not_found": 0}, []

    retries_by_paper = _retry_records_by_paper()
    if not retries_by_paper:
        return {"papers_touched": 0, "fields_overwritten": 0,
                "fields_missing": 0, "papers_not_found": 0}, []

    # Index retries by stable_name → list of (pass_id, retry_rec)
    by_stable = defaultdict(list)
    for (stable, pass_id), rec in retries_by_paper.items():
        by_stable[stable].append((pass_id, rec))

    # Stream papers; for each one matched in by_stable, overlay
    papers_out = []
    counts = {"papers_touched": 0, "fields_overwritten": 0,
              "fields_missing": 0, "papers_not_found": 0,
              "total_papers": 0}
    summaries = []

    with open(EXTRACTIONS_MERGED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            paper = json.loads(line)
            counts["total_papers"] += 1
            stable = paper.get("stable_name") or paper.get("_meta", {}).get("stable_name", "")
            if not stable or stable not in by_stable:
                papers_out.append(paper)
                continue
            modified = paper
            paper_n_over = 0
            paper_n_miss = 0
            for pass_id, retry_rec in by_stable[stable]:
                fields = _failed_field_names(retry_rec)
                modified, n_over, missing = _overlay_retry_into_paper(
                    modified, retry_rec, fields)
                paper_n_over += n_over
                paper_n_miss += len(missing)
            counts["papers_touched"] += 1
            counts["fields_overwritten"] += paper_n_over
            counts["fields_missing"] += paper_n_miss
            summaries.append({
                "stable_name": stable,
                "fields_overwritten": paper_n_over,
                "fields_missing_in_retry": paper_n_miss,
                "passes": [p for p, _ in by_stable[stable]],
            })
            papers_out.append(modified)

    # Detect any retry whose paper wasn't found
    seen_stables = {p.get("stable_name") or p.get("_meta", {}).get("stable_name", "")
                    for p in papers_out}
    for stable in by_stable:
        if stable not in seen_stables:
            counts["papers_not_found"] += 1

    if commit:
        # Backup first
        shutil.copy2(EXTRACTIONS_MERGED, EXTRACTIONS_BACKUP)
        # Atomic write
        tmp = EXTRACTIONS_MERGED.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as out:
            for p in papers_out:
                out.write(json.dumps(p) + "\n")
        tmp.replace(EXTRACTIONS_MERGED)

    return counts, summaries


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--commit", action="store_true",
                        help="Actually write the merge (default: dry-run).")
    parser.add_argument("--skip-validate", action="store_true",
                        help="Skip the validate step; only run the merge.")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not RETRY_PENDING.exists() and not args.skip_validate:
        print("ERROR: {} not found. Run retry_quarantined.py first.".format(
            RETRY_PENDING), file=sys.stderr)
        return 2

    # ---- Validate ----
    if not args.skip_validate:
        print("=" * 70)
        print("STEP 1: Validate retry replies")
        print("=" * 70)
        done = _done_retry_ids()
        if done:
            print("Already-validated (resumable):", len(done))
        for p in PASSES:
            counts = validate_pass(p, done)
            total_present = (counts["accepted"] + counts["rescued"] +
                             counts["quarantined"] + counts["skipped"])
            print("Pass {} retry: accepted={}, rescued={}, quarantined={}, skipped={}, no_reply={}".format(
                p, counts["accepted"], counts["rescued"], counts["quarantined"],
                counts["skipped"], counts["no_reply"]))
        print()

    # ---- Merge ----
    print("=" * 70)
    print("STEP 2: Merge accepted retries into extractions_merged.jsonl")
    if not args.commit:
        print("  (DRY RUN — nothing written. Use --commit to apply.)")
    print("=" * 70)
    counts, summaries = merge_retries(commit=args.commit)
    if "error" in counts:
        print("ERROR:", counts["error"], file=sys.stderr)
        return 2
    print("Total papers in extractions_merged.jsonl: {}".format(counts.get("total_papers", 0)))
    print("Papers with retry overlays:               {}".format(counts["papers_touched"]))
    print("Fields overwritten by retry:              {}".format(counts["fields_overwritten"]))
    print("Failed fields not present in retry reply: {}".format(counts["fields_missing"]))
    print("Retries whose paper not found in merged:  {}".format(counts["papers_not_found"]))
    print()
    if summaries:
        print("Per-paper summary (first 10):")
        for s in summaries[:10]:
            print("  {}: passes={}, +fields={}, missing_in_retry={}".format(
                s["stable_name"], s["passes"], s["fields_overwritten"],
                s["fields_missing_in_retry"]))
        if len(summaries) > 10:
            print("  ... and {} more.".format(len(summaries) - 10))
    print()
    if args.commit:
        print("Wrote:", EXTRACTIONS_MERGED)
        print("Backup:", EXTRACTIONS_BACKUP)
        print()
        print("Next: regenerate workbook with `python3 extractions_to_xlsx.py`")
        print("and run `python3 patch_quarantine_flags.py` (TBD) to flag")
        print("any residual unrecoverable quarantines for human spot-check.")
    else:
        print("Dry run only. Inspect the counts and per-paper summary above.")
        print("To apply the merge: rerun with --commit.")
    return 0


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    """Test the merge-overlay logic. Validation path requires the production
    validate_extraction module + real paper text, so we only test merge here."""
    # Fixture paper record (as produced by merge_passes.py --consolidate)
    paper = {
        "stable_name": "p1",
        "doi": "10.x/p1",
        "passes": {
            "A": {
                "title": {"value": "Test Paper", "quote": "Test Paper",
                          "chunk_id": 0, "pass_id": "A"},
                "funding_declared": {"value": True, "quote": "fake quote that failed",
                                     "chunk_id": 0, "pass_id": "A"},
                "modality_primary": {"value": "MRI", "quote": "wrong",
                                     "chunk_id": 0, "pass_id": "A"},
                "country_corresponding": {"value": "USA", "quote": "ok quote",
                                          "chunk_id": 0, "pass_id": "A"},
            }
        },
    }
    # Retry record with overlaid fields
    retry = {
        "_meta": {
            "stable_name": "p1",
            "chunk_id": 0,
            "pass": "A",
            "_is_retry": True,
            "_retry_context": {
                "stable_name": "p1",
                "pass_id": "A",
                "chunk_id": 0,
                "failed_fields": [
                    {"field": "funding_declared", "reason": "unrescued_quote"},
                    {"field": "modality_primary", "reason": "unrescued_quote"},
                ],
            },
        },
        "funding_declared": {"value": True, "quote": None, "_image_only": True,
                             "chunk_id": 0, "pass_id": "A"},
        "modality_primary": {"value": "CT", "quote": "computed tomography",
                             "chunk_id": 0, "pass_id": "A"},
    }
    fields = _failed_field_names(retry)
    assert fields == ["funding_declared", "modality_primary"], fields

    new, n_over, missing = _overlay_retry_into_paper(paper, retry, fields)
    assert n_over == 2, n_over
    assert missing == []
    # funding_declared replaced
    assert new["passes"]["A"]["funding_declared"]["_image_only"] is True
    assert new["passes"]["A"]["funding_declared"]["_retry"] is True
    assert new["passes"]["A"]["funding_declared"]["quote"] is None
    # modality_primary replaced (value changed from MRI to CT)
    assert new["passes"]["A"]["modality_primary"]["value"] == "CT"
    assert new["passes"]["A"]["modality_primary"]["_retry"] is True
    # country_corresponding untouched (not in failed_fields)
    assert new["passes"]["A"]["country_corresponding"]["value"] == "USA"
    assert "_retry" not in new["passes"]["A"]["country_corresponding"]
    assert new["passes"]["A"]["country_corresponding"]["quote"] == "ok quote"
    # title untouched
    assert new["passes"]["A"]["title"]["value"] == "Test Paper"
    assert "_retry" not in new["passes"]["A"]["title"]
    # Audit trail recorded
    assert "_audit" in new
    assert "retries" in new["_audit"]
    assert len(new["_audit"]["retries"]) == 1
    assert new["_audit"]["retries"][0]["fields_overwritten"] == ["funding_declared", "modality_primary"]
    # Original paper unchanged (deep-copy verified)
    assert paper["passes"]["A"]["funding_declared"]["quote"] == "fake quote that failed"
    print("SELFTEST Test 1 (overlay 2 fields): PASS")

    # Test missing field in retry
    retry2 = {
        "_meta": {
            "stable_name": "p1", "chunk_id": 0, "pass": "A",
            "_retry_context": {"failed_fields": [
                {"field": "funding_declared"},
                {"field": "nonexistent_field"},
            ]},
        },
        "funding_declared": {"value": False, "quote": None, "_image_only": True,
                             "chunk_id": 0, "pass_id": "A"},
    }
    new2, n_over2, missing2 = _overlay_retry_into_paper(
        paper, retry2, _failed_field_names(retry2))
    assert n_over2 == 1
    assert missing2 == ["nonexistent_field"]
    assert new2["passes"]["A"]["funding_declared"]["value"] is False
    print("SELFTEST Test 2 (missing field in retry): PASS")

    # Test list-valued field
    paper3 = {
        "stable_name": "p1",
        "passes": {"A": {
            "authors": [
                {"value": {"family": "X", "given": "Y"}, "quote": "X Y",
                 "chunk_id": 0, "pass_id": "A"},
            ]
        }},
    }
    retry3 = {
        "_meta": {"stable_name": "p1", "chunk_id": 0, "pass": "A",
                  "_retry_context": {"failed_fields": [{"field": "authors"}]}},
        "authors": [
            {"value": {"family": "Smith", "given": "J"}, "quote": "Smith J",
             "chunk_id": 0, "pass_id": "A"},
            {"value": {"family": "Doe", "given": "K"}, "quote": "Doe K",
             "chunk_id": 0, "pass_id": "A"},
        ],
    }
    new3, n_over3, missing3 = _overlay_retry_into_paper(
        paper3, retry3, _failed_field_names(retry3))
    assert n_over3 == 1
    assert len(new3["passes"]["A"]["authors"]) == 2
    # All list items tagged with _retry
    assert all(a["_retry"] is True for a in new3["passes"]["A"]["authors"])
    print("SELFTEST Test 3 (list-valued field): PASS")

    print("SELFTEST: ALL PASS")


if __name__ == "__main__":
    sys.exit(main())
