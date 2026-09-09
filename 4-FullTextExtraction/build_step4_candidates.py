#!/usr/bin/env python3
"""
build_step4_candidates.py
=========================

Build the consolidated Step-4 candidate set workbook for co-author review.

Combines:
- v2 Step-3 verdicts where primary.category == "QC_FOR_IMAGING"  (179 records)
- v2 Step-3.5 promote_to_IN verdicts                              (8 records)
- v1 Step-3.5 promote_to_IN verdicts                              (4 records)

Total expected: 191 candidates.

PDF status is computed against rdf_pdf_index.csv (the v1 Zotero PDF index).
Records whose DOI is in the index AND whose pdf_exists == True are marked
have_local; everything else is marked missing.

Output: /home/lpapp/IEEE_SYS_REV/4-FullTextExtraction/step4_candidates_v2.xlsx

Sheets:
  1. all_candidates     - 191 rows, every candidate with status
  2. missing_pdfs       - subset where PDF_status != "have_local",
                          sorted by DOI publisher prefix for easy
                          institutional-access triage
  3. instructions       - readme for co-authors

Usage:
    python3 4-FullTextExtraction/build_step4_candidates.py
"""

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("openpyxl is required: pip3 install --break-system-packages openpyxl")


# Paths (resolved from this script's location)
PROJECT_ROOT       = Path('/home/lpapp/IEEE_SYS_REV')
STEP3_V2_DIR       = PROJECT_ROOT / '3-AbstractRetrieve' / 'v2_active'
STEP3_V1_ARCHIVE   = PROJECT_ROOT / '3-AbstractRetrieve' / 'v1_archive'
STEP3_5_V2_DIR     = PROJECT_ROOT / '3.5-IonisingPromote' / 'v2_active'
STEP3_5_V1_ARCHIVE = PROJECT_ROOT / '3.5-IonisingPromote' / 'v1_archive'
STEP4_DIR          = PROJECT_ROOT / '4-FullTextExtraction'

VERDICTS_STEP3_V2  = STEP3_V2_DIR     / 'verdicts.jsonl'
CORPUS_V2          = STEP3_V2_DIR     / 'merged_dataset.json'
VERDICTS_3_5_V2    = STEP3_5_V2_DIR   / 'verdicts_3_5_v2.jsonl'
VERDICTS_3_5_V1    = STEP3_5_V1_ARCHIVE / 'verdicts_3_5.jsonl'
RDF_PDF_INDEX      = STEP3_V1_ARCHIVE / 'rdf_pdf_index.csv'

OUT_XLSX           = STEP4_DIR / 'step4_candidates_v2.xlsx'


def safe_str(x):
    return (x or '').strip()


def load_jsonl(path):
    if not Path(path).exists():
        print(f"WARNING: {path} not found")
        return []
    out = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def doi_publisher_prefix(doi):
    """Return the publisher prefix portion of a DOI (the 10.xxxx part)."""
    if not doi:
        return ''
    doi = doi.lower()
    if not doi.startswith('10.'):
        return ''
    parts = doi.split('/', 1)
    return parts[0]  # e.g., "10.1109"


# Map of well-known DOI publisher prefixes (best-effort, not exhaustive)
PUBLISHER_PREFIX_MAP = {
    '10.1002': 'Wiley',
    '10.1007': 'Springer',
    '10.1016': 'Elsevier',
    '10.1021': 'ACS',
    '10.1038': 'Springer Nature',
    '10.1039': 'RSC',
    '10.1063': 'AIP',
    '10.1088': 'IOP',
    '10.1103': 'APS',
    '10.1109': 'IEEE',
    '10.1117': 'SPIE',
    '10.1145': 'ACM',
    '10.1155': 'Hindawi',
    '10.1364': 'OSA / Optica',
    '10.1371': 'PLOS',
    '10.1186': 'BioMed Central',
    '10.1364': 'Optica',
    '10.1093': 'OUP',
    '10.3389': 'Frontiers',
    '10.3390': 'MDPI',
    '10.1101': 'Cold Spring Harbor',
    '10.1148': 'RSNA (Radiology)',
    '10.1158': 'AACR',
    '10.1118': 'AAPM',
    '10.1287': 'INFORMS',
    '10.1561': 'Now Publishers',
    '10.18280': 'Lavoisier (IIETA)',
    '10.3788': 'Chinese Optics journals',
    '10.5604': 'Polish Academy of Sciences',
    '10.34133': 'AAAS Spectrum',
    '10.13039': 'Crossref Funder',
}


def publisher_name(doi):
    prefix = doi_publisher_prefix(doi)
    return PUBLISHER_PREFIX_MAP.get(prefix, prefix)


def first_authors(authors_list, n=3):
    """Format the first n authors as 'A, B, C, et al'."""
    if not authors_list:
        return ''
    names = []
    for a in authors_list[:n]:
        if isinstance(a, dict):
            fam = a.get('family') or a.get('last')
            giv = a.get('given') or a.get('first')
            if fam and giv:
                names.append(f"{fam}, {giv[0]}.")
            elif fam:
                names.append(fam)
            elif a.get('literal'):
                names.append(a['literal'])
        elif isinstance(a, str):
            names.append(a)
    out = ', '.join(names)
    if len(authors_list) > n:
        out += ', et al.'
    return out


def main():
    if not VERDICTS_STEP3_V2.exists():
        sys.exit(f"Missing Step-3 v2 verdicts: {VERDICTS_STEP3_V2}")
    if not CORPUS_V2.exists():
        sys.exit(f"Missing v2 corpus: {CORPUS_V2}")

    STEP4_DIR.mkdir(exist_ok=True)

    # Load v2 corpus by DOI for metadata
    corpus = json.load(CORPUS_V2.open())
    corpus_by_doi = {}
    for r in corpus:
        doi = safe_str(r.get('DOI') or r.get('doi')).lower()
        if doi:
            corpus_by_doi[doi] = r

    # Load PDF index by DOI
    pdf_index = {}
    if RDF_PDF_INDEX.exists():
        with RDF_PDF_INDEX.open(encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                doi = safe_str(row.get('doi')).lower()
                if doi:
                    pdf_index[doi] = row

    # Load verdicts
    verdicts_s3 = load_jsonl(VERDICTS_STEP3_V2)
    verdicts_s35_v2 = load_jsonl(VERDICTS_3_5_V2)
    verdicts_s35_v1 = load_jsonl(VERDICTS_3_5_V1)

    print(f"Loaded:")
    print(f"  Step-3 v2 verdicts: {len(verdicts_s3)}")
    print(f"  Step-3.5 v2 verdicts: {len(verdicts_s35_v2)}")
    print(f"  Step-3.5 v1 verdicts: {len(verdicts_s35_v1)}")
    print(f"  v2 corpus records: {len(corpus_by_doi)}")
    print(f"  PDF index entries: {len(pdf_index)}")

    # ---- Build candidate list ----
    candidates = []
    seen_dois = set()  # dedupe in case any record appears across sets

    # 1. Step-3 v2 QC_FOR_IMAGING
    for v in verdicts_s3:
        primary = v.get('primary') or {}
        if primary.get('category') != 'QC_FOR_IMAGING':
            continue
        doi = safe_str(v.get('doi')).lower()
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        confidence = v.get('confidence') or 'low'
        candidates.append({
            'doi': doi,
            'record_id': v.get('record_id'),
            'source': 'v2_step3' if not v.get('_reused_from_v1') else 'v1_seed (reused in v2)',
            'stage': f'Step-3 {confidence}',
            'confidence': confidence,
            'primary_category': 'QC_FOR_IMAGING',
            'promote_modality': '',
            'evidence_quote': primary.get('evidence_quote') or '',
            'reasoning': v.get('reasoning') or '',
            'audit_flags': ','.join([k for k in v.keys() if k.startswith('_')]),
        })

    # 2. Step-3.5 v2 promotes
    for v in verdicts_s35_v2:
        if v.get('verdict') != 'promote_to_IN':
            continue
        doi = safe_str(v.get('doi')).lower()
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        candidates.append({
            'doi': doi,
            'record_id': v.get('record_id'),
            'source': 'v2_step3.5',
            'stage': 'Step-3.5 promote',
            'confidence': v.get('confidence') or 'high',
            'primary_category': 'QUANTUM_SENSING_BIOMED (promoted)',
            'promote_modality': v.get('ionising_modality_name') or '',
            'evidence_quote': v.get('context_quote') or v.get('ionising_modality_quote') or '',
            'reasoning': v.get('reasoning') or '',
            'audit_flags': '',
        })

    # 3. Step-3.5 v1 promotes
    for v in verdicts_s35_v1:
        if v.get('verdict') != 'promote_to_IN':
            continue
        doi = safe_str(v.get('doi')).lower()
        if not doi or doi in seen_dois:
            continue
        seen_dois.add(doi)
        candidates.append({
            'doi': doi,
            'record_id': v.get('record_id'),
            'source': 'v1_step3.5',
            'stage': 'Step-3.5 promote',
            'confidence': v.get('confidence') or 'high',
            'primary_category': 'QUANTUM_SENSING_BIOMED (promoted)',
            'promote_modality': v.get('ionising_modality_name') or '',
            'evidence_quote': v.get('context_quote') or v.get('ionising_modality_quote') or '',
            'reasoning': v.get('reasoning') or '',
            'audit_flags': '',
        })

    print(f"Total candidates: {len(candidates)}")

    # Enrich with corpus metadata and PDF status
    for c in candidates:
        rec = corpus_by_doi.get(c['doi']) or {}
        c['title']   = safe_str(rec.get('title'))
        c['year']    = rec.get('issued', {}).get('date-parts', [[None]])[0][0] if isinstance(rec.get('issued'), dict) else (rec.get('year') or '')
        c['journal'] = safe_str(rec.get('container-title') or rec.get('journal') or '')
        c['authors'] = first_authors(rec.get('author', []) or rec.get('authors', []) or [])
        c['publisher_doi_prefix'] = doi_publisher_prefix(c['doi'])
        c['publisher_guess'] = publisher_name(c['doi'])

        # PDF lookup
        pdf_row = pdf_index.get(c['doi'])
        if pdf_row and pdf_row.get('pdf_exists', '').strip().lower() == 'true':
            c['pdf_status'] = 'have_local'
            c['pdf_local_path'] = pdf_row.get('pdf_abs_path', '')
            c['pdf_size_bytes'] = pdf_row.get('pdf_size_bytes', '')
        else:
            c['pdf_status'] = 'missing'
            c['pdf_local_path'] = ''
            c['pdf_size_bytes'] = ''

    # ---- Build workbook ----
    wb = openpyxl.Workbook()

    bold = Font(bold=True)
    fill_header = PatternFill('solid', fgColor='B4C7E7')
    fill_have = PatternFill('solid', fgColor='C6EFCE')
    fill_missing = PatternFill('solid', fgColor='FFC7CE')
    wrap = Alignment(wrap_text=True, vertical='top')

    # === Sheet 1: instructions ===
    ws = wb.active
    ws.title = 'instructions'
    lines = [
        ('Step 4 Candidate Set v2 - For Co-Author Manual Review', True, 14),
        ('', False, 11),
        ('Project: IEEE TRPMS systematic review on Quantum Computing for Medical Imaging Applications', False, 11),
        ('Corresponding author: Laszlo Papp', False, 11),
        ('Generated: from v2 Step-3 + v2 Step-3.5 + v1 Step-3.5 verdicts.', False, 11),
        ('', False, 11),
        ('SHEETS:', True, 11),
        (f'  1. all_candidates: {len(candidates)} records, every Step-4 candidate.', False, 11),
        (f'  2. missing_pdfs: subset requiring institutional-access retrieval, sorted by DOI publisher.', False, 11),
        ('', False, 11),
        ('SOURCE BREAKDOWN:', True, 11),
        (f'  v2 Step-3 QC_FOR_IMAGING (high+low confidence): {sum(1 for c in candidates if c["stage"].startswith("Step-3"))}', False, 11),
        (f'  v2 Step-3.5 promotes:                            {sum(1 for c in candidates if c["source"] == "v2_step3.5")}', False, 11),
        (f'  v1 Step-3.5 promotes:                            {sum(1 for c in candidates if c["source"] == "v1_step3.5")}', False, 11),
        ('', False, 11),
        ('PDF STATUS:', True, 11),
        (f'  have_local: {sum(1 for c in candidates if c["pdf_status"] == "have_local")}', False, 11),
        (f'  missing:    {sum(1 for c in candidates if c["pdf_status"] == "missing")}', False, 11),
        ('', False, 11),
        ('WORKFLOW FOR CO-AUTHORS:', True, 11),
        ('  1. Use the "all_candidates" sheet to assign reviewers (Assigned_reviewer column).', False, 11),
        ('  2. The "missing_pdfs" sheet lists papers we could not retrieve open-access.', False, 11),
        ('     Sort/filter by Publisher_guess to identify papers your institution likely has access to.', False, 11),
        ('  3. As you obtain PDFs, drop them in a shared folder and update PDF_retrieved_by column.', False, 11),
        ('  4. As you complete full-text review, fill Decision (include / exclude / uncertain) and Notes.', False, 11),
        ('', False, 11),
        ('CONFIDENCE: "high" = abstract clearly supports the verdict; "low" = ambiguous abstract.', False, 11),
        ('Low-confidence Step-3 records (59 of the 179) should be reviewed first since the AI screen was uncertain.', False, 11),
    ]
    for i, (text, is_bold, size) in enumerate(lines, start=1):
        cell = ws.cell(row=i, column=1, value=text)
        if is_bold:
            cell.font = Font(bold=True, size=size)
    ws.column_dimensions['A'].width = 120

    # === Sheet 2: all_candidates ===
    ws = wb.create_sheet('all_candidates')
    headers = [
        'DOI', 'Title', 'Year', 'Journal', 'Authors',
        'Publisher_guess', 'Source', 'Stage', 'Confidence',
        'Primary_category', 'Promote_modality', 'Evidence_quote', 'Reasoning',
        'Audit_flags', 'PDF_status', 'PDF_local_path',
        'Assigned_reviewer', 'PDF_retrieved_by', 'Decision', 'Notes',
    ]
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = bold
        c.fill = fill_header
        c.alignment = wrap

    # Sort: high-confidence Step-3 first, then low-confidence Step-3, then Step-3.5 promotes
    def sort_key(c):
        stage_order = {'Step-3 high': 0, 'Step-3 low': 1, 'Step-3.5 promote': 2}
        return (stage_order.get(c['stage'], 99), c.get('publisher_guess') or '', c['doi'])

    for row_idx, c in enumerate(sorted(candidates, key=sort_key), start=2):
        values = [
            c['doi'], c['title'], c['year'], c['journal'], c['authors'],
            c['publisher_guess'], c['source'], c['stage'], c['confidence'],
            c['primary_category'], c['promote_modality'],
            c['evidence_quote'], c['reasoning'],
            c['audit_flags'], c['pdf_status'], c['pdf_local_path'],
            '', '', '', '',
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = wrap
        # Color the PDF_status column
        status_col = headers.index('PDF_status') + 1
        if c['pdf_status'] == 'have_local':
            ws.cell(row=row_idx, column=status_col).fill = fill_have
        else:
            ws.cell(row=row_idx, column=status_col).fill = fill_missing

    widths = [28, 60, 8, 30, 30, 18, 22, 18, 12,
              26, 26, 50, 50, 24, 16, 50,
              18, 18, 18, 40]
    for col, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = ws.dimensions

    # === Sheet 3: missing_pdfs ===
    ws = wb.create_sheet('missing_pdfs')
    headers_miss = [
        'DOI', 'Title', 'Year', 'Journal', 'Publisher_guess',
        'Stage', 'Confidence', 'PDF_retrieved_by', 'OA_url_tried', 'Notes',
    ]
    for col, h in enumerate(headers_miss, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = bold
        c.fill = fill_header

    missing = [c for c in candidates if c['pdf_status'] == 'missing']
    # Sort by publisher prefix so each institution can grab "its" papers
    missing.sort(key=lambda c: (c.get('publisher_guess') or '', c['doi']))
    for row_idx, c in enumerate(missing, start=2):
        values = [
            c['doi'], c['title'], c['year'], c['journal'], c['publisher_guess'],
            c['stage'], c['confidence'], '', '', '',
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = wrap

    widths_miss = [28, 60, 8, 30, 18, 18, 12, 20, 30, 40]
    for col, w in enumerate(widths_miss, start=1):
        ws.column_dimensions[get_column_letter(col)].width = w
    ws.freeze_panes = 'B2'
    ws.auto_filter.ref = ws.dimensions

    wb.save(OUT_XLSX)
    print()
    print(f"Wrote: {OUT_XLSX}")
    print(f"  all_candidates: {len(candidates)}")
    print(f"  missing_pdfs:   {len(missing)}")
    print()
    # Publisher breakdown of missing
    pub_counts = defaultdict(int)
    for c in missing:
        pub_counts[c['publisher_guess']] += 1
    print(f"Missing PDFs by publisher:")
    for pub, n in sorted(pub_counts.items(), key=lambda x: -x[1]):
        print(f"  {n:3d}  {pub}")


if __name__ == '__main__':
    main()
