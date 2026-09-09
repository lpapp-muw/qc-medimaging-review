"""
paths.py
========

Shared path configuration for Step 3.5 (Ionising Sub-Screen) of the IEEE TRPMS
systematic review on Quantum Computing for Medical Imaging Applications.

Layout (sibling folders under PROJECT_ROOT)
-------------------------------------------
PROJECT_ROOT/                                       # /home/lpapp/IEEE_SYS_REV/
  1-Search/
  2-Metadata_Analysis/
  3-AbstractRetrieve/                               # Step 3 outputs (canonical inputs to 3.5)
    verdicts.jsonl
    merged_dataset.json
    rdf_pdf_index.csv
  3.5-IonisingPromote/                              # STEP_DIR (this folder)
    paths.py                                        # this file
    .claude/agents/ionising-promote.md
    prepare_3_5_input.py
    batch_helper_3_5.py
    rescue_3_5.py
    report_3_5.py
    audit_3_5_sampling.py
    CLAUDE_3_5.md
    verdicts_3_5.jsonl                              # canonical Step 3.5 output
    stage_3_5_input.jsonl
    pending_3_5/
    processed_3_5/
    quarantine_3_5.jsonl
    audit_3_5.log
    audit_3_5.xlsx
    rescue_3_5_report.csv
    report_3_5.txt
    report_3_5.json
  4-FullTextExtraction/                             # downstream
  PDFs/

All paths are absolute, derived from this file's location.

Author : Laszlo Papp
Date   : 2026-05-11
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STEP_DIR = Path(__file__).resolve().parent
STEP3_DIR = PROJECT_ROOT / "3-AbstractRetrieve"

# Canonical inputs from Step 3 (immutable)
VERDICTS_PATH = STEP3_DIR / "verdicts.jsonl"
CORPUS_PATH = STEP3_DIR / "merged_dataset.json"
PDF_INDEX_PATH = STEP3_DIR / "rdf_pdf_index.csv"

# Canonical Stage 3.5 output (in STEP_DIR; this folder is the canonical home)
VERDICTS_3_5_PATH = STEP_DIR / "verdicts_3_5.jsonl"

# Stage 3.5 working files in STEP_DIR
INPUT_PATH = STEP_DIR / "stage_3_5_input.jsonl"
LOG_PATH = STEP_DIR / "prepare_3_5_input.log"
PENDING_DIR = STEP_DIR / "pending_3_5"
PROCESSED_DIR = STEP_DIR / "processed_3_5"
QUARANTINE_PATH = STEP_DIR / "quarantine_3_5.jsonl"
AUDIT_LOG_PATH = STEP_DIR / "audit_3_5.log"
RESCUE_REPORT_PATH = STEP_DIR / "rescue_3_5_report.csv"
REPORT_TXT = STEP_DIR / "report_3_5.txt"
REPORT_JSON = STEP_DIR / "report_3_5.json"
AUDIT_XLSX = STEP_DIR / "audit_3_5.xlsx"
