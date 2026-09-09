#!/usr/bin/env python3
"""
fetch_missing_pdfs.py
=====================

Pipeline wrapper that:

1. Reads /home/lpapp/IEEE_SYS_REV/4-FullTextExtraction/step4_candidates_v2.xlsx,
   sheet "missing_pdfs".
2. Writes a temporary single-column XLSX with header "DOI" containing the
   missing DOIs to /home/lpapp/IEEE_SYS_REV/4-FullTextExtraction/missing_dois.xlsx.
3. Invokes the existing v1 downloader script
   /home/lpapp/IEEE_SYS_REV/DOIRetrieve/doi_pdf_downloader_best.py
   on that file, with --pdf-dir pointing at the Step-4 PDFs/ folder.
4. Reads the downloader's output (which updates the input XLSX with a "PDF"
   column) and merges it back into step4_candidates_v2.xlsx, updating
   the "all_candidates" and "missing_pdfs" sheets:
       - if PDF column contains a filename: PDF_status -> "retrieved_OA",
         PDF_local_path -> the downloaded file path.
       - else: PDF_status remains "missing", OA_url_tried -> "Undetected".

Usage:
    # First, ensure step4_candidates_v2.xlsx exists (run build_step4_candidates.py).
    python3 4-FullTextExtraction/fetch_missing_pdfs.py

The script preserves any manual edits in Assigned_reviewer / PDF_retrieved_by /
Decision / Notes columns. It updates only PDF_status and PDF_local_path on
the all_candidates sheet, and OA_url_tried on the missing_pdfs sheet.

Wall time estimate: ~30-90 seconds per DOI depending on publisher response,
so ~60 missing DOIs ~ 30-90 min total. The downloader script itself is
resumable.

Author: Laszlo Papp (orchestration), v1 downloader: existing
Date:   2026-05-19
"""

import shutil
import subprocess
import sys
from pathlib import Path

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl is required: pip3 install --break-system-packages openpyxl")

PROJECT_ROOT     = Path('/home/lpapp/IEEE_SYS_REV')
STEP4_DIR        = PROJECT_ROOT / '4-FullTextExtraction'
WORKBOOK         = STEP4_DIR / 'step4_candidates_v2.xlsx'
WORKBOOK_BAK     = STEP4_DIR / 'step4_candidates_v2.xlsx.pre_fetch.bak'
MISSING_DOIS_XLS = STEP4_DIR / 'missing_dois.xlsx'
PDF_OUTPUT_DIR   = STEP4_DIR / 'PDFs_v2'
DOWNLOADER       = PROJECT_ROOT / 'DOIRetrieve' / 'doi_pdf_downloader_best.py'


def main():
    if not WORKBOOK.exists():
        sys.exit(f"Missing workbook: {WORKBOOK}. Run build_step4_candidates.py first.")
    if not DOWNLOADER.exists():
        sys.exit(f"Missing downloader: {DOWNLOADER}")

    # Backup workbook
    if not WORKBOOK_BAK.exists():
        shutil.copy2(WORKBOOK, WORKBOOK_BAK)
        print(f"Backed up: {WORKBOOK_BAK}")

    PDF_OUTPUT_DIR.mkdir(exist_ok=True)

    # ---- Step 1: Read the missing_pdfs sheet ----
    wb = openpyxl.load_workbook(WORKBOOK)
    if 'missing_pdfs' not in wb.sheetnames:
        sys.exit("missing_pdfs sheet not found in workbook")
    miss_ws = wb['missing_pdfs']

    # Find DOI column (should be A)
    headers_miss = [c.value for c in miss_ws[1]]
    doi_col_idx = headers_miss.index('DOI') + 1
    missing_dois = []
    for row in miss_ws.iter_rows(min_row=2, values_only=False):
        doi = row[doi_col_idx - 1].value
        if doi:
            missing_dois.append(doi)
    print(f"Missing DOIs to fetch: {len(missing_dois)}")
    if not missing_dois:
        print("Nothing to do; all candidates already have PDFs.")
        return

    # ---- Step 2: Write a temp XLSX with header "DOI" for the downloader ----
    tmp_wb = openpyxl.Workbook()
    tmp_ws = tmp_wb.active
    tmp_ws.title = 'Sheet1'
    tmp_ws.cell(row=1, column=1, value='DOI')
    for i, doi in enumerate(missing_dois, start=2):
        tmp_ws.cell(row=i, column=1, value=doi)
    tmp_wb.save(MISSING_DOIS_XLS)
    print(f"Wrote {MISSING_DOIS_XLS} with {len(missing_dois)} DOIs")

    # ---- Step 3: Invoke the v1 downloader ----
    cmd = [
        'python3', str(DOWNLOADER),
        '--input', str(MISSING_DOIS_XLS),
        '--pdf-dir', str(PDF_OUTPUT_DIR),
        '--unpaywall-email', 'laszlo.papp@meduniwien.ac.at',
        '--contact-email', 'laszlo.papp@meduniwien.ac.at',
        '--verbose',
    ]
    print(f"Invoking: {' '.join(cmd)}")
    print("(this may take 30-90 min depending on the number of DOIs)")
    proc = subprocess.run(cmd, cwd=str(STEP4_DIR), check=False,
                          stdout=sys.stdout, stderr=sys.stderr)
    if proc.returncode != 0:
        print(f"WARNING: downloader exited with code {proc.returncode}")
        print("Some PDFs may have been retrieved. Proceeding with whatever is in the output workbook.")

    # ---- Step 4: Read the downloader's output (it modified MISSING_DOIS_XLS in place) ----
    out_wb = openpyxl.load_workbook(MISSING_DOIS_XLS)
    out_ws = out_wb.active
    out_headers = [c.value for c in out_ws[1]]
    if 'PDF' not in out_headers:
        sys.exit("Downloader did not add a 'PDF' column to the output. Manual investigation needed.")
    pdf_col_idx = out_headers.index('PDF') + 1
    out_doi_col = out_headers.index('DOI') + 1

    # Build DOI -> PDF status map
    pdf_results = {}
    retrieved = 0
    undetected = 0
    for row in out_ws.iter_rows(min_row=2, values_only=True):
        doi = row[out_doi_col - 1]
        pdf = row[pdf_col_idx - 1]
        if not doi:
            continue
        doi = str(doi).strip().lower()
        if pdf and str(pdf).strip().lower() != 'undetected':
            pdf_results[doi] = ('retrieved_OA', str(pdf))
            retrieved += 1
        else:
            pdf_results[doi] = ('still_missing', 'Undetected')
            undetected += 1

    print()
    print(f"Downloader results: {retrieved} retrieved, {undetected} undetected")

    # ---- Step 5: Update both sheets in step4_candidates_v2.xlsx ----
    # 5a: all_candidates
    all_ws = wb['all_candidates']
    all_headers = [c.value for c in all_ws[1]]
    doi_idx_all = all_headers.index('DOI') + 1
    status_idx = all_headers.index('PDF_status') + 1
    path_idx = all_headers.index('PDF_local_path') + 1

    updates_all = 0
    for row in all_ws.iter_rows(min_row=2):
        doi = row[doi_idx_all - 1].value
        if not doi:
            continue
        doi = str(doi).strip().lower()
        result = pdf_results.get(doi)
        if result is None:
            continue
        status, pdf_path_or_msg = result
        if status == 'retrieved_OA':
            row[status_idx - 1].value = 'retrieved_OA'
            # Construct full path: downloader saves to PDF_OUTPUT_DIR
            full_path = PDF_OUTPUT_DIR / pdf_path_or_msg
            row[path_idx - 1].value = str(full_path) if full_path.exists() else pdf_path_or_msg
            updates_all += 1

    # 5b: missing_pdfs sheet: update OA_url_tried column
    miss_headers = [c.value for c in miss_ws[1]]
    doi_idx_miss = miss_headers.index('DOI') + 1
    oa_tried_idx = miss_headers.index('OA_url_tried') + 1
    rows_to_delete_indices = []  # rows that have been retrieved
    for i, row in enumerate(miss_ws.iter_rows(min_row=2), start=2):
        doi = row[doi_idx_miss - 1].value
        if not doi:
            continue
        doi = str(doi).strip().lower()
        result = pdf_results.get(doi)
        if result is None:
            continue
        status, msg = result
        if status == 'retrieved_OA':
            rows_to_delete_indices.append(i)
        else:
            row[oa_tried_idx - 1].value = msg

    # Delete retrieved rows from missing_pdfs (work in reverse to keep indices valid)
    for i in sorted(rows_to_delete_indices, reverse=True):
        miss_ws.delete_rows(i, 1)

    wb.save(WORKBOOK)
    print()
    print(f"Updated workbook: {WORKBOOK}")
    print(f"  all_candidates updated rows:  {updates_all}")
    print(f"  missing_pdfs rows removed:    {len(rows_to_delete_indices)}")
    print(f"  missing_pdfs rows remaining:  {miss_ws.max_row - 1}")
    print()
    print(f"Backup of pre-fetch state: {WORKBOOK_BAK}")


if __name__ == '__main__':
    main()
