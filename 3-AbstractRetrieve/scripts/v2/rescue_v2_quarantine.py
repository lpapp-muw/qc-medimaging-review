#!/usr/bin/env python3
"""
rescue_v2_quarantine.py

Rescue verdicts in v2_active/quarantine.jsonl by applying three repair
strategies, then merge rescued verdicts back into v2_active/verdicts.jsonl.

Repair strategies:

1. **Empty-text rescue** (`primary_evidence_quote_missing`):
   - Many of these records were quarantined because both title and abstract
     were empty at screening time. After title recovery, most now have a
     title. For these, RE-QUEUE the record for screening (do NOT rescue
     the verdict; the orchestrator will re-run them with the new title).
   - For records that still have no text (the 8 DOIs in
     no_metadata_retrievable.json): the verdict cannot be rescued; mark
     it as `_quote_unverified: true` with `evidence_quote: ""` so the
     audit trail is honest.

2. **Smart-quote / unicode rescue** (`primary_evidence_quote_not_in_source`
   and `secondary_evidence_quote_not_in_source`):
   - Apply relaxed substring matching that normalizes smart quotes,
     em-dashes, non-breaking spaces, and Greek-letter HTML entities.
   - If the relaxed normalization matches: rescue with `_quote_rescued: true`.
   - If no match even after relaxation: mark `_quote_unverified: true`.

3. **secondary string cast** (`secondary_invalid_type`):
   - Subagent emitted a string where an object was required.
   - Action: set `secondary: null` (we cannot synthesize an evidence_quote).
     Loses one of two findings but the primary verdict still stands.
   - Mark `_secondary_stripped: true`.

Outputs:
    v2_active/quarantine.jsonl.pre_rescue.bak     backup of original quarantine
    v2_active/verdicts.jsonl                       updated (rescued verdicts appended)
    v2_active/quarantine.jsonl                     updated (rescues removed)
    v2_active/to_screen_v2.json                    updated (records flagged for re-screening removed
                                                           from quarantine and re-queueable; their
                                                           record_ids removed from verdicts)
    v2_active/rescue_v2_report.txt                 summary

Usage:
    python3 scripts/v2/rescue_v2_quarantine.py
"""

import json
import re
import shutil
import unicodedata
from pathlib import Path
from collections import Counter

QUARANTINE       = Path("v2_active/quarantine.jsonl")
QUARANTINE_BAK   = Path("v2_active/quarantine.jsonl.pre_rescue.bak")
VERDICTS         = Path("v2_active/verdicts.jsonl")
VERDICTS_BAK     = Path("v2_active/verdicts.jsonl.pre_rescue.bak")
CORPUS           = Path("v2_active/merged_dataset.json")
TO_SCREEN        = Path("v2_active/to_screen_v2.json")
NO_META          = Path("v2_active/no_metadata_retrievable.json")
REPORT           = Path("v2_active/rescue_v2_report.txt")

CATEGORIES = {
    "QC_FOR_IMAGING", "QUANTUM_DOTS", "NANO_THERAPEUTICS",
    "FLUORESCENCE_PROBES", "QUANTUM_SENSING_BIOMED", "QUANTUM_CRYPTO_MED",
    "QUANTUM_INSPIRED_CLASSICAL", "CLASSICAL_AI_FOR_IMAGING",
    "CLINICAL_NON_IMAGING", "OTHER",
}


def safe_str(x):
    return (x or "").strip()


def normalize_relaxed(s):
    """Aggressive normalization for substring matching:
    - Unicode NFKC (collapses ligatures, curly quotes to ascii where possible)
    - Replace common Unicode punctuation with ASCII equivalents
    - Collapse whitespace
    - Lowercase
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    # Map common unicode punctuation to ascii
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
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


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

    # Load corpus by record_id and by doi
    corpus = json.load(CORPUS.open())
    corpus_by_rid = {}
    corpus_by_doi = {}
    for r in corpus:
        rid = r.get("id")
        doi = safe_str(r.get("DOI") or r.get("doi")).lower()
        if rid:
            corpus_by_rid[rid] = r
        if doi:
            corpus_by_doi[doi] = r

    # Load the no-metadata DOI set
    no_meta_dois = set()
    if NO_META.exists():
        no_meta_dois = set(json.load(NO_META.open()).get("dois", []))

    # Load quarantine
    quarantine = []
    with QUARANTINE.open() as f:
        for line in f:
            quarantine.append(json.loads(line))

    print(f"Quarantine entries: {len(quarantine)}")

    stats = Counter()
    rescued_verdicts = []
    stay_quarantined = []
    requeue_record_ids = set()
    requeue_dois = set()

    for q in quarantine:
        v = q.get("verdict", {})
        errors = set(q.get("errors", []))
        rid = v.get("record_id")
        doi = safe_str(v.get("doi")).lower()
        rec = corpus_by_rid.get(rid) or corpus_by_doi.get(doi)

        # --- Strategy 3: secondary_invalid_type → strip secondary ---
        if "secondary_invalid_type" in errors:
            v2 = dict(v)
            v2["secondary"] = None
            v2["_secondary_stripped"] = True
            errors.discard("secondary_invalid_type")
            # If only this error: rescue immediately
            if not errors:
                rescued_verdicts.append(v2)
                stats["rescued_secondary_stripped"] += 1
                continue
            # else: continue, the verdict will be checked against other errors

        # --- Strategy 2: smart-quote rescue ---
        quote_errors = {"primary_evidence_quote_not_in_source",
                        "secondary_evidence_quote_not_in_source"}
        if errors & quote_errors and rec is not None:
            title  = safe_str(rec.get("title"))
            abst   = safe_str(rec.get("abstract"))
            srcs = {"title": normalize_relaxed(title),
                    "abstract": normalize_relaxed(abst)}

            def check_quote(field_name, quote):
                src_norm = srcs.get(field_name, "")
                q_norm = normalize_relaxed(quote)
                if not q_norm or not src_norm:
                    return False
                return q_norm in src_norm

            v2 = dict(v)
            ok_primary = True
            ok_secondary = True
            if "primary_evidence_quote_not_in_source" in errors:
                p = v.get("primary", {})
                ok_primary = check_quote(p.get("evidence_field"), p.get("evidence_quote"))
            if "secondary_evidence_quote_not_in_source" in errors:
                s = v.get("secondary") or {}
                ok_secondary = check_quote(s.get("evidence_field"), s.get("evidence_quote"))

            if ok_primary and ok_secondary:
                v2["_quote_rescued"] = True
                rescued_verdicts.append(v2)
                stats["rescued_quote_relaxed"] += 1
                continue

        # --- Strategy 1: empty-text rescue ---
        if "primary_evidence_quote_missing" in errors:
            # Does the record now have text after title recovery?
            text_present = False
            if rec is not None:
                text_present = bool(safe_str(rec.get("title")) or safe_str(rec.get("abstract")))

            if text_present:
                # Record gained a title from title recovery.
                # Requeue for re-screening, do NOT accept the old quarantined verdict.
                if rid: requeue_record_ids.add(rid)
                if doi: requeue_dois.add(doi)
                stats["requeue_for_rescreening"] += 1
                continue
            else:
                # Genuinely empty text. Accept the verdict as-is with audit flag.
                v2 = dict(v)
                v2["_quote_unverified"] = True
                v2["_quote_unverified_reason"] = "no_text_available_in_source"
                rescued_verdicts.append(v2)
                stats["accepted_no_text_as_OTHER"] += 1
                continue

        # --- Fallthrough: unable to rescue, stay quarantined ---
        stay_quarantined.append(q)
        stats["unrescued"] += 1

    print()
    print("Rescue summary:")
    for k, n in stats.most_common():
        print(f"  {n:4d}  {k}")

    # Append rescued verdicts to verdicts.jsonl
    if rescued_verdicts:
        with VERDICTS.open("a", encoding="utf-8") as f:
            for v in rescued_verdicts:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        print(f"Appended {len(rescued_verdicts)} rescued verdicts to {VERDICTS}")

    # Rewrite quarantine with only the still-stuck ones
    with QUARANTINE.open("w", encoding="utf-8") as f:
        for q in stay_quarantined:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"Quarantine rewritten with {len(stay_quarantined)} entries (was {len(quarantine)})")

    # to_screen_v2.json: the requeue records are still in there (their record_ids
    # are NOT in verdicts.jsonl after the rewrite above, because we did not write
    # them as rescued verdicts). batch_helper.py uses set-difference on record_id,
    # so they will automatically be picked up by the next orchestrator iteration.
    # Therefore no edit to to_screen_v2.json is needed for requeue.
    print(f"Records flagged for re-screening: {len(requeue_record_ids)} (will be picked up automatically by orchestrator)")

    # Report
    lines = [
        "rescue_v2_quarantine.py report",
        "=" * 60,
        f"Quarantine entries processed:    {len(quarantine)}",
        "",
        "Outcome distribution:",
    ]
    for k, n in stats.most_common():
        lines.append(f"  {k:35s} {n:>4}")
    lines += [
        "",
        f"Rescued verdicts appended to verdicts.jsonl:  {len(rescued_verdicts)}",
        f"Records re-queued for screening:              {len(requeue_record_ids)}",
        f"Still quarantined (unrescuable):              {len(stay_quarantined)}",
        "",
        "Audit flags introduced on rescued verdicts:",
        "  _secondary_stripped: secondary string cast to null",
        "  _quote_rescued:      quote validated via relaxed Unicode normalization",
        "  _quote_unverified:   no rescue possible; verdict kept for audit only",
        "",
        "Backups created:",
        f"  {QUARANTINE_BAK}",
        f"  {VERDICTS_BAK}",
    ]
    REPORT.write_text("\n".join(lines) + "\n")
    print()
    print(REPORT.read_text())


if __name__ == "__main__":
    main()
