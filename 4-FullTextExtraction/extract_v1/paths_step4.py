"""
paths_step4.py — Canonical path configuration for the Step-4 AI extraction
pipeline.

Single source of truth. All Step-4 scripts MUST import from this module rather
than hard-code paths. Mirrors the paths.py / paths_v2.py convention from the
Stage-3 and Stage-3.5 pipelines.

Python 3.8 compatible (no PEP-604/585 unions, no parametrised builtins,
no @dataclass with generic annotations).
"""

import os
from pathlib import Path


# ----------------------------------------------------------------------------
# Project root resolution
# ----------------------------------------------------------------------------
# This module lives at:
#   /home/lpapp/IEEE_SYS_REV/4-FullTextExtraction/extract_v1/paths_step4.py
#
# We resolve PROJECT_ROOT by walking up two parents from this file:
#   extract_v1/paths_step4.py -> extract_v1/ -> 4-FullTextExtraction/ -> IEEE_SYS_REV/
#
# An IEEE_SYS_REV_ROOT environment override is honoured for portability
# (e.g. running the same scripts on a different host or under tmpfs for
# smoke tests).

_HERE = Path(__file__).resolve().parent  # extract_v1/
_STEP4_DIR = _HERE.parent                # 4-FullTextExtraction/
_DEFAULT_ROOT = _STEP4_DIR.parent        # IEEE_SYS_REV/

PROJECT_ROOT = Path(os.environ.get("IEEE_SYS_REV_ROOT", str(_DEFAULT_ROOT))).resolve()


# ----------------------------------------------------------------------------
# Top-level project directories (read-mostly; produced by earlier stages)
# ----------------------------------------------------------------------------
SEARCH_DIR = PROJECT_ROOT / "1-Search"
METADATA_DIR = PROJECT_ROOT / "2-Metadata_Analysis"
STAGE3_DIR = PROJECT_ROOT / "3-AbstractRetrieve"
STAGE3_V2_DIR = STAGE3_DIR / "v2_active"
STAGE3_5_DIR = PROJECT_ROOT / "3.5-IonisingPromote"
STAGE3_5_V2_DIR = STAGE3_5_DIR / "v2_active"

# Canonical inputs that Step 4 consumes from earlier stages.
MERGED_DATASET = STAGE3_V2_DIR / "merged_dataset.json"
VERDICTS_STAGE3 = STAGE3_V2_DIR / "verdicts.jsonl"
VERDICTS_STAGE3_5_V1 = STAGE3_5_DIR / "v1_archive" / "verdicts_3_5.jsonl"
VERDICTS_STAGE3_5_V2 = STAGE3_5_V2_DIR / "verdicts_3_5_v2.jsonl"


# ----------------------------------------------------------------------------
# Step-4 top directory
# ----------------------------------------------------------------------------
STEP4_DIR = PROJECT_ROOT / "4-FullTextExtraction"

# Candidate workbook + PDF folders (produced by earlier Step-4 setup).
CANDIDATES_XLSX = STEP4_DIR / "step4_candidates_v2.xlsx"
PDFS_STEP4 = STEP4_DIR / "PDFs_step4"

# Newly-retrieved paywalled PDFs (manual retrieval by co-authors, raw
# publisher filenames). Consolidated into PDFS_STEP4 by
# consolidate_retrieved_pdfs.py via content-DOI matching.
PDFS_PAYWALL_SRC = STEP4_DIR / "PDFs_v2_PayWall"
CONSOLIDATION_UNMATCHED = STEP4_DIR / "extract_v1" / "consolidation_unmatched.csv"
CONSOLIDATION_LOG = STEP4_DIR / "extract_v1" / "consolidation_log.csv"


# ----------------------------------------------------------------------------
# Extraction pipeline working directory (this iteration: v1)
# ----------------------------------------------------------------------------
EXTRACT_DIR = STEP4_DIR / "extract_v1"

# Phase 1.1 — PDF-to-text outputs
PDF_TEXT_DIR = EXTRACT_DIR / "PDFs_step4_text"
PDF_TEXT_INDEX = EXTRACT_DIR / "pdf_text_index.csv"

# Section tagging + chunk plans
SECTION_TAGS_DIR = EXTRACT_DIR / "section_tags"
CHUNKS_DIR = EXTRACT_DIR / "chunks"

# Pass batch infrastructure (mirrors Stage-3.5 v2 pattern)
PENDING_DIR = EXTRACT_DIR / "pending_step4"
PROCESSED_DIR = EXTRACT_DIR / "processed_step4"
QUARANTINE_FILE = EXTRACT_DIR / "quarantine_step4.jsonl"

# Per-pass verdict streams (immutable, append-only)
VERDICTS_PASS_A = EXTRACT_DIR / "verdicts_pass_a.jsonl"
VERDICTS_PASS_B = EXTRACT_DIR / "verdicts_pass_b.jsonl"
VERDICTS_PASS_C = EXTRACT_DIR / "verdicts_pass_c.jsonl"
VERDICTS_PASS_D = EXTRACT_DIR / "verdicts_pass_d.jsonl"
VERDICTS_PASS_E = EXTRACT_DIR / "verdicts_pass_e.jsonl"

# Consolidated outputs
MERGED_EXTRACTIONS = EXTRACT_DIR / "extractions_merged.jsonl"  # one row per PDF, post-merge_passes.py
DERIVED_EXTRACTIONS = EXTRACT_DIR / "extractions_derived.jsonl"  # post-compute_derived.py
EXTRACTIONS_XLSX = EXTRACT_DIR / "extractions_ai.xlsx"          # final deliverable

# --- OCR-augmented B/C re-run (Step-4 quote-grounding fix; isolated artifacts) ---
PDF_OCR_DIR = EXTRACT_DIR / "PDFs_step4_ocr"             # per-page 300-DPI OCR cache
PDF_TEXT_AUG_DIR = EXTRACT_DIR / "PDFs_step4_text_aug"   # OCR-augmented grounding text
PAGE_OFFSETS_AUG_NAME = "page_offsets_aug.json"          # under PDFs_step4_images/<stable>/
PENDING_BC_DIR = EXTRACT_DIR / "pending_step4_bc"        # isolated B/C refill manifests
VERDICTS_REFILL = EXTRACT_DIR / "verdicts_refill.jsonl"  # accepted refill verdicts
QUARANTINE_REFILL = EXTRACT_DIR / "quarantine_refill.jsonl"
EXTRACTIONS_PRE_REFILL_BAK = EXTRACT_DIR / "extractions_merged.jsonl.pre_refill.bak"

# Audit + logs
AUDIT_LOG = EXTRACT_DIR / "audit_step4.log"
RECONCILIATION_REPORT = EXTRACT_DIR / "reconciliation_step4.csv"

# Subagent definitions live under .claude/agents/ inside EXTRACT_DIR
CLAUDE_AGENTS_DIR = EXTRACT_DIR / ".claude" / "agents"
CLAUDE_ORCHESTRATOR_MD = EXTRACT_DIR / "CLAUDE.md"


# ----------------------------------------------------------------------------
# Thresholds and constants (locked in Phase 1 design)
# ----------------------------------------------------------------------------
# Chunking — TOKEN-BUDGET model (hybrid image+text feed).
# Rationale: with page images in the feed, image tokens dominate the input
# budget, not text bytes. The binding constraint is page count, not char
# count. A paper is fed single-shot per pass whenever its estimated input
# tokens fit under the ceiling; only papers exceeding the ceiling are split,
# and they are split BY PAGES (image tokens dominate) with the corresponding
# text slice carried along via page_offsets.json.
#
# Opus 4.7 context window ~200K tokens. We reserve ~100K headroom for the
# schema prompt, extended-thinking scratch, and output JSON, so the per-chunk
# INPUT ceiling is 100K tokens (85K for Pass D, which also receives Pass B +
# Pass C structured outputs).
TOKENS_PER_PAGE_IMAGE = 1600            # approx vision tokens per 150-DPI page
TOKENS_PER_CHAR = 0.25                  # ~1 token per 4 chars of text
CHUNK_TOKEN_CEILING = 100_000           # per-chunk INPUT ceiling, passes A/B/C
CHUNK_TOKEN_CEILING_PASS_D = 85_000     # lower for Pass D (carries B+C outputs)
CHUNK_PAGE_OVERLAP = 1                  # 1-page overlap between adjacent chunks
CHUNK_BOUNDARY_TOLERANCE_PCT = 0.10     # prefer section break within ±10% when splitting


def estimate_input_tokens(n_pages, n_chars):
    """Estimate subagent input tokens for a hybrid (image+text) feed."""
    return int(n_pages * TOKENS_PER_PAGE_IMAGE + n_chars * TOKENS_PER_CHAR)

# PDF text extraction fallback thresholds
PDFTOTEXT_MIN_BYTES = 2 * 1024          # below this, escalate to next extraction mode
OCR_DPI = 300                           # 300 DPI for OCR fallback

# Batch infrastructure
BATCH_SIZE = 5                          # records per batch (mirrors Stage-3 / 3.5)
PARALLEL_SUBAGENTS = 10                 # 10-way parallelism in orchestrator
QUARANTINE_PAUSE_THRESHOLD = 30         # pause if > this many quarantined per iteration

# Randomness (kept consistent across pipeline stages)
RANDOM_SEED = 20260519                  # seed used by Stage-3.5 v2 audit sampling


# ----------------------------------------------------------------------------
# Lookup tables (locked in Phase 1 design)
# ----------------------------------------------------------------------------
# Ionising-radiation modality lookup (§9.3 derivation rule)
IONISING_MODALITIES = frozenset([
    "PET", "SPECT", "CT", "X-ray", "mammography", "fluoroscopy",
    "gamma_camera", "scintigraphy", "Compton_camera", "proton_CT",
    "proton_radiography", "EPID", "in_vivo_dosimetric_imaging",
    "photon_counting_CT",
])
NON_IONISING_MODALITIES = frozenset([
    "MRI", "ultrasound", "OCT", "fundus", "histopathology",
    "dermoscopy", "microscopy",
])


# ----------------------------------------------------------------------------
# Convenience: ensure_dirs()
# ----------------------------------------------------------------------------
def ensure_dirs():
    """Create all writable Step-4 directories if they do not exist.

    Idempotent. Read-only inputs are NOT created here; missing inputs from
    earlier stages indicate a pipeline-state error and should raise on access,
    not be silently masked.
    """
    for d in [
        EXTRACT_DIR,
        PDF_TEXT_DIR,
        SECTION_TAGS_DIR,
        CHUNKS_DIR,
        PENDING_DIR,
        PROCESSED_DIR,
        CLAUDE_AGENTS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


# ----------------------------------------------------------------------------
# Self-check (run as a script for fast verification)
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    print("PROJECT_ROOT          :", PROJECT_ROOT)
    print("STEP4_DIR             :", STEP4_DIR)
    print("EXTRACT_DIR           :", EXTRACT_DIR)
    print("PDFS_STEP4 exists?    :", PDFS_STEP4.exists())
    print("CANDIDATES_XLSX exists?:", CANDIDATES_XLSX.exists())
    print("MERGED_DATASET exists?:", MERGED_DATASET.exists())
    print("VERDICTS_STAGE3 exists?:", VERDICTS_STAGE3.exists())
    print()
    print("Chunk ceiling (A/B/C)  :", CHUNK_TOKEN_CEILING, "tokens")
    print("Chunk ceiling (D)      :", CHUNK_TOKEN_CEILING_PASS_D, "tokens")
    print("Tokens / page image    :", TOKENS_PER_PAGE_IMAGE)
    print("Tokens / char          :", TOKENS_PER_CHAR)
    print("Page overlap           :", CHUNK_PAGE_OVERLAP, "page(s)")
    print()
    print("Run paths_step4.ensure_dirs() to create writable directories.")
