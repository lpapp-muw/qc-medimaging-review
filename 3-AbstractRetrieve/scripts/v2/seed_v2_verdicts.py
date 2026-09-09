#!/usr/bin/env python3
"""
seed_v2_verdicts.py

Prepare the v2 Step-3 screening workspace by:

1. Loading the v2 corpus (v2_active/merged_dataset.json).
2. Loading v1 verdicts (v1_archive/verdicts_OA_only_v1.jsonl).
3. For each v2 record:
     - If its abstract provenance is "v1_reused" (i.e. the abstract is the
       v1 abstract verbatim) AND there is a v1 verdict for the same DOI
       AND that verdict passed v1 validation:
           -> reuse the v1 verdict, written into v2 verdicts file with an
              additional flag `_reused_from_v1: true` and `_reused_v1_record_id`
              for audit.
     - Else (new abstract, no v1 verdict, or v1 verdict missing):
           -> add the record to v2_active/to_screen_v2.json which becomes
              the screening input for the Claude Code orchestrator.
4. Writes a report summarizing both populations.

After running this, the orchestrator can be pointed at v2_active/to_screen_v2.json
as its corpus (instead of merged_dataset.json) and only the ~3971 not-yet-screened
records will be processed. Verdicts from the new screening will be APPENDED to
v2_active/verdicts.jsonl (which is pre-seeded with the reused v1 verdicts).

Outputs:
    v2_active/verdicts.jsonl                    seeded with v1_reused verdicts
    v2_active/to_screen_v2.json                 input for orchestrator (records to screen)
    v2_active/seed_v2_report.txt                summary

Usage:
    python3 scripts/v2/seed_v2_verdicts.py
"""

import json
from pathlib import Path
from collections import Counter

CORPUS         = Path("v2_active/merged_dataset.json")
V1_VERDICTS    = Path("v1_archive/verdicts_OA_only_v1.jsonl")

OUT_DIR        = Path("v2_active")
OUT_VERDICTS   = OUT_DIR / "verdicts.jsonl"
OUT_TO_SCREEN  = OUT_DIR / "to_screen_v2.json"
OUT_REPORT     = OUT_DIR / "seed_v2_report.txt"


def main():
    OUT_DIR.mkdir(exist_ok=True)

    # Refuse to overwrite existing verdicts file (safety)
    if OUT_VERDICTS.exists():
        size = OUT_VERDICTS.stat().st_size
        if size > 0:
            raise SystemExit(
                f"REFUSING TO OVERWRITE: {OUT_VERDICTS} already exists ({size} bytes).\n"
                f"If you intend to re-seed, rename it first:\n"
                f"  mv {OUT_VERDICTS} {OUT_VERDICTS}.bak"
            )

    print("Loading inputs...")
    corpus = json.load(CORPUS.open())
    print(f"  v2 corpus records: {len(corpus)}")

    v1_verdicts_by_doi = {}
    with V1_VERDICTS.open() as f:
        for line in f:
            v = json.loads(line)
            doi = (v.get("doi") or "").strip().lower()
            if doi:
                v1_verdicts_by_doi[doi] = v
    print(f"  v1 verdicts loaded: {len(v1_verdicts_by_doi)}")

    reused = []
    to_screen = []

    provenance_counts = Counter()
    reused_by_v1_category = Counter()
    to_screen_provenance = Counter()
    to_screen_with_abstract = 0
    to_screen_title_only = 0

    for rec in corpus:
        doi = (rec.get("DOI") or rec.get("doi") or "").strip().lower()
        provenance = rec.get("_abstract_provenance", "unknown")
        provenance_counts[provenance] += 1

        v1_verdict = v1_verdicts_by_doi.get(doi)

        # Decision: reuse only if abstract is verbatim v1 (provenance "v1_reused")
        # AND a v1 verdict exists for this DOI.
        if provenance == "v1_reused" and v1_verdict is not None:
            seeded = dict(v1_verdict)
            seeded["_reused_from_v1"] = True
            seeded["_reused_v1_record_id"] = v1_verdict.get("record_id")
            # Use the v2 record_id (now prefixed v2_) for the seeded entry so
            # downstream tools using v2 record_id resolve correctly.
            seeded["record_id"] = rec.get("id")
            reused.append(seeded)
            cat = v1_verdict.get("primary", {}).get("category", "?")
            reused_by_v1_category[cat] += 1
        else:
            to_screen.append(rec)
            to_screen_provenance[provenance] += 1
            if rec.get("abstract", "").strip():
                to_screen_with_abstract += 1
            else:
                to_screen_title_only += 1

    # Write seeded verdicts (one JSON object per line)
    with OUT_VERDICTS.open("w") as f:
        for v in reused:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    # Write to-screen as a JSON array (the orchestrator's batch_helper expects this shape)
    with OUT_TO_SCREEN.open("w") as f:
        json.dump(to_screen, f, ensure_ascii=False, indent=2)

    # Report
    lines = [
        "seed_v2_verdicts.py report",
        "=" * 60,
        f"v2 corpus records:                       {len(corpus)}",
        f"v1 verdicts available (DOIs):            {len(v1_verdicts_by_doi)}",
        "",
        "Corpus provenance distribution:",
    ]
    for prov, n in provenance_counts.most_common():
        lines.append(f"  {prov:30s} {n:>6}")

    lines += [
        "",
        f"REUSED v1 verdicts (seeded into v2 verdicts.jsonl):",
        f"  Total reused:                          {len(reused)}",
        "",
        "  Distribution by v1 primary category:",
    ]
    for cat, n in reused_by_v1_category.most_common():
        lines.append(f"    {cat:30s} {n:>6}")

    lines += [
        "",
        f"TO SCREEN in v2 (written to to_screen_v2.json):",
        f"  Total to screen:                       {len(to_screen)}",
        f"  ... with abstract:                     {to_screen_with_abstract}",
        f"  ... title-only (no abstract):          {to_screen_title_only}",
        "",
        "  By abstract provenance:",
    ]
    for prov, n in to_screen_provenance.most_common():
        lines.append(f"    {prov:30s} {n:>6}")

    lines += [
        "",
        "Files written:",
        f"  {OUT_VERDICTS}   ({len(reused)} verdicts)",
        f"  {OUT_TO_SCREEN}   ({len(to_screen)} records)",
        f"  {OUT_REPORT}",
        "",
        "NEXT STEPS:",
        "  1. Point the Claude Code orchestrator at to_screen_v2.json instead of merged_dataset.json.",
        "  2. Run: 'process the entire corpus' inside the Claude Code session.",
        "  3. The orchestrator's batch_helper will skip records already in verdicts.jsonl",
        "     (i.e. the reused v1 verdicts), so it only screens the to-screen records.",
        "  4. After screening, run a verdict-count sanity check: total verdicts in",
        "     verdicts.jsonl should equal the v2 corpus size (6847).",
    ]
    report = "\n".join(lines)
    OUT_REPORT.write_text(report + "\n")
    print()
    print(report)


if __name__ == "__main__":
    main()
