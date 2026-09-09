"""
paths_v2.py
===========

Shared path configuration for Step 3.5 v2 (Ionising Sub-Screen, v2 corpus)
of the IEEE TRPMS systematic review on Quantum Computing for Medical Imaging
Applications.

The v1 file (paths.py) pointed at the v1 OA-only corpus at
3-AbstractRetrieve/verdicts.jsonl. This v2 file points at the v2 (full)
corpus at 3-AbstractRetrieve/v2_active/verdicts.jsonl.

v1 outputs are NOT overwritten. v2 outputs go to v2_active/ in this folder.

Author : Laszlo Papp
Date   : 2026-05-19
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STEP_DIR     = Path(__file__).resolve().parent
STEP3_DIR    = PROJECT_ROOT / "3-AbstractRetrieve"
STEP3_V2_DIR = STEP3_DIR / "v2_active"

# Canonical v2 inputs from Step 3 v2
VERDICTS_PATH  = STEP3_V2_DIR / "verdicts.jsonl"
CORPUS_PATH    = STEP3_V2_DIR / "merged_dataset.json"

# v1 reference (for excluding records already sub-screened in v1)
V1_VERDICTS_3_5_PATH = STEP_DIR / "v1_archive" / "verdicts_3_5.jsonl"
# Fallback in case v1_archive doesn't exist yet (pre-reorganization):
V1_VERDICTS_3_5_LEGACY_PATH = STEP_DIR / "verdicts_3_5.jsonl"

# v2 working files (in STEP_DIR/v2_active/)
V2_DIR              = STEP_DIR / "v2_active"
INPUT_PATH          = V2_DIR / "stage_3_5_v2_input.jsonl"
VERDICTS_3_5_PATH   = V2_DIR / "verdicts_3_5_v2.jsonl"
QUARANTINE_PATH     = V2_DIR / "quarantine_3_5_v2.jsonl"
PENDING_DIR         = V2_DIR / "pending_3_5_v2"
PROCESSED_DIR       = V2_DIR / "processed_3_5_v2"

# Reports / logs
LOG_PATH            = V2_DIR / "prepare_3_5_v2_input.log"
AUDIT_LOG_PATH      = V2_DIR / "audit_3_5_v2.log"
RESCUE_REPORT_PATH  = V2_DIR / "rescue_3_5_v2_report.csv"
REPORT_TXT          = V2_DIR / "report_3_5_v2.txt"
REPORT_JSON         = V2_DIR / "report_3_5_v2.json"
AUDIT_XLSX          = V2_DIR / "audit_3_5_v2.xlsx"
