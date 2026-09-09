"""
paths.py
========

Shared path configuration for Step 4 (Full-Text Extraction) of the IEEE TRPMS
systematic review on Quantum Computing for Medical Imaging Applications.

Layout (sibling folders under PROJECT_ROOT)
-------------------------------------------
PROJECT_ROOT/                                       # /home/lpapp/IEEE_SYS_REV/
  1-Search/
  2-Metadata_Analysis/
  3-AbstractRetrieve/                               # Step 3 outputs (canonical)
    verdicts.jsonl
    merged_dataset.json
    rdf_pdf_index.csv
  3.5-IonisingPromote/                              # Step 3.5 outputs (canonical)
    verdicts_3_5.jsonl
  4-FullTextExtraction/                             # STEP_DIR (this folder)
    paths.py                                        # this file
    .claude/agents/extract-fulltext.md              # to be created
    build_step4_candidates.py
    step4_candidates.xlsx
    step4_candidates.jsonl
    step4_candidates_report.txt
    manual_pdfs/                                    # user-supplied PDFs
      <DOI-with-slash-replaced-by-underscore>.pdf
    (extraction scripts to follow)
  PDFs/

All paths are absolute, derived from this file's location.

Author : Laszlo Papp
Date   : 2026-05-11
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STEP_DIR = Path(__file__).resolve().parent
STEP3_DIR = PROJECT_ROOT / "3-AbstractRetrieve"
STEP3_5_DIR = PROJECT_ROOT / "3.5-IonisingPromote"

# Canonical inputs (immutable)
VERDICTS_PATH = STEP3_DIR / "verdicts.jsonl"
VERDICTS_3_5_PATH = STEP3_5_DIR / "verdicts_3_5.jsonl"
CORPUS_PATH = STEP3_DIR / "merged_dataset.json"
PDF_INDEX_PATH = STEP3_DIR / "rdf_pdf_index.csv"

# Manual PDF drop-in folder (for records not covered by rdf_pdf_index)
# Files are named <DOI with / replaced by _>.pdf
MANUAL_PDFS_DIR = STEP_DIR / "manual_pdfs"

# Step 4 candidate-set outputs (in STEP_DIR)
CANDIDATES_XLSX = STEP_DIR / "step4_candidates.xlsx"
CANDIDATES_JSONL = STEP_DIR / "step4_candidates.jsonl"
CANDIDATES_REPORT = STEP_DIR / "step4_candidates_report.txt"

# Reserved for next-turn artefacts (extraction phase)
PENDING_DIR = STEP_DIR / "pending_4"
PROCESSED_DIR = STEP_DIR / "processed_4"
EXTRACTIONS_PATH = STEP_DIR / "extractions_4.jsonl"
QUARANTINE_PATH = STEP_DIR / "quarantine_4.jsonl"
AUDIT_LOG_PATH = STEP_DIR / "audit_4.log"

# Full-text extraction working dir (cached pdftotext output per record)
FULLTEXT_CACHE_DIR = STEP_DIR / "fulltext_cache"
