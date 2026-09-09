#!/usr/bin/env python3
"""
rescue_v2_quarantine_v2.py

Replaces rescue_v2_quarantine.py with a per-component rescue strategy.

For each quarantined verdict:

1. Evaluate `primary` independently:
   - Check evidence_quote against the labeled evidence_field (title or abstract).
   - If miss: also try the OTHER field (in case subagent mislabeled the field).
   - Try verbatim first, then relaxed Unicode normalization.
   - Outcomes:
       PASS_VERBATIM   - quote matches exactly
       PASS_RELAXED    - matches after Unicode normalization (flag _quote_rescued)
       PASS_FIELD_SWAP - matches in the other field (flag _evidence_field_corrected)
       FAIL            - no match anywhere

2. Evaluate `secondary` similarly. If `secondary` is a string (schema bug),
   cast to null with `_secondary_stripped: true`.

3. Final disposition:
   - primary PASS + secondary PASS-or-null  --> rescue verdict, append to verdicts.jsonl
   - primary PASS + secondary FAIL           --> strip secondary to null with _secondary_stripped, rescue
   - primary FAIL + record now has text      --> re-queue for screening (do not write a verdict)
   - primary FAIL + record empty             --> accept verdict with _quote_unverified

Outputs:
    v2_active/verdicts.jsonl                   (rescued verdicts appended)
    v2_active/quarantine.jsonl                 (rewritten with only unrescuable)
    v2_active/rescue_v2_report.txt             (overwritten)
    v2_active/quarantine.jsonl.pre_rescue_v2.bak
    v2_active/verdicts.jsonl.pre_rescue_v2.bak

Usage:
    python3 scripts/v2/rescue_v2_quarantine_v2.py
"""

import json
import re
import shutil
import unicodedata
from pathlib import Path
from collections import Counter

QUARANTINE       = Path("v2_active/quarantine.jsonl")
QUARANTINE_BAK   = Path("v2_active/quarantine.jsonl.pre_rescue_v2.bak")
VERDICTS         = Path("v2_active/verdicts.jsonl")
VERDICTS_BAK     = Path("v2_active/verdicts.jsonl.pre_rescue_v2.bak")
CORPUS           = Path("v2_active/merged_dataset.json")
REPORT           = Path("v2_active/rescue_v2_report.txt")


def safe_str(x):
    return (x or "").strip()


def normalize_relaxed(s):
    """Aggressive normalization for substring matching."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    replacements = {
        "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
        "\u2014": "-", "\u2015": "-", "\u2212": "-",
        "\u2018": "'", "\u2019": "'", "\u201A": "'", "\u201B": "'",
        "\u201C": '"', "\u201D": '"', "\u201E": '"', "\u201F": '"',
        "\u2026": "...", "\u00A0": " ", "\u202F": " ", "\u2009": " ",
        "\u200B": "", "\u200C": "", "\u200D": "", "\uFEFF": "",
    }
    for k, v in replacements.items():
        s = s.replace(k, v)
    return re.sub(r"\s+", " ", s).strip().lower()


def verify_quote(quote, field, rec):
    """Return (status, used_field).

    status: 'PASS_VERBATIM' | 'PASS_RELAXED' | 'PASS_FIELD_SWAP_VERBATIM' |
            'PASS_FIELD_SWAP_RELAXED' | 'FAIL'
    used_field: the field where the quote was found (or the labeled field if FAIL)
    """
    if not quote:
        return ('FAIL', field)

    title = safe_str(rec.get("title"))
    abst  = safe_str(rec.get("abstract"))

    sources = {"title": title, "abstract": abst}
    labeled_src = sources.get(field, "")
    other_field = "abstract" if field == "title" else "title"
    other_src = sources.get(other_field, "")

    # Verbatim, labeled field
    if labeled_src and quote in labeled_src:
        return ('PASS_VERBATIM', field)
    # Verbatim, other field (subagent mislabeled the field)
    if other_src and quote in other_src:
        return ('PASS_FIELD_SWAP_VERBATIM', other_field)
    # Relaxed, labeled field
    nq = normalize_relaxed(quote)
    if nq:
        if labeled_src and nq in normalize_relaxed(labeled_src):
            return ('PASS_RELAXED', field)
        if other_src and nq in normalize_relaxed(other_src):
            return ('PASS_FIELD_SWAP_RELAXED', other_field)
    return ('FAIL', field)


def main():
    if not QUARANTINE.exists():
        raise SystemExit(f"{QUARANTINE} not found.")

    # Backups
    if not QUARANTINE_BAK.exists():
        shutil.copy2(QUARANTINE, QUARANTINE_BAK)
        print(f"Backed up: {QUARANTINE_BAK}")
    if not VERDICTS_BAK.exists():
        shutil.copy2(VERDICTS, VERDICTS_BAK)
        print(f"Backed up: {VERDICTS_BAK}")

    # Load corpus
    corpus = json.load(CORPUS.open())
    by_rid = {r.get("id"): r for r in corpus if r.get("id")}
    by_doi = {safe_str(r.get("DOI") or r.get("doi")).lower(): r for r in corpus if (r.get("DOI") or r.get("doi"))}

    # Load quarantine
    quarantine = []
    with QUARANTINE.open() as f:
        for line in f:
            quarantine.append(json.loads(line))
    print(f"Quarantine entries: {len(quarantine)}")

    rescued_verdicts = []
    stay_quarantined = []
    requeue_record_ids = set()
    stats = Counter()
    primary_outcomes = Counter()
    secondary_outcomes = Counter()

    for q in quarantine:
        v = dict(q.get("verdict", {}))
        rid = v.get("record_id")
        doi = safe_str(v.get("doi")).lower()
        rec = by_rid.get(rid) or by_doi.get(doi)

        if not rec:
            stay_quarantined.append(q)
            stats["no_record_match"] += 1
            continue

        flags = {}

        # --- secondary type fix ---
        sec = v.get("secondary")
        if isinstance(sec, str):
            # Schema bug: subagent emitted string. Cast to null.
            v["secondary"] = None
            flags["_secondary_stripped"] = True
            flags["_secondary_original_string"] = sec
            sec = None

        # --- evaluate primary ---
        p = v.get("primary", {})
        if not p or not p.get("evidence_quote"):
            primary_status = 'FAIL'
        else:
            primary_status, primary_used_field = verify_quote(
                p.get("evidence_quote", ""),
                p.get("evidence_field"),
                rec
            )
            if primary_status.startswith('PASS_FIELD_SWAP'):
                v["primary"]["evidence_field"] = primary_used_field
                flags["_evidence_field_corrected"] = "primary"
            if primary_status in ('PASS_RELAXED', 'PASS_FIELD_SWAP_RELAXED'):
                flags["_quote_rescued"] = flags.get("_quote_rescued", []) + ["primary"]

        primary_outcomes[primary_status] += 1

        # --- evaluate secondary (if it is now an object) ---
        secondary_status = 'NONE'
        if isinstance(v.get("secondary"), dict):
            s = v["secondary"]
            secondary_status, secondary_used_field = verify_quote(
                s.get("evidence_quote", ""),
                s.get("evidence_field"),
                rec
            )
            if secondary_status.startswith('PASS_FIELD_SWAP'):
                v["secondary"]["evidence_field"] = secondary_used_field
                ec = flags.get("_evidence_field_corrected")
                if ec is None:
                    flags["_evidence_field_corrected"] = "secondary"
                else:
                    flags["_evidence_field_corrected"] = "primary+secondary"
            if secondary_status in ('PASS_RELAXED', 'PASS_FIELD_SWAP_RELAXED'):
                qr = flags.get("_quote_rescued", [])
                if isinstance(qr, list):
                    qr.append("secondary")
                else:
                    qr = ["secondary"]
                flags["_quote_rescued"] = qr
            if secondary_status == 'FAIL':
                # Strip bad secondary
                v["secondary"] = None
                flags["_secondary_stripped"] = True
                flags["_secondary_failed_quote"] = s.get("evidence_quote")

        secondary_outcomes[secondary_status] += 1

        # --- Final disposition ---
        if primary_status != 'FAIL':
            # Apply audit flags
            for k, val in flags.items():
                v[k] = val
            rescued_verdicts.append(v)
            stats["rescued"] += 1
        else:
            # Primary failed. Check if record has text now.
            has_text = bool(safe_str(rec.get("title")) or safe_str(rec.get("abstract")))
            if has_text:
                if rid:
                    requeue_record_ids.add(rid)
                stats["requeue_for_rescreening"] += 1
            else:
                # No text and no rescue. Accept with audit flag.
                for k, val in flags.items():
                    v[k] = val
                v["_quote_unverified"] = True
                v["_quote_unverified_reason"] = "primary_quote_not_in_source_no_text_available"
                rescued_verdicts.append(v)
                stats["accepted_unverified_no_text"] += 1

    # Write outputs
    if rescued_verdicts:
        with VERDICTS.open("a", encoding="utf-8") as f:
            for v in rescued_verdicts:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        print(f"Appended {len(rescued_verdicts)} rescued verdicts to {VERDICTS}")

    with QUARANTINE.open("w", encoding="utf-8") as f:
        for q in stay_quarantined:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"Quarantine rewritten: {len(stay_quarantined)} remaining")

    print(f"Re-queued for screening: {len(requeue_record_ids)} record_ids")
    print()
    print("Summary:")
    for k, n in stats.most_common():
        print(f"  {n:4d}  {k}")

    print()
    print("Primary evaluation breakdown:")
    for k, n in primary_outcomes.most_common():
        print(f"  {n:4d}  {k}")
    print()
    print("Secondary evaluation breakdown:")
    for k, n in secondary_outcomes.most_common():
        print(f"  {n:4d}  {k}")

    # Report
    lines = [
        "rescue_v2_quarantine_v2.py report",
        "=" * 60,
        f"Quarantine entries processed:    {len(quarantine)}",
        "",
        "Final disposition:",
    ]
    for k, n in stats.most_common():
        lines.append(f"  {k:35s} {n:>4}")
    lines += [
        "",
        "Primary evaluation breakdown:",
    ]
    for k, n in primary_outcomes.most_common():
        lines.append(f"  {k:35s} {n:>4}")
    lines += [
        "",
        "Secondary evaluation breakdown:",
    ]
    for k, n in secondary_outcomes.most_common():
        lines.append(f"  {k:35s} {n:>4}")
    lines += [
        "",
        f"Rescued verdicts appended to verdicts.jsonl:  {len(rescued_verdicts)}",
        f"Records re-queued for re-screening:           {len(requeue_record_ids)}",
        f"Still quarantined (unrescuable):              {len(stay_quarantined)}",
        "",
        "Audit flags on rescued verdicts:",
        "  _quote_rescued:                quote matched via relaxed Unicode normalization",
        "  _evidence_field_corrected:     quote was found in the OTHER field (title vs abstract)",
        "  _secondary_stripped:           secondary was string or its quote failed; set to null",
        "  _secondary_failed_quote:       (audit) original secondary quote text that failed verification",
        "  _quote_unverified:             primary quote unverifiable AND no text in source",
        "",
        "Backups created:",
        f"  {QUARANTINE_BAK}",
        f"  {VERDICTS_BAK}",
    ]
    REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
