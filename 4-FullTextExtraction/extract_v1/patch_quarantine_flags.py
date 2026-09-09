"""
patch_quarantine_flags.py - Mark genuinely-unverified extraction cells with
`_quote_unverified` in extractions_merged.jsonl, for Step-5 human spot-check.

Why source-of-truth, not a quarantine-file union
------------------------------------------------
Three quarantine files exist from different runs:
  - quarantine_refill.jsonl   (current; authoritative for B/C after the refill)
  - quarantine_step4.jsonl    (original production; A/B/C/D)
  - quarantine_retry.jsonl    (Step-4.6 retry; A/B)
Their B/C entries are stale (the refill re-extracted all of B and C), and some
of their A entries were recovered by the Step-4.6 retry. Unioning them would
stamp `_quote_unverified` on cells that are now grounded.

This script instead decides per cell from the LIVE merged file:
  candidate cells = (B/C from quarantine_refill) + (A/D from the two old files);
  a candidate is flagged ONLY IF, in the current extractions_merged.jsonl, its
  field has a positive value whose quote does not ground against the source
  text (augmented text for B/C, original text for A/D). Anything the refill or
  retry already grounded is automatically excluded.

The value is never modified. Only `_quote_unverified: true` (and a short
`_quote_unverified_reason`) is added to the failing field entry. The flag
surfaces in extractions_ai.xlsx via the existing audit_flags `quote_unverified`
column after the normal compute_derived + extractions_to_xlsx rebuild. No
direct workbook editing.

Usage
-----
  python3 patch_quarantine_flags.py            # dry-run: report what would flag
  python3 patch_quarantine_flags.py --commit    # write flags into merged jsonl
  python3 patch_quarantine_flags.py --selftest

Python 3.8 compatible.
"""

import argparse
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import paths_step4 as P
import extraction_schema as S
from validate_extraction import is_null_equivalent, validate_quote, normalise_ocr


MERGED = P.EXTRACT_DIR / "extractions_merged.jsonl"
BACKUP = P.EXTRACT_DIR / "extractions_merged.jsonl.pre_flag.bak"

QUAR_REFILL = P.EXTRACT_DIR / "quarantine_refill.jsonl"
QUAR_STEP4 = P.EXTRACT_DIR / "quarantine_step4.jsonl"
QUAR_RETRY = P.EXTRACT_DIR / "quarantine_retry.jsonl"

PDF_TEXT_AUG_DIR = P.EXTRACT_DIR / "PDFs_step4_text_aug"

_EXTRA_ABSENCE = frozenset(["none_reported"])


def is_absence(value):
    if is_null_equivalent(value):
        return True
    if value is False:
        return True
    if isinstance(value, str) and value.strip().lower() in _EXTRA_ABSENCE:
        return True
    return False


# ----------------------------------------------------------------------------
# Candidate collection from the three quarantine files (scoped by pass)
# ----------------------------------------------------------------------------
def _fields_from_errors(rec):
    """Field names referenced in a quarantine record's error list(s)."""
    out = set()
    errs = rec.get("_errors")
    if errs is None and "_error" in rec:
        errs = rec.get("_error")
    for e in errs or []:
        if isinstance(e, (list, tuple)) and e:
            out.add(e[0])
        elif isinstance(e, str):
            out.add(e)
    return out


def _read_candidates(path, keep_passes):
    """Return {(stable, pass): set(fields)} for records whose pass is in
    keep_passes."""
    cands = defaultdict(set)
    if not path.exists():
        return cands
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            m = rec.get("_meta", {})
            pl = m.get("pass") or m.get("pass_id")
            stable = m.get("stable_name")
            if not stable or pl not in keep_passes:
                continue
            flds = _fields_from_errors(rec)
            flds.discard("__parse__")
            flds.discard("__source__")
            if flds:
                cands[(stable, pl)] |= flds
    return cands


def collect_candidates():
    """B/C from the refill file; A/D from the two original files."""
    cands = defaultdict(set)
    for k, v in _read_candidates(QUAR_REFILL, {"B", "C"}).items():
        cands[k] |= v
    for src in (QUAR_STEP4, QUAR_RETRY):
        for k, v in _read_candidates(src, {"A", "D"}).items():
            cands[k] |= v
    return cands


# ----------------------------------------------------------------------------
# Source-text resolution (augmented for B/C, original for A/D)
# ----------------------------------------------------------------------------
def _source_text(stable, pass_letter):
    if pass_letter in ("B", "C"):
        p = PDF_TEXT_AUG_DIR / (stable + ".txt")
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    p = P.PDF_TEXT_DIR / (stable + ".txt")
    if p.exists():
        return p.read_text(encoding="utf-8", errors="replace")
    return ""


def _entry_grounds(item, source_text):
    """True if this field entry is grounded or legitimately needs no quote.
    False only when a positive value lacks a grounding quote."""
    if not isinstance(item, dict):
        return True
    val = item.get("value")
    if is_absence(val):
        return True
    if item.get("_quote_trimmed") is True:
        return True  # already rescued + flagged during refill
    quote = item.get("quote")
    if is_null_equivalent(quote):
        return False
    return validate_quote(quote, source_text, ocr_mode=True)[0]


# ----------------------------------------------------------------------------
# Patch
# ----------------------------------------------------------------------------
def patch(commit=False):
    if not MERGED.exists():
        print("ERROR: {} not found".format(MERGED), file=sys.stderr)
        return 2

    cands = collect_candidates()
    by_field = Counter()
    by_paper = Counter()
    skipped_grounded = 0
    flagged = 0
    papers_out = []

    with open(MERGED, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            paper = json.loads(line)
            stable = (paper.get("stable_name")
                      or paper.get("_meta", {}).get("stable_name", ""))
            passes = paper.get("passes", {})
            for (cstable, pl), fields in cands.items():
                if cstable != stable:
                    continue
                block = passes.get(pl, {})
                src = _source_text(stable, pl)
                for field in fields:
                    spec = S.SCHEMA.get(field)
                    if spec is None or not spec.requires_quote:
                        continue
                    if field not in block:
                        continue
                    entry = block[field]
                    items = entry if isinstance(entry, list) else [entry]
                    for it in items:
                        if not isinstance(it, dict):
                            continue
                        if _entry_grounds(it, src):
                            skipped_grounded += 1
                            continue
                        if it.get("_quote_unverified") is True:
                            continue
                        it["_quote_unverified"] = True
                        it["_quote_unverified_reason"] = (
                            "missing_quote" if is_null_equivalent(it.get("quote"))
                            else "quote_not_in_source")
                        flagged += 1
                        by_field["{}.{}".format(pl, field)] += 1
                        by_paper[stable] += 1
            papers_out.append(paper)

    print("Candidate (paper,pass) groups : {}".format(len(cands)))
    print("Cells flagged _quote_unverified: {}".format(flagged))
    print("Candidates already grounded (skipped): {}".format(skipped_grounded))
    print("Papers touched                 : {}".format(len(by_paper)))
    if by_field:
        print("\nFlagged by field:")
        for k, n in by_field.most_common():
            print("  {:40s} {}".format(k, n))

    if commit and flagged:
        shutil.copy2(MERGED, BACKUP)
        tmp = MERGED.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as out:
            for p in papers_out:
                out.write(json.dumps(p) + "\n")
        tmp.replace(MERGED)
        print("\nCOMMIT: wrote {} flags. Backup: {}".format(flagged, BACKUP))
        print("Next: python3 compute_derived.py && python3 extractions_to_xlsx.py")
    elif commit:
        print("\nNothing to flag; no write performed.")
    else:
        print("\nDry run. Re-run with --commit to write the flags.")
    return 0


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    src = "We trained the model in PennyLane on a public benchmark dataset."
    # positive value, grounding quote -> grounds (no flag)
    print("T1 grounded:", "PASS" if _entry_grounds(
        {"value": "simulator", "quote": "trained the model in PennyLane"}, src)
        else "FAIL")
    # positive value, null quote -> not grounded (flag)
    print("T2 missing quote:", "PASS" if not _entry_grounds(
        {"value": "simulator", "quote": None}, src) else "FAIL")
    # positive value, bad quote -> not grounded (flag)
    print("T3 bad quote:", "PASS" if not _entry_grounds(
        {"value": "real", "quote": "executed on IBM hardware"}, src) else "FAIL")
    # absence value, no quote -> grounds (no flag)
    print("T4 absence:", "PASS" if _entry_grounds(
        {"value": "not_reported", "quote": None}, src) else "FAIL")
    # already trimmed/rescued -> grounds (no flag)
    print("T5 trimmed:", "PASS" if _entry_grounds(
        {"value": "x", "quote": "zzz", "_quote_trimmed": True}, src) else "FAIL")


def main():
    ap = argparse.ArgumentParser(description="Flag unverified cells for spot-check.")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        _selftest()
        return 0
    return patch(commit=args.commit)


if __name__ == "__main__":
    raise SystemExit(main())
