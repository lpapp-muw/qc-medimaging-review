#!/usr/bin/env python3
"""
prepare_3_5_v2_input.py
=======================

Read v2_active/verdicts.jsonl (Step 3 v2 final) and merged_dataset.json,
filter to records primary.category == "QUANTUM_SENSING_BIOMED", then
EXCLUDE any record whose DOI was already sub-screened in v1 (presence in
v1_archive/verdicts_3_5.jsonl or the legacy verdicts_3_5.jsonl).

Write the result to v2_active/stage_3_5_v2_input.jsonl, one record per
line. Schema per line matches what the ionising-promote subagent expects:

    {
      "record_id":              "v2_<doi>",
      "doi":                    "<lowercase doi>",
      "title":                  "<title>",
      "abstract":               "<abstract or empty>",
      "abstract_provenance":    "v1_reused | v2_crossref | v2_openalex | v2_europepmc | v2_failed",
      "step3_primary_category": "QUANTUM_SENSING_BIOMED",
      "step3_primary_quote":    "<from primary.evidence_quote>",
      "step3_confidence":       "high | low",
      "step3_reasoning":        "<from verdict.reasoning>"
    }

Usage:
    python3 scripts/v2/prepare_3_5_v2_input.py
"""

import json
import sys
from pathlib import Path

# Add the step folder root (where paths_v2.py lives) to sys.path
HERE = Path(__file__).resolve()
STEP_DIR = HERE.parent.parent.parent  # scripts/v2/ -> scripts/ -> STEP_DIR
sys.path.insert(0, str(STEP_DIR))
import paths_v2 as P  # noqa: E402


def safe_str(x):
    return (x or "").strip()


def load_jsonl(path):
    out = []
    if not Path(path).exists():
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main():
    P.V2_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: load v2 verdicts
    verdicts = load_jsonl(P.VERDICTS_PATH)
    print(f"v2 Step-3 verdicts loaded: {len(verdicts)} from {P.VERDICTS_PATH}")

    # Step 2: load v2 corpus (for title and abstract)
    corpus = json.load(P.CORPUS_PATH.open())
    corpus_by_doi = {}
    for r in corpus:
        doi = safe_str(r.get("DOI") or r.get("doi")).lower()
        if doi:
            corpus_by_doi[doi] = r
    print(f"v2 corpus records loaded: {len(corpus_by_doi)} from {P.CORPUS_PATH}")

    # Step 3: load v1 sub-screened DOIs (to exclude)
    v1_path = P.V1_VERDICTS_3_5_PATH
    if not v1_path.exists():
        # Fall back to legacy location if v1_archive hasn't been created yet
        if P.V1_VERDICTS_3_5_LEGACY_PATH.exists():
            v1_path = P.V1_VERDICTS_3_5_LEGACY_PATH
            print(f"NOTE: using legacy v1 verdicts at {v1_path}")
        else:
            print(f"WARNING: no v1 verdicts file found at {P.V1_VERDICTS_3_5_PATH} "
                  f"or {P.V1_VERDICTS_3_5_LEGACY_PATH}. All v2 QSB records will be screened.")
            v1_path = None

    v1_dois_already_screened = set()
    if v1_path is not None:
        v1_verdicts = load_jsonl(v1_path)
        for v in v1_verdicts:
            doi = safe_str(v.get("doi")).lower()
            if doi:
                v1_dois_already_screened.add(doi)
        print(f"v1 sub-screened DOIs (will be excluded): {len(v1_dois_already_screened)}")

    # Step 4: filter v2 verdicts to QSB
    qsb_records = []
    skipped_no_corpus = 0
    skipped_v1 = 0
    for v in verdicts:
        primary = v.get("primary") or {}
        if primary.get("category") != "QUANTUM_SENSING_BIOMED":
            continue
        doi = safe_str(v.get("doi")).lower()
        if not doi:
            continue
        if doi in v1_dois_already_screened:
            skipped_v1 += 1
            continue
        rec = corpus_by_doi.get(doi)
        if not rec:
            skipped_no_corpus += 1
            continue
        record_id = v.get("record_id") or f"v2_{doi}"
        out_obj = {
            "record_id":              record_id,
            "doi":                    doi,
            "title":                  safe_str(rec.get("title")),
            "abstract":               safe_str(rec.get("abstract")),
            "abstract_provenance":    rec.get("_abstract_provenance") or "unknown",
            "step3_primary_category": "QUANTUM_SENSING_BIOMED",
            "step3_primary_quote":    safe_str(primary.get("evidence_quote")),
            "step3_confidence":       v.get("confidence") or "low",
            "step3_reasoning":        safe_str(v.get("reasoning")),
        }
        qsb_records.append(out_obj)

    # Step 5: write output
    with P.INPUT_PATH.open("w", encoding="utf-8") as f:
        for r in qsb_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # Step 6: summarize
    print()
    print(f"v2 QSB candidates (excluding v1-screened): {len(qsb_records)}")
    print(f"  skipped (already screened in v1):  {skipped_v1}")
    print(f"  skipped (no corpus match):         {skipped_no_corpus}")
    print()
    print(f"Wrote: {P.INPUT_PATH}")
    print(f"  records: {len(qsb_records)}")
    print(f"  estimated batches (5/batch): {(len(qsb_records) + 4) // 5}")

    # Also dump short log
    with P.LOG_PATH.open("w", encoding="utf-8") as f:
        f.write(f"prepare_3_5_v2_input.py: v2 QSB records prepared = {len(qsb_records)}\n")
        f.write(f"  skipped already-v1-screened: {skipped_v1}\n")
        f.write(f"  skipped no-corpus-match:     {skipped_no_corpus}\n")
        f.write(f"  output:                      {P.INPUT_PATH}\n")


if __name__ == "__main__":
    main()
