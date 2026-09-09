"""
extractions_to_xlsx.py — Emit two geometrically identical workbooks from the
locked schema:

  1. extraction_human_blank.xlsx  — the HUMAN extraction instrument.
       Pre-filled only with identifiers (doi, title, stable_name, pdf_file).
       All field cells empty. Enum/bool cells carry Excel data-validation
       dropdowns sourced from a hidden `vocab` sheet. Paired value/quote
       columns. No AI values (preserves §4.6 blinding).

  2. extractions_ai.xlsx          — the AI anchor.
       Identical sheets/columns/order, populated from extractions_derived.jsonl
       (values + quotes), plus an audit_flags sheet.

Identical geometry is what makes the Step-5 cell-for-cell diff trivial.

Column layout (both files), one row per paper:
  identifiers:  doi | title | stable_name | pdf_file | year
  Pass A:       <field> | <field>_quote   (paired) ...
  Pass B:       <field> | <field>_quote   ...
  Pass C:       <field> | <field>_quote   ...
  Pass D:       <field> | <field>_quote   ...
  human-only:   clinical_impact_assessment | clinical_impact_assessment_justification
  derived:      <derived_field>            (value only; computed, no quote)

Conventions:
  - Multi-enum / multi-value fields: SEMICOLON-SEPARATED in a single cell
    (e.g. "amplitude; FRQI"). Documented in README and applied by the AI export
    too, so both sides use the same convention.
  - Booleans: dropdown {TRUE, FALSE}.
  - Enums: dropdown of the exact allowed vocabulary.
  - Null-equivalent AI values are written as "not_reported"/"none"/"" per field.

Sheets:
  00_README     — purpose, conventions, protocol refs, field definitions
  extractions   — the data grid (one row per paper)
  vocab         — allowed values per enum field (drives dropdowns; hidden)
  audit_flags   — (AI file only) per-paper flags + Pass-E contradictions

Python 3.8 compatible. Requires openpyxl.

Usage:
  python3 extractions_to_xlsx.py --human   # build blank instrument only
  python3 extractions_to_xlsx.py --ai      # build AI anchor only
  python3 extractions_to_xlsx.py           # build both
"""

import argparse
import json
import sys

import paths_step4 as P
import extraction_schema as S

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter


# ----------------------------------------------------------------------------
# Column model
# ----------------------------------------------------------------------------
IDENTIFIER_COLS = ["doi", "title", "stable_name", "pdf_file", "year"]

PASS_GROUPS = [
    ("A", "Bibliographic / Imaging / Translational"),
    ("B", "QC characterisation"),
    ("C", "Validation & methodological quality"),
    ("D", "Risk-of-bias (explainability)"),
]


def build_column_spec():
    """Return an ordered list of column dicts:
       {key, header, kind, allowed, group}
       kind in {id, value, quote, derived, human}
    """
    cols = []
    for k in IDENTIFIER_COLS:
        cols.append({"key": k, "header": k, "kind": "id", "allowed": None, "group": "identifiers"})

    for pl, group in PASS_GROUPS:
        for f in S.fields_for_pass(pl):
            if f.derived or f.human_only or f.pass_ == S.PASS_META:
                continue
            cols.append({"key": f.name, "header": f.name, "kind": "value",
                         "allowed": f.allowed, "group": group,
                         "ftype": f.type_, "multivalue": f.multivalue})
            if f.requires_quote:
                cols.append({"key": f.name + "_quote", "header": f.name + "_quote",
                             "kind": "quote", "allowed": None, "group": group})

    # human-only field + its justification
    cols.append({"key": "clinical_impact_assessment", "header": "clinical_impact_assessment",
                 "kind": "human", "allowed": ["low", "moderate", "high"],
                 "group": "Human assessment", "ftype": S.TYPE_ENUM, "multivalue": False})
    cols.append({"key": "clinical_impact_assessment_justification",
                 "header": "clinical_impact_assessment_justification",
                 "kind": "quote", "allowed": None, "group": "Human assessment"})

    # derived (value only)
    for f in S.derived_fields():
        cols.append({"key": f.name, "header": f.name + " (derived)", "kind": "derived",
                     "allowed": f.allowed, "group": "Derived (auto-computed)",
                     "ftype": f.type_, "multivalue": f.multivalue})
    # Paradigm subcategorisation columns (computed by paradigm_subcategory.py via
    # compute_derived.py and stored in record["derived"]). allowed=None so they
    # render as plain green text with no dropdown/vocab (derived, not human-edited).
    # The derived render branch keys off "key" -> record["derived"][key]["value"].
    for _pk in ("paradigm_subcategory", "paradigm_facets", "paradigm_resolution_basis"):
        cols.append({"key": _pk, "header": _pk + " (derived)", "kind": "derived",
                     "allowed": None, "group": "Derived (auto-computed)",
                     "ftype": None, "multivalue": False})
    # Registered Axis B (OSF v0.6 sec 7.6): 9-value paradigm rollup of
    # paradigm_subcategory (paradigm_axis_b.py). Authoritative for the manuscript;
    # placed next to the finer paradigm_subcategory above.
    cols.append({"key": "paradigm_axis_b", "header": "paradigm_axis_b (derived)",
                 "kind": "derived", "allowed": None, "group": "Derived (auto-computed)",
                 "ftype": None, "multivalue": False})
    # Hardware-execution subcategorisation columns (computed by hardware_execution.py
    # via compute_derived.py, stored in record["derived"]). Same rationale as the
    # paradigm columns above: allowed=None -> plain green text with no dropdown; the
    # derived render branch keys off "key" -> record["derived"][key]["value"].
    for _hk in ("hardware_execution_subcategory", "hardware_execution_facets",
                "hardware_execution_basis"):
        cols.append({"key": _hk, "header": _hk + " (derived)", "kind": "derived",
                     "allowed": None, "group": "Derived (auto-computed)",
                     "ftype": None, "multivalue": False})
    # Registered Axis D (OSF v0.6 sec 7.6): execution context {ideal/noisy/real}
    # (execution_context_axis.py). The vendor / qubit-modality / compilation
    # dimensions of Axis D are the existing hardware_vendor / hardware_modality /
    # transpilation_level columns; execution_context_basis echoes them.
    for _ek in ("execution_context", "execution_context_basis"):
        cols.append({"key": _ek, "header": _ek + " (derived)", "kind": "derived",
                     "allowed": None, "group": "Derived (auto-computed)",
                     "ftype": None, "multivalue": False})
    # Imaging-modality subcategorisation columns (computed by modality_axis.py via
    # compute_derived.py, stored in record["derived"]). Same rationale as the
    # paradigm / hardware blocks: allowed=None -> plain green text, no dropdown.
    for _mk in ("modality_subcategory", "modality_family", "modality_basis"):
        cols.append({"key": _mk, "header": _mk + " (derived)", "kind": "derived",
                     "allowed": None, "group": "Derived (auto-computed)",
                     "ftype": None, "multivalue": False})
    # Imaging-task axis (OSF v0.6 sec 7.6 Axis A): registered task_axis_a +
    # imaging_task_tags (authoritative), plus finer task_subcategory / task_family
    # alongside (imaging_task_axis.py).
    for _tk in ("task_axis_a", "imaging_task_tags", "task_subcategory",
                "task_family", "task_basis"):
        cols.append({"key": _tk, "header": _tk + " (derived)", "kind": "derived",
                     "allowed": None, "group": "Derived (auto-computed)",
                     "ftype": None, "multivalue": False})
    return cols


# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
HDR_FILL = PatternFill("solid", fgColor="1F4E78")
HDR_FONT = Font(bold=True, color="FFFFFF", name="Arial", size=10)
GRP_FILL = PatternFill("solid", fgColor="D6E4F0")
GRP_FONT = Font(bold=True, color="1F4E78", name="Arial", size=10)
ID_FILL = PatternFill("solid", fgColor="FFF2CC")
DERIVED_FILL = PatternFill("solid", fgColor="E2EFDA")
QUOTE_FILL = PatternFill("solid", fgColor="F2F2F2")
THIN = Side(style="thin", color="BBBBBB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
BASE_FONT = Font(name="Arial", size=10)


def _write_header(ws, cols):
    # Row 1: group banners; Row 2: column headers
    col_idx = 1
    # group spans
    group_runs = []
    last = None
    start = 1
    for i, c in enumerate(cols, 1):
        g = c["group"]
        if g != last:
            if last is not None:
                group_runs.append((last, start, i - 1))
            last = g
            start = i
    group_runs.append((last, start, len(cols)))

    for gname, c0, c1 in group_runs:
        cell = ws.cell(row=1, column=c0, value=gname)
        cell.fill = GRP_FILL
        cell.font = GRP_FONT
        cell.alignment = Alignment(horizontal="center")
        if c1 > c0:
            ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c1)

    for i, c in enumerate(cols, 1):
        cell = ws.cell(row=2, column=i, value=c["header"])
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BORDER
        letter = get_column_letter(i)
        if c["kind"] == "quote":
            ws.column_dimensions[letter].width = 38
        elif c["kind"] == "id":
            ws.column_dimensions[letter].width = 22
        else:
            ws.column_dimensions[letter].width = 18

    ws.freeze_panes = "A3"


# ----------------------------------------------------------------------------
# vocab sheet + data validations
# ----------------------------------------------------------------------------
def _build_vocab_sheet(wb, cols):
    vs = wb.create_sheet("vocab")
    vs.sheet_state = "hidden"
    # one column per enum field; first cell = field key, below = allowed values
    col = 1
    field_to_range = {}
    for c in cols:
        if c["kind"] in ("value", "derived", "human") and c.get("allowed"):
            letter = get_column_letter(col)
            vs.cell(row=1, column=col, value=c["key"])
            vals = list(c["allowed"])
            # for bools we handle separately; enums use their allowed list
            for r, v in enumerate(vals, start=2):
                vs.cell(row=r, column=col, value=v)
            # range excludes header row
            field_to_range[c["key"]] = "vocab!${L}$2:${L}${N}".format(L=letter, N=1 + len(vals))
            col += 1
    # a TRUE/FALSE column for booleans
    bl = get_column_letter(col)
    vs.cell(row=1, column=col, value="_bool")
    vs.cell(row=2, column=col, value="TRUE")
    vs.cell(row=3, column=col, value="FALSE")
    field_to_range["_bool"] = "vocab!${L}$2:${L}$3".format(L=bl)
    return field_to_range


def _apply_validations(ws, cols, field_to_range, n_data_rows):
    """Attach dropdowns to enum/bool value cells (rows 3..3+n-1, or a generous
    fixed span for the blank instrument)."""
    first = 3
    last = first + max(n_data_rows, 300) - 1  # generous span so humans can add rows
    for i, c in enumerate(cols, 1):
        letter = get_column_letter(i)
        rng = None
        if c["kind"] in ("value", "human") and c.get("allowed"):
            if c.get("multivalue"):
                # multi-enum: free-text (semicolon convention); no strict dropdown
                continue
            rng = field_to_range.get(c["key"])
        elif c["kind"] in ("value",) and c.get("ftype") == S.TYPE_BOOL:
            rng = field_to_range.get("_bool")
        if rng:
            dv = DataValidation(type="list", formula1="={}".format(rng), allow_blank=True)
            dv.error = "Pick a value from the list (or clear the cell)."
            dv.errorTitle = "Invalid value"
            dv.prompt = "Allowed: see vocab / README."
            ws.add_data_validation(dv)
            dv.add("{L}{a}:{L}{b}".format(L=letter, a=first, b=last))


# ----------------------------------------------------------------------------
# README
# ----------------------------------------------------------------------------
def _write_readme(wb, which):
    ws = wb.create_sheet("00_README", 0)
    ws.sheet_view.showGridLines = False
    lines = [
        ("Quantum Computing for Medical Imaging Applications — IEEE TRPMS systematic review", True),
        ("Step-4 full-text data extraction", True),
        ("", False),
        ("This workbook: {}".format(
            "HUMAN extraction instrument (blank, to be filled by a reviewer)"
            if which == "human" else
            "AI extraction anchor (auto-populated; independent of human extraction)"), False),
        ("", False),
        ("Conventions", True),
        ("- One row per paper. Identifier columns (yellow) are pre-filled; do not edit.", False),
        ("- Each field has a value column and a paired <field>_quote column.", False),
        ("- Paste the VERBATIM supporting sentence into the _quote column.", False),
        ("- Enum and TRUE/FALSE cells have dropdowns. Pick from the list.", False),
        ("- Multi-value fields: separate entries with a semicolon, e.g. 'amplitude; FRQI'.", False),
        ("- If a field is not reported in the paper, enter 'not_reported' (text), "
         "leave bool as FALSE, or leave blank per the field.", False),
        ("- Derived columns (green) are auto-computed downstream; leave blank in the human file.", False),
        ("", False),
        ("Independence (protocol §4.6)", True),
        ("- The human instrument contains NO AI values, to avoid anchoring.", False),
        ("- AI and human files share identical columns for cell-by-cell comparison in Step 5.", False),
        ("", False),
        ("Protocol references", True),
        ("- §9.1 bibliographic, §9.2 QC characterisation, §9.3 imaging, §9.4 validation,", False),
        ("  §9.5 translational maturity, §10 risk-of-bias / methodological quality.", False),
        ("- Grades use the exact protocol vocabularies (see the 'vocab' sheet).", False),
        ("- clinical_impact_assessment is a HUMAN judgement (low/moderate/high) with justification;", False),
        ("  it is intentionally absent from the AI file.", False),
    ]
    for r, (text, bold) in enumerate(lines, 1):
        cell = ws.cell(row=r, column=1, value=text)
        cell.font = Font(name="Arial", size=11, bold=bold,
                         color="1F4E78" if bold else "000000")
    ws.column_dimensions["A"].width = 110


# ----------------------------------------------------------------------------
# Value formatting for AI export
# ----------------------------------------------------------------------------
def _fmt_value(entry):
    """Return (value_str, quote_str) from a field entry (scalar or list)."""
    if entry is None:
        return "", ""
    if isinstance(entry, list):
        vals = []
        quotes = []
        for it in entry:
            if isinstance(it, dict):
                v = it.get("value")
                vals.append(_scalar_to_str(v))
                if it.get("quote"):
                    quotes.append(str(it.get("quote")))
            else:
                vals.append(_scalar_to_str(it))
        return "; ".join([v for v in vals if v != ""]), " | ".join(quotes)
    if isinstance(entry, dict):
        return _scalar_to_str(entry.get("value")), (entry.get("quote") or "")
    return _scalar_to_str(entry), ""


def _scalar_to_str(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, dict):
        # author object etc.
        if "family" in v or "given" in v:
            return "{}, {}".format(v.get("family", ""), v.get("given", "")).strip(", ")
        if "metric_name" in v:
            return "{}={}".format(v.get("metric_name", ""), v.get("value", ""))
        return json.dumps(v, ensure_ascii=False)
    return str(v)


# ----------------------------------------------------------------------------
# Builders
# ----------------------------------------------------------------------------
def _load_identifier_rows():
    """Pull doi/title/stable_name/pdf_file/year for every paper from the
    chunk plans + text index. Used for BOTH files so rows align."""
    rows = []
    # prefer derived/merged if present (gives full set); else chunk plans
    import csv
    title_by_stable = {}
    # titles live in Pass A merged inputs if available
    mi = P.PROCESSED_DIR / "merged_inputs"
    # fallback: read chunk plans for stable+doi
    for f in sorted(P.CHUNKS_DIR.glob("*.json")):
        plan = json.loads(f.read_text(encoding="utf-8"))
        stable = plan["stable_name"]
        doi = plan.get("doi", "")
        title = ""
        af = mi / (stable + ".A.json")
        if af.exists():
            arec = json.loads(af.read_text(encoding="utf-8"))
            t = arec.get("title")
            if isinstance(t, dict):
                title = t.get("value") or ""
        rows.append({"doi": doi, "title": title, "stable_name": stable,
                     "pdf_file": stable + ".pdf", "year": ""})
    return rows


def build_workbook(cols, populate=None, which="human"):
    """populate: None for blank; else dict stable_name -> derived record."""
    wb = Workbook()
    wb.remove(wb.active)
    ws = wb.create_sheet("extractions")
    _write_header(ws, cols)

    rows = _load_identifier_rows()

    r = 3
    for row in rows:
        stable = row["stable_name"]
        rec = populate.get(stable) if populate else None
        for i, c in enumerate(cols, 1):
            cell = ws.cell(row=r, column=i)
            cell.font = BASE_FONT
            cell.border = BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=(c["kind"] == "quote"))
            if c["kind"] == "id":
                cell.value = row.get(c["key"], "")
                cell.fill = ID_FILL
            elif c["kind"] == "quote":
                cell.fill = QUOTE_FILL
                if rec is not None:
                    pass  # quotes filled alongside values below
            elif c["kind"] == "derived":
                cell.fill = DERIVED_FILL
                if rec is not None:
                    d = rec.get("derived", {}).get(c["key"], {})
                    cell.value = _scalar_to_str(d.get("value") if isinstance(d, dict) else d)
            elif c["kind"] in ("value", "human"):
                if rec is not None and c["kind"] == "value":
                    # find which pass owns this field
                    val_str, quote_str = "", ""
                    for pl in ("A", "B", "C", "D"):
                        prec = rec.get("passes", {}).get(pl, {})
                        if c["key"] in prec:
                            val_str, quote_str = _fmt_value(prec[c["key"]])
                            break
                    cell.value = val_str
                    # write paired quote into the next column if it is the quote col
                    if i < len(cols) and cols[i]["key"] == c["key"] + "_quote":
                        qcell = ws.cell(row=r, column=i + 1)
                        qcell.value = quote_str
                        qcell.font = BASE_FONT
                        qcell.border = BORDER
                        qcell.fill = QUOTE_FILL
                        qcell.alignment = Alignment(vertical="top", wrap_text=True)
        r += 1

    n_rows = len(rows)
    field_to_range = _build_vocab_sheet(wb, cols)
    _apply_validations(ws, cols, field_to_range, n_rows)
    _write_readme(wb, which)

    # AI file: audit_flags sheet
    if populate is not None:
        _write_audit_sheet(wb, cols, populate, rows)

    return wb


def _write_audit_sheet(wb, cols, populate, rows):
    ws = wb.create_sheet("audit_flags")
    headers = ["stable_name", "doi", "n_chunks_any", "rescued_any",
               "grade_disagreement", "boolean_nonunanimity", "quote_unverified",
               "image_only", "reconciliation_contradictions"]
    for i, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=i, value=h)
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
    r = 2
    for row in rows:
        rec = populate.get(row["stable_name"])
        if not rec:
            continue
        flags = _scan_flags(rec)
        ws.cell(row=r, column=1, value=row["stable_name"])
        ws.cell(row=r, column=2, value=row["doi"])
        ws.cell(row=r, column=3, value=flags["n_chunks_any"])
        ws.cell(row=r, column=4, value=flags["rescued_any"])
        ws.cell(row=r, column=5, value=flags["grade_disagreement"])
        ws.cell(row=r, column=6, value=flags["boolean_nonunanimity"])
        ws.cell(row=r, column=7, value=flags["quote_unverified"])
        ws.cell(row=r, column=8, value=flags["image_only"])
        ws.cell(row=r, column=9, value=flags["contradictions"])
        r += 1
    for i in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 22
    ws.column_dimensions[get_column_letter(9)].width = 60
    ws.freeze_panes = "A2"


def _scan_flags(rec):
    out = {"n_chunks_any": 0, "rescued_any": False, "grade_disagreement": "",
           "boolean_nonunanimity": "", "quote_unverified": "", "image_only": "",
           "contradictions": ""}
    gd, bn, qu, io = [], [], [], []
    for pl, prec in rec.get("passes", {}).items():
        meta = prec.get("_meta", {})
        out["n_chunks_any"] = max(out["n_chunks_any"], meta.get("n_chunks", 1) if isinstance(meta.get("n_chunks", 1), int) else 1)
        if meta.get("_rescued"):
            out["rescued_any"] = True
        for k, v in prec.items():
            entries = v if isinstance(v, list) else [v]
            for e in entries:
                if not isinstance(e, dict):
                    continue
                if e.get("_grade_disagreement"):
                    gd.append("{}.{}".format(pl, k))
                if e.get("_boolean_unanimity") is False:
                    bn.append("{}.{}".format(pl, k))
                if e.get("_quote_unverified"):
                    qu.append("{}.{}".format(pl, k))
                if e.get("_image_only"):
                    io.append("{}.{}".format(pl, k))
    out["grade_disagreement"] = "; ".join(gd)
    out["boolean_nonunanimity"] = "; ".join(bn)
    out["quote_unverified"] = "; ".join(qu)
    out["image_only"] = "; ".join(io)
    E = rec.get("passes", {}).get("E", {})
    contras = E.get("contradictions", []) if isinstance(E, dict) else []
    out["contradictions"] = " || ".join(
        "[{}] {} vs {}: {}".format(c.get("severity"), c.get("field_a"),
                                   c.get("field_b"), c.get("note"))
        for c in contras if isinstance(c, dict))
    return out


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--human", action="store_true", help="Build blank human instrument only.")
    parser.add_argument("--ai", action="store_true", help="Build AI anchor only.")
    args = parser.parse_args()
    P.ensure_dirs()

    build_both = not (args.human or args.ai)
    cols = build_column_spec()

    if args.human or build_both:
        wb = build_workbook(cols, populate=None, which="human")
        out = P.EXTRACT_DIR / "extraction_human_blank.xlsx"
        wb.save(str(out))
        print("Wrote human instrument:", out)

    if args.ai or build_both:
        populate = {}
        if P.DERIVED_EXTRACTIONS.exists():
            with open(P.DERIVED_EXTRACTIONS, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        rec = json.loads(line)
                        populate[rec["stable_name"]] = rec
        else:
            print("WARNING: {} not found; AI file will be structurally complete but empty."
                  .format(P.DERIVED_EXTRACTIONS))
        wb = build_workbook(cols, populate=populate, which="ai")
        out = P.EXTRACTIONS_XLSX
        wb.save(str(out))
        print("Wrote AI anchor:", out, "({} papers populated)".format(len(populate)))

    return 0


if __name__ == "__main__":
    sys.exit(main())
