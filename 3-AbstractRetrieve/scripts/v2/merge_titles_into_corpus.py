#!/usr/bin/env python3
"""
merge_titles_into_corpus.py

After recover_titles_v2.py runs, update v2_active/merged_dataset.json and
v2_active/to_screen_v2.json in place so that records with recovered titles
get their title populated. Records that still have no title remain
as-is and are flagged as PRISMA "no metadata retrievable".

Outputs:
    v2_active/merged_dataset.json          updated in place (backup made first)
    v2_active/to_screen_v2.json            updated in place (backup made first)
    v2_active/no_metadata_retrievable.json list of DOIs with no title from any source

Backup files (created before write):
    v2_active/merged_dataset.json.pre_title_recovery.bak
    v2_active/to_screen_v2.json.pre_title_recovery.bak

Usage:
    python3 scripts/v2/merge_titles_into_corpus.py
"""

import json
import shutil
from pathlib import Path
from collections import Counter

CORPUS_PATH    = Path("v2_active/merged_dataset.json")
TO_SCREEN_PATH = Path("v2_active/to_screen_v2.json")
TITLES_PATH    = Path("v2_active/recovered_titles.jsonl")
NO_META_PATH   = Path("v2_active/no_metadata_retrievable.json")

CORPUS_BAK     = Path("v2_active/merged_dataset.json.pre_title_recovery.bak")
TO_SCREEN_BAK  = Path("v2_active/to_screen_v2.json.pre_title_recovery.bak")


def safe_str(x):
    return (x or "").strip()


def main():
    if not TITLES_PATH.exists():
        raise SystemExit(f"Run recover_titles_v2.py first; {TITLES_PATH} not found.")

    # Index recovered titles by DOI
    recovered = {}
    with TITLES_PATH.open() as f:
        for line in f:
            r = json.loads(line)
            doi = safe_str(r.get("doi")).lower()
            if doi:
                recovered[doi] = r
    print(f"Recovered title entries:          {len(recovered)}")

    titles_filled = sum(1 for r in recovered.values() if safe_str(r.get("title")))
    titles_empty  = len(recovered) - titles_filled
    print(f"  With title:                     {titles_filled}")
    print(f"  Still without title:            {titles_empty}")

    # Source distribution among recovered
    src_dist = Counter(r.get("source") for r in recovered.values())
    print("  By source:")
    for s, n in src_dist.most_common():
        print(f"    {s:12s} {n}")

    # Backup originals
    if not CORPUS_BAK.exists():
        shutil.copy2(CORPUS_PATH, CORPUS_BAK)
        print(f"Backed up: {CORPUS_BAK}")
    if not TO_SCREEN_BAK.exists():
        shutil.copy2(TO_SCREEN_PATH, TO_SCREEN_BAK)
        print(f"Backed up: {TO_SCREEN_BAK}")

    # Update merged_dataset.json
    corpus = json.load(CORPUS_PATH.open())
    corpus_updates = 0
    no_meta_dois = []
    for rec in corpus:
        if safe_str(rec.get("title")) or safe_str(rec.get("abstract")):
            continue
        doi = safe_str(rec.get("DOI") or rec.get("doi")).lower()
        if not doi:
            continue
        r = recovered.get(doi)
        if r and safe_str(r.get("title")):
            rec["title"] = r["title"]
            rec["_title_provenance"] = f"v2_{r['source']}_title_only"
            corpus_updates += 1
        else:
            no_meta_dois.append(doi)

    CORPUS_PATH.write_text(json.dumps(corpus, ensure_ascii=False, indent=2))
    print(f"Updated merged_dataset.json:     {corpus_updates} records gained a title")

    # Update to_screen_v2.json
    to_screen = json.load(TO_SCREEN_PATH.open())
    to_screen_updates = 0
    to_screen_no_meta = []
    for rec in to_screen:
        if safe_str(rec.get("title")) or safe_str(rec.get("abstract")):
            continue
        doi = safe_str(rec.get("DOI") or rec.get("doi")).lower()
        if not doi:
            continue
        r = recovered.get(doi)
        if r and safe_str(r.get("title")):
            rec["title"] = r["title"]
            rec["_title_provenance"] = f"v2_{r['source']}_title_only"
            to_screen_updates += 1
        else:
            to_screen_no_meta.append(doi)

    TO_SCREEN_PATH.write_text(json.dumps(to_screen, ensure_ascii=False, indent=2))
    print(f"Updated to_screen_v2.json:       {to_screen_updates} records gained a title")
    print(f"Still no-text in to_screen_v2:   {len(to_screen_no_meta)}")

    # Write no-metadata list
    NO_META_PATH.write_text(json.dumps({
        "description": "DOIs for which neither v1 nor v2 retrieved a title or abstract. PRISMA exclusion reason: 'no metadata retrievable from any source'.",
        "count": len(no_meta_dois),
        "dois": sorted(no_meta_dois),
    }, indent=2))
    print(f"Wrote no_metadata_retrievable.json with {len(no_meta_dois)} DOIs")

    print()
    print("NEXT STEP:")
    print(f"  These {len(to_screen_no_meta)} records will still quarantine if screened (empty title and abstract).")
    print(f"  Remove them from to_screen_v2.json before resuming the orchestrator, OR")
    print(f"  accept that they will quarantine cleanly with 'empty_text' label.")


if __name__ == "__main__":
    main()
