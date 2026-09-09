#!/usr/bin/env python3
"""
consolidate_pdfs.py
===================

Read step4_candidates_v2.xlsx and copy every available PDF (either
have_local from the Zotero RDF storage, or retrieved_OA from PDFs_v2/)
into a single flat folder PDFs_step4/ with stable DOI-derived filenames.

Update the workbook's PDF_local_path column to point at the new flat
location, so the workbook becomes self-contained for sharing.

Naming convention: DOI lowercased, non-alphanumerics replaced with '_',
suffix '.pdf'. Example: 10.1109/tns.2024.3369972 -> 10.1109_tns.2024.3369972.pdf

Usage:
    python3 4-FullTextExtraction/consolidate_pdfs.py
"""

import re
import shutil
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl required: pip3 install --break-system-packages openpyxl")

PROJECT_ROOT = Path("/home/lpapp/IEEE_SYS_REV")
STEP4_DIR    = PROJECT_ROOT / "4-FullTextExtraction"
WORKBOOK     = STEP4_DIR / "step4_candidates_v2.xlsx"
WORKBOOK_BAK = STEP4_DIR / "step4_candidates_v2.xlsx.pre_consolidate.bak"
OUT_DIR      = STEP4_DIR / "PDFs_step4"


def doi_to_filename(doi):
    safe = re.sub(r"[^A-Za-z0-9.-]", "_", doi.lower())
    return safe + ".pdf"


def main():
    if not WORKBOOK.exists():
        sys.exit(f"Missing workbook: {WORKBOOK}")
    if not WORKBOOK_BAK.exists():
        shutil.copy2(WORKBOOK, WORKBOOK_BAK)
        print(f"Backed up: {WORKBOOK_BAK}")

    OUT_DIR.mkdir(exist_ok=True)

    wb = openpyxl.load_workbook(WORKBOOK)
    ws = wb["all_candidates"]
    headers = [c.value for c in ws[1]]
    doi_idx     = headers.index("DOI") + 1
    status_idx  = headers.index("PDF_status") + 1
    path_idx    = headers.index("PDF_local_path") + 1

    copied = 0
    missing_src = 0
    already_in_target = 0
    failed = []

    for row in ws.iter_rows(min_row=2):
        doi = row[doi_idx - 1].value
        status = row[status_idx - 1].value
        src_path_str = row[path_idx - 1].value
        if not doi or status not in ("have_local", "retrieved_OA"):
            continue
        doi = str(doi).strip().lower()
        if not src_path_str:
            failed.append((doi, "no_source_path"))
            continue
        src_path = Path(src_path_str)
        if not src_path.exists():
            failed.append((doi, f"source_missing:{src_path}"))
            missing_src += 1
            continue
        dst_name = doi_to_filename(doi)
        dst_path = OUT_DIR / dst_name
        if dst_path.exists() and dst_path.stat().st_size > 0:
            already_in_target += 1
            # Still update the workbook to the canonical location
            row[path_idx - 1].value = str(dst_path)
            continue
        try:
            shutil.copy2(src_path, dst_path)
            row[path_idx - 1].value = str(dst_path)
            copied += 1
        except Exception as e:
            failed.append((doi, f"copy_err:{e}"))

    wb.save(WORKBOOK)

    print()
    print(f"=== consolidate_pdfs.py summary ===")
    print(f"Copied to {OUT_DIR}: {copied}")
    print(f"Already in target folder: {already_in_target}")
    print(f"Source PDFs missing on disk: {missing_src}")
    print(f"Other failures: {len(failed) - missing_src}")
    if failed:
        print()
        print("Failures detail (first 10):")
        for doi, reason in failed[:10]:
            print(f"  {doi}: {reason}")
    print()
    print(f"Workbook updated: {WORKBOOK}")
    print(f"Backup: {WORKBOOK_BAK}")
    print()
    print(f"Total PDFs in {OUT_DIR}: {len(list(OUT_DIR.glob('*.pdf')))}")


if __name__ == "__main__":
    main()
