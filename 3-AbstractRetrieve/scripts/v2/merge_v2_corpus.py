#!/usr/bin/env python3
"""
merge_v2_corpus.py

Build the v2 canonical corpus from three sources:
  1. v1 merged dataset (merged_dataset_OA_only_v1.json): for DOIs in the v2
     corpus that also have a valid abstract from the v1 pass, reuse the v1
     record verbatim.
  2. v2 recovery output (recovered_abstracts_v2.jsonl): for DOIs newly
     fetched, build a fresh record with the new abstract.
  3. v2 corpus authoritative list (DOIs-RemovedDuplicates.xlsx): defines
     which DOIs belong to the final v2 corpus.

Output schema (matches v1 merged_dataset.json so downstream tools work
unchanged):

    {
      "id":            "<best-effort record id, prefixed v2_ for new records>",
      "DOI":           "<lowercase DOI>",
      "title":         "<title>",
      "abstract":      "<validated abstract>",
      "_abstract_provenance":  "v1_reused" | "v2_crossref" | "v2_openalex" |
                               "v2_europepmc" | "v2_failed",
      "_corpus":       "v2",
      "_v1_record_id": "<if reused, the original v1 id>",
    }

Records with no recoverable abstract (v1 failed AND v2 failed) are still
included in the output with abstract = "" and _abstract_provenance = "v2_failed".
These are the records that go into title_only_records_v2.json.

Usage:
    python3 merge_v2_corpus.py

Inputs (all paths are relative to the script's working directory):
    inputs/DOIs-RemovedDuplicates.xlsx
    v1_archive/merged_dataset_OA_only_v1.json
    recovered_abstracts_v2.jsonl

Outputs:
    v2_active/merged_dataset.json
    v2_active/title_only_records_v2.json
    v2_active/merge_v2_report.txt

The script is idempotent: running it twice produces the same output (it
overwrites). It is also safe to run while the fetch is in progress — it
will just see fewer v2-recovered abstracts and report more "v2_failed"
records. For the final canonical merge, run it AFTER the fetch finishes.
"""

import json
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl required: pip3 install --user openpyxl")

# ----- paths -----
CORPUS_XLSX = Path("inputs/DOIs-RemovedDuplicates.xlsx")
V1_MERGED   = Path("v1_archive/merged_dataset_OA_only_v1.json")
V2_RECOVERY = Path("v2_active/recovered_abstracts_v2.jsonl")

OUT_DIR     = Path("v2_active")
OUT_CORPUS  = OUT_DIR / "merged_dataset.json"
OUT_TITLE   = OUT_DIR / "title_only_records_v2.json"
OUT_REPORT  = OUT_DIR / "merge_v2_report.txt"

# ----- validation rules (same as v1 and v2 fetcher) -----
MIN_CHARS = 250
MIN_WORDS = 30

def is_valid_abstract(text):
    if not text:
        return False
    t = text.strip()
    return len(t) >= MIN_CHARS and len(t.split()) >= MIN_WORDS

def load_v2_corpus_dois():
    wb = openpyxl.load_workbook(CORPUS_XLSX, data_only=True)
    ws = wb["Sheet1"]
    dois = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            dois.add(row[0].strip().lower())
    return dois

def load_v1_records():
    """Return dict: doi_lower -> v1 record (full dict)."""
    if not V1_MERGED.exists():
        return {}
    out = {}
    data = json.load(V1_MERGED.open())
    for r in data:
        doi = (r.get("DOI") or r.get("doi") or "").strip().lower()
        if doi:
            out[doi] = r
    return out

def load_v2_recovery():
    """Return dict: doi_lower -> recovery record (from recover_abstracts_v2.jsonl)."""
    if not V2_RECOVERY.exists():
        return {}
    out = {}
    with V2_RECOVERY.open() as f:
        for line in f:
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            doi = (r.get("doi") or "").strip().lower()
            if doi:
                out[doi] = r
    return out

def main():
    OUT_DIR.mkdir(exist_ok=True)

    print("Loading inputs...")
    corpus_dois = load_v2_corpus_dois()
    v1_records  = load_v1_records()
    v2_recovery = load_v2_recovery()

    print(f"  v2 corpus DOIs:        {len(corpus_dois)}")
    print(f"  v1 records loaded:     {len(v1_records)}")
    print(f"  v2 recovery records:   {len(v2_recovery)}")

    out_records = []
    title_only  = []

    provenance_counts = {
        "v1_reused": 0,
        "v2_crossref": 0,
        "v2_openalex": 0,
        "v2_europepmc": 0,
        "v2_failed": 0,
    }

    for doi in sorted(corpus_dois):
        v1 = v1_records.get(doi)
        v2 = v2_recovery.get(doi)

        chosen_record = None
        provenance = None

        # Priority 1: reuse v1 if its abstract is valid
        if v1 and is_valid_abstract(v1.get("abstract")):
            chosen_record = {
                "id":             v1.get("id") or v1.get("record_id") or f"v2_{doi}",
                "DOI":            doi,
                "title":          v1.get("title", ""),
                "abstract":       v1.get("abstract", "").strip(),
                "_abstract_provenance": "v1_reused",
                "_corpus":        "v2",
                "_v1_record_id":  v1.get("id") or v1.get("record_id"),
            }
            provenance = "v1_reused"

        # Priority 2: fresh v2 fetch
        elif v2 and v2.get("source") and is_valid_abstract(v2.get("abstract")):
            src = v2["source"]
            chosen_record = {
                "id":             f"v2_{doi}",
                "DOI":            doi,
                "title":          v2.get("title") or (v1.get("title", "") if v1 else ""),
                "abstract":       v2["abstract"].strip(),
                "_abstract_provenance": f"v2_{src}",
                "_corpus":        "v2",
                "_v1_record_id":  v1.get("id") if v1 else None,
            }
            provenance = f"v2_{src}"

        # Priority 3: failed in both
        else:
            chosen_record = {
                "id":             f"v2_{doi}",
                "DOI":            doi,
                "title":          (v1.get("title", "") if v1 else "") or (v2.get("title", "") if v2 else ""),
                "abstract":       "",
                "_abstract_provenance": "v2_failed",
                "_corpus":        "v2",
                "_v1_record_id":  v1.get("id") if v1 else None,
            }
            provenance = "v2_failed"
            title_only.append({
                "DOI": doi,
                "title": chosen_record["title"],
                "v1_attempt": v1 is not None,
                "v2_attempt": v2 is not None,
                "v2_validation_status": v2.get("validation_status") if v2 else None,
                "v2_attempts": v2.get("attempts") if v2 else None,
            })

        out_records.append(chosen_record)
        provenance_counts[provenance] += 1

    # Write outputs
    OUT_CORPUS.write_text(json.dumps(out_records, ensure_ascii=False, indent=2))
    OUT_TITLE.write_text(json.dumps(title_only, ensure_ascii=False, indent=2))

    # Report
    report_lines = [
        "merge_v2_corpus.py report",
        "=" * 60,
        f"v2 corpus DOIs:           {len(corpus_dois)}",
        f"v1 records available:     {len(v1_records)}",
        f"v2 recovery records:      {len(v2_recovery)}",
        "",
        "Output: merged_dataset.json",
        f"  Total records written:  {len(out_records)}",
        "",
        "Provenance distribution:",
        f"  v1_reused:              {provenance_counts['v1_reused']:>6}",
        f"  v2_crossref:            {provenance_counts['v2_crossref']:>6}",
        f"  v2_openalex:            {provenance_counts['v2_openalex']:>6}",
        f"  v2_europepmc:           {provenance_counts['v2_europepmc']:>6}",
        f"  v2_failed:              {provenance_counts['v2_failed']:>6}",
        "",
        "Validation:",
        f"  Total with valid abstract:  {len(out_records) - provenance_counts['v2_failed']}",
        f"  Title-only (no abstract):   {provenance_counts['v2_failed']}",
        "",
        f"Files written:",
        f"  {OUT_CORPUS}",
        f"  {OUT_TITLE}",
        f"  {OUT_REPORT}",
    ]
    report = "\n".join(report_lines)
    OUT_REPORT.write_text(report + "\n")
    print()
    print(report)

if __name__ == "__main__":
    main()
