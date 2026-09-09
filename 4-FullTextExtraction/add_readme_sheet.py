#!/usr/bin/env python3
"""
add_readme_sheet.py
===================

Replace the existing "instructions" sheet in step4_candidates_v2.xlsx
with a comprehensive "00_README" sheet that combines:

- Project context (one-paragraph overview, OSF protocol reference)
- PRISMA flow numbers (v1 + v2 corpus expansion)
- Step-4 candidate set composition (counts by stage, confidence)
- PDF retrieval status (have_local / retrieved_OA / missing)
- Workflow instructions for co-authors
- Publisher breakdown of missing PDFs (for institutional-access planning)
- Notes on the audit flags

Pin the new README sheet as the first sheet (leftmost tab) so it opens
by default.

Usage:
    python3 4-FullTextExtraction/add_readme_sheet.py
"""

import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl required: pip3 install --break-system-packages openpyxl")

PROJECT_ROOT = Path("/home/lpapp/IEEE_SYS_REV")
STEP4_DIR    = PROJECT_ROOT / "4-FullTextExtraction"
WORKBOOK     = STEP4_DIR / "step4_candidates_v2.xlsx"
WORKBOOK_BAK = STEP4_DIR / "step4_candidates_v2.xlsx.pre_readme.bak"
PDF_FOLDER   = STEP4_DIR / "PDFs_step4"


def main():
    if not WORKBOOK.exists():
        sys.exit(f"Missing workbook: {WORKBOOK}")

    if not WORKBOOK_BAK.exists():
        shutil.copy2(WORKBOOK, WORKBOOK_BAK)
        print(f"Backed up: {WORKBOOK_BAK}")

    wb = openpyxl.load_workbook(WORKBOOK)

    # Read all_candidates for counting
    all_ws = wb["all_candidates"]
    miss_ws = wb["missing_pdfs"]

    all_headers = [c.value for c in all_ws[1]]
    miss_headers = [c.value for c in miss_ws[1]]

    a_status = all_headers.index("PDF_status") + 1
    a_stage  = all_headers.index("Stage") + 1
    a_conf   = all_headers.index("Confidence") + 1
    a_pub    = all_headers.index("Publisher_guess") + 1

    by_stage  = Counter()
    by_conf   = Counter()
    by_status = Counter()
    by_pub_miss = Counter()
    total = 0

    for row in all_ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        total += 1
        by_stage[row[a_stage - 1]] += 1
        by_conf[row[a_conf - 1]] += 1
        by_status[row[a_status - 1]] += 1
        if row[a_status - 1] not in ("have_local", "retrieved_OA"):
            by_pub_miss[row[a_pub - 1] or "(unknown)"] += 1

    pdfs_in_folder = len(list(PDF_FOLDER.glob("*.pdf"))) if PDF_FOLDER.exists() else 0
    missing_count = by_status.get("missing", 0) + by_status.get(None, 0)
    have_local = by_status.get("have_local", 0)
    retrieved_OA = by_status.get("retrieved_OA", 0)

    # Remove existing "instructions" sheet
    if "instructions" in wb.sheetnames:
        del wb["instructions"]
        print("Removed existing 'instructions' sheet.")
    if "00_README" in wb.sheetnames:
        del wb["00_README"]
        print("Removed existing '00_README' sheet.")

    # Create new 00_README sheet and move it to position 0
    ws = wb.create_sheet("00_README", 0)

    # Styles
    title_font   = Font(name="Arial", size=14, bold=True)
    section_font = Font(name="Arial", size=12, bold=True)
    bold_font    = Font(name="Arial", size=11, bold=True)
    normal_font  = Font(name="Arial", size=11)
    small_font   = Font(name="Arial", size=10)
    wrap_align   = Alignment(wrap_text=True, vertical="top", horizontal="left")
    center_align = Alignment(horizontal="center", vertical="center")
    fill_header  = PatternFill("solid", fgColor="B4C7E7")
    fill_section = PatternFill("solid", fgColor="E6E6E6")

    row = 1

    def put(text, *, font=normal_font, fill=None, align=wrap_align, span=None):
        nonlocal row
        cell = ws.cell(row=row, column=1, value=text)
        cell.font = font
        cell.alignment = align
        if fill is not None:
            cell.fill = fill
        if span:
            ws.merge_cells(start_row=row, start_column=1,
                           end_row=row, end_column=span)
        row += 1

    def put_table_row(values, *, font=normal_font, fill=None, align=wrap_align):
        nonlocal row
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row, column=col_idx, value=val)
            cell.font = font
            cell.alignment = align
            if fill is not None:
                cell.fill = fill
        row += 1

    def blank():
        nonlocal row
        row += 1

    # ===== Title block =====
    put("Step 4 Candidate Workbook — IEEE TRPMS Systematic Review",
        font=title_font, span=5)
    put("Quantum Computing for Medical Imaging Applications",
        font=section_font, span=5)
    put(f"Corresponding author: Laszlo Papp   |   Generated: {date.today().isoformat()}",
        font=small_font, span=5)
    blank()

    # ===== 1. Overview =====
    put("1. Overview", font=section_font, fill=fill_section, span=5)
    put(
        "Two-pass design: a v1 pilot on the open-access subset of the "
        "literature locked the 10-category screening taxonomy; a v2 "
        "full-corpus pass then applied that locked taxonomy to all 6847 "
        "deduplicated DOIs returned by PubMed, Web of Science, and IEEE "
        "Xplore on 30 January 2026. The OSF protocol v0.6 documents the "
        "full methodology. This workbook is the working artefact for the "
        "Step-4 (full-text review) phase of the pipeline.",
        span=5)
    blank()

    # ===== 2. PRISMA flow =====
    put("2. PRISMA flow (v2 corpus is canonical)",
        font=section_font, fill=fill_section, span=5)
    flow_lines = [
        "Database hits on 30 Jan 2026: PubMed 1569, WoS 2201, IEEE Xplore 123.",
        "v2 full-corpus search (non-OA expansion): 8961 raw entries; deduplicated to 6847 unique DOIs.",
        "v1 pilot: 2977 unique DOIs (OA-filtered); 2974 entered Stage 3 after 4 hard exclusions.",
        "PRISMA exclusions before Stage 3 (v2): 8 DOIs with no retrievable metadata from any of Crossref / OpenAlex / EuropePMC.",
        "Records screened by AI-assisted abstract classification (Stage 3): 6839.",
        f"Stage-3 IN-scope: 179 QC_FOR_IMAGING (120 high-confidence, 59 low-confidence).",
        "Stage-3.5 ionising sub-screen: 4 promotes from v1 (146 records screened) + 8 promotes from v2 (96 NEW records screened) = 12 combined promotes.",
        f"Total Step-4 candidates: {total} (= 179 QC_FOR_IMAGING + 12 Step-3.5 promotes).",
    ]
    for line in flow_lines:
        put("  • " + line, span=5)
    blank()

    # ===== 3. Candidate composition =====
    put("3. Step-4 candidate set composition",
        font=section_font, fill=fill_section, span=5)
    blank()
    # Stage table
    put_table_row(["Stage", "Count"], font=bold_font, fill=fill_header)
    for s, n in by_stage.most_common():
        put_table_row([s or "(unknown)", n])
    put_table_row(["TOTAL", total], font=bold_font)
    blank()
    # Confidence table
    put_table_row(["Confidence", "Count"], font=bold_font, fill=fill_header)
    for c, n in by_conf.most_common():
        put_table_row([c or "(unknown)", n])
    blank()

    # ===== 4. PDF status =====
    put("4. PDF retrieval status",
        font=section_font, fill=fill_section, span=5)
    blank()
    put_table_row(["PDF status", "Count"], font=bold_font, fill=fill_header)
    put_table_row(["have_local (from v1 Zotero storage)", have_local])
    put_table_row(["retrieved_OA (v2 open-access retrieval pass)", retrieved_OA])
    put_table_row(["missing (need institutional access)", missing_count])
    put_table_row(["TOTAL", total], font=bold_font)
    blank()
    put(
        f"All {pdfs_in_folder} available PDFs are consolidated in the "
        f"folder PDFs_step4/ alongside this workbook. Filename convention: "
        f"<doi-with-special-chars-replaced>.pdf "
        f"(e.g. 10.1109_tns.2024.3369972.pdf).",
        span=5)
    blank()

    # ===== 5. Workflow =====
    put("5. Workflow for co-author full-text review",
        font=section_font, fill=fill_section, span=5)
    workflow = [
        "Open the 'all_candidates' sheet. Filter or claim records via the 'Assigned_reviewer' column.",
        "For each claimed record: open the PDF in PDFs_step4/ (if PDF_status is have_local or retrieved_OA).",
        "Apply the Step-4 eligibility criteria from the OSF protocol (section 7.5). Fill the 'Decision' column with: include / exclude / uncertain.",
        "For low-confidence Stage-3 records (Confidence column = low), expect a higher exclusion rate; the AI screen was uncertain on these.",
        "For missing PDFs (see 'missing_pdfs' sheet), attempt retrieval via your institutional access. Use the publisher guess column to triage. Record your initials in 'PDF_retrieved_by' and drop the file in PDFs_step4/ with the DOI-derived filename.",
        "Use the 'Notes' column for any reasoning that should appear in the manuscript or the PRISMA exclusion table.",
        "Once a record is decided, save the workbook. Coordinate sharing of decisions between co-authors (e.g. once-weekly merge).",
    ]
    for line in workflow:
        put("  • " + line, span=5)
    blank()

    # ===== 6. Missing PDFs by publisher =====
    put("6. Missing PDFs by publisher (for institutional-access triage)",
        font=section_font, fill=fill_section, span=5)
    put(
        f"The {missing_count} missing PDFs are distributed as follows. The "
        f"full per-paper list is on sheet 'missing_pdfs'.",
        span=5)
    blank()
    put_table_row(["Publisher", "Missing count"], font=bold_font, fill=fill_header)
    for p, n in by_pub_miss.most_common():
        put_table_row([p, n])
    blank()

    # ===== 7. Audit flags =====
    put("7. Audit flags on Stage-3 verdicts (column 'Audit_flags')",
        font=section_font, fill=fill_section, span=5)
    audit_lines = [
        "_reused_from_v1: verdict reused from the v1 pilot pass because the abstract was identical to v1.",
        "_quote_rescued: evidence quote required relaxed Unicode normalisation to validate (smart-quote / dash mismatch).",
        "_evidence_field_corrected: the subagent labeled the quote source as title but it was found in abstract (or vice-versa).",
        "_secondary_stripped: the secondary category structure was malformed and was stripped; primary verdict stands.",
        "_quote_unverified: quote could not be verified verbatim or via rescue; primary category retained with audit flag (small number of records, all OUT-of-scope).",
        "_manual: verdict was assigned manually (very small number; documented in the audit log).",
    ]
    for line in audit_lines:
        put("  • " + line, font=small_font, span=5)
    blank()

    # ===== 8. Companion artifacts =====
    put("8. Companion artifacts",
        font=section_font, fill=fill_section, span=5)
    companions = [
        "step4_candidates_v2.xlsx — THIS workbook.",
        "PDFs_step4/ — folder containing all retrieved PDFs (105 files).",
        "OSF_Protocol_QC_MedImaging_v0_6.docx — systematic review protocol (OSF deposit candidate).",
    ]
    for line in companions:
        put("  • " + line, span=5)

    # Column widths and freeze
    ws.column_dimensions["A"].width = 90
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 18
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
    # No frozen panes needed for the README

    # Save
    wb.save(WORKBOOK)
    print()
    print(f"=== Summary ===")
    print(f"00_README sheet added at position 0.")
    print(f"Total candidates:   {total}")
    print(f"have_local:         {have_local}")
    print(f"retrieved_OA:       {retrieved_OA}")
    print(f"missing:            {missing_count}")
    print(f"PDFs on disk:       {pdfs_in_folder}")
    print(f"Workbook saved:     {WORKBOOK}")
    print(f"Backup:             {WORKBOOK_BAK}")


if __name__ == "__main__":
    main()
