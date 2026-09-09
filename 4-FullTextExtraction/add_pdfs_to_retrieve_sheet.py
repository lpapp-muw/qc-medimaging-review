#!/usr/bin/env python3
"""
add_pdfs_to_retrieve_sheet.py
=============================

Add a new sheet 'pdfs_to_retrieve' to step4_candidates_v2.xlsx
containing ONLY the records whose current PDF_status is not "have_local"
or "retrieved_OA" -- i.e. the papers that co-authors must still try to
retrieve via institutional access.

This is distinct from the existing 'missing_pdfs' sheet, which is a
historical record of all DOIs we initially flagged missing (including
those subsequently retrieved by the v2 OA pass). The new sheet is the
clean actionable list.

Sheet order after this script:
    1. 00_README
    2. all_candidates
    3. pdfs_to_retrieve   (NEW - actionable)
    4. missing_pdfs       (audit / historical)

The 00_README's Section 4 (PDF retrieval status) and Section 6
(Missing PDFs by publisher) are updated to point at the new sheet.

Usage:
    python3 4-FullTextExtraction/add_pdfs_to_retrieve_sheet.py
"""

import shutil
import sys
from collections import Counter
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl required: pip3 install --break-system-packages openpyxl")

PROJECT_ROOT = Path("/home/lpapp/IEEE_SYS_REV")
STEP4_DIR    = PROJECT_ROOT / "4-FullTextExtraction"
WORKBOOK     = STEP4_DIR / "step4_candidates_v2.xlsx"
WORKBOOK_BAK = STEP4_DIR / "step4_candidates_v2.xlsx.pre_to_retrieve.bak"


def main():
    if not WORKBOOK.exists():
        sys.exit(f"Missing workbook: {WORKBOOK}")
    if not WORKBOOK_BAK.exists():
        shutil.copy2(WORKBOOK, WORKBOOK_BAK)
        print(f"Backed up: {WORKBOOK_BAK}")

    wb = openpyxl.load_workbook(WORKBOOK)
    all_ws = wb["all_candidates"]
    all_headers = [c.value for c in all_ws[1]]

    idx = {h: i + 1 for i, h in enumerate(all_headers)}
    needed = ["DOI", "Title", "Year", "Journal", "Publisher_guess",
              "Stage", "Confidence", "PDF_status"]
    for n in needed:
        if n not in idx:
            sys.exit(f"all_candidates is missing required column: {n}")

    # Collect currently-missing rows
    to_retrieve = []
    for row in all_ws.iter_rows(min_row=2, values_only=True):
        if not row[idx["DOI"] - 1]:
            continue
        status = row[idx["PDF_status"] - 1]
        if status in ("have_local", "retrieved_OA"):
            continue
        to_retrieve.append({
            "doi":       row[idx["DOI"] - 1],
            "title":     row[idx["Title"] - 1] or "",
            "year":      row[idx["Year"] - 1] or "",
            "journal":   row[idx["Journal"] - 1] or "",
            "publisher": row[idx["Publisher_guess"] - 1] or "(unknown)",
            "stage":     row[idx["Stage"] - 1] or "",
            "confidence": row[idx["Confidence"] - 1] or "",
        })

    # Sort by publisher, then DOI, so each institution's papers are grouped
    to_retrieve.sort(key=lambda r: (str(r["publisher"]).lower(), str(r["doi"]).lower()))

    # Drop any existing pdfs_to_retrieve sheet first
    if "pdfs_to_retrieve" in wb.sheetnames:
        del wb["pdfs_to_retrieve"]

    # Insert new sheet AFTER all_candidates (so position 2)
    # Order target: 00_README (0), all_candidates (1), pdfs_to_retrieve (2), missing_pdfs (3)
    all_index = wb.sheetnames.index("all_candidates")
    ws = wb.create_sheet("pdfs_to_retrieve", all_index + 1)

    bold = Font(name="Arial", size=11, bold=True)
    normal = Font(name="Arial", size=11)
    fill_header = PatternFill("solid", fgColor="B4C7E7")
    wrap = Alignment(wrap_text=True, vertical="top")

    headers = [
        "DOI", "Title", "Year", "Journal", "Publisher",
        "Stage", "Confidence",
        "PDF_retrieved_by", "Date_retrieved", "Notes",
    ]
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = bold
        c.fill = fill_header
        c.alignment = wrap

    for row_idx, r in enumerate(to_retrieve, start=2):
        values = [
            r["doi"], r["title"], r["year"], r["journal"], r["publisher"],
            r["stage"], r["confidence"],
            "", "", "",
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.font = normal
            cell.alignment = wrap

    widths = [28, 60, 8, 30, 18, 18, 12, 18, 14, 40]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions

    # ---------- Update the 00_README section 6 (missing-by-publisher) ----------
    # Easiest robust path: rebuild the publisher counts in a small text update
    # at the right rows. We do not rebuild the full README; we just patch the
    # publisher table that already references the missing breakdown.
    # Approach: find the row that contains "6. Missing PDFs by publisher",
    # then replace the table rows below it with the fresh counts.

    if "00_README" in wb.sheetnames:
        rdm = wb["00_README"]
        header_row = None
        for r in range(1, rdm.max_row + 1):
            cell = rdm.cell(row=r, column=1)
            if cell.value and isinstance(cell.value, str) and cell.value.startswith("6. Missing PDFs by publisher"):
                header_row = r
                break
        if header_row is not None:
            # Find the table header row "Publisher | Missing count" beneath
            # it; that row plus its data rows are what we replace.
            table_header_row = None
            for r in range(header_row + 1, min(rdm.max_row + 1, header_row + 10)):
                if rdm.cell(row=r, column=1).value == "Publisher":
                    table_header_row = r
                    break
            if table_header_row is not None:
                # Clear from table_header_row down until a blank row or a row
                # starting with "7."
                last_data_row = table_header_row
                for r in range(table_header_row, min(rdm.max_row + 1, table_header_row + 50)):
                    v = rdm.cell(row=r, column=1).value
                    if v and isinstance(v, str) and v.startswith("7. "):
                        break
                    last_data_row = r
                # Clear cells in cols A and B from table_header_row to last_data_row-1
                for r in range(table_header_row, last_data_row):
                    rdm.cell(row=r, column=1).value = None
                    rdm.cell(row=r, column=2).value = None
                # Repopulate
                rdm.cell(row=table_header_row, column=1).value = "Publisher"
                rdm.cell(row=table_header_row, column=1).font = bold
                rdm.cell(row=table_header_row, column=1).fill = fill_header
                rdm.cell(row=table_header_row, column=2).value = "Missing count"
                rdm.cell(row=table_header_row, column=2).font = bold
                rdm.cell(row=table_header_row, column=2).fill = fill_header
                by_pub = Counter(r["publisher"] for r in to_retrieve)
                rr = table_header_row + 1
                for pub, n in by_pub.most_common():
                    rdm.cell(row=rr, column=1).value = pub
                    rdm.cell(row=rr, column=1).font = normal
                    rdm.cell(row=rr, column=2).value = n
                    rdm.cell(row=rr, column=2).font = normal
                    rr += 1

        # Also update Section 4's counts (PDF retrieval status). We replace
        # specific cells with fresh numbers.
        # Find "4. PDF retrieval status" section.
        sec4_row = None
        for r in range(1, rdm.max_row + 1):
            cell = rdm.cell(row=r, column=1)
            if cell.value and isinstance(cell.value, str) and cell.value.startswith("4. PDF retrieval status"):
                sec4_row = r
                break
        if sec4_row is not None:
            for r in range(sec4_row, min(rdm.max_row + 1, sec4_row + 12)):
                v = rdm.cell(row=r, column=1).value
                if v == "missing (need institutional access)":
                    rdm.cell(row=r, column=2).value = len(to_retrieve)
                    break

    wb.save(WORKBOOK)
    print()
    print(f"=== Summary ===")
    print(f"pdfs_to_retrieve sheet added with {len(to_retrieve)} rows.")
    print(f"Sheet order: {wb.sheetnames}")
    pub_counts = Counter(r["publisher"] for r in to_retrieve)
    print(f"\nBy publisher:")
    for p, n in pub_counts.most_common():
        print(f"  {n:3d}  {p}")
    print(f"\nWorkbook updated: {WORKBOOK}")
    print(f"Backup:           {WORKBOOK_BAK}")


if __name__ == "__main__":
    main()
