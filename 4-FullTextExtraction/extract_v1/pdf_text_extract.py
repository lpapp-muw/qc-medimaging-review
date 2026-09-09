"""
pdf_text_extract.py — Phase 1.1 PDF-to-text pipeline for Step-4 extraction.

For each PDF in PDFS_STEP4:
  1. pdftotext -layout (preserves column structure for most journal layouts)
     If the output is >= PDFTOTEXT_MIN_BYTES, accept and stop.
  2. pdftotext (no -layout; collapses two-column to reading order)
     If accepted, flag extraction_mode='no_layout'.
  3. OCR fallback via pdf2image + pytesseract at OCR_DPI (300 DPI).
     Flag _ocr_source=True.

Outputs:
  - PDFs_step4_text/<stable_name>.txt per PDF (UTF-8)
  - pdf_text_index.csv with: doi, stable_name, text_path, source_pdf,
    extraction_mode, char_count, page_count, ocr_source, status, notes

Stable filenames:
  Derived deterministically from the source PDF's basename (without
  extension), ASCII-folded, with non-alphanumeric runs collapsed to '_',
  truncated to 120 chars. Sortable, debuggable, no DOI lookup required at
  this stage. The DOI is resolved from the candidates workbook in a separate
  pass (see Notes below).

Python 3.8 compatible.

Usage:
  python3 pdf_text_extract.py                 # process all PDFs
  python3 pdf_text_extract.py --limit 5       # smoke test (first 5)
  python3 pdf_text_extract.py --pdf NAME.pdf  # single file
  python3 pdf_text_extract.py --force         # reprocess even if text exists
  python3 pdf_text_extract.py --dry-run       # list what would be done

Notes on DOI resolution:
  The PDF folder PDFs_step4/ uses DOI-derived filenames per the consolidation
  step in 4-FullTextExtraction. The CSV records the source basename and a
  doi field populated by mapping basename -> doi from step4_candidates_v2.xlsx
  if the workbook is reachable. If the workbook is not reachable or the
  basename does not map, doi is left empty and the script logs a warning;
  the text extraction is unaffected.
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import unicodedata
from pathlib import Path

import paths_step4 as P


# ----------------------------------------------------------------------------
# Filename helpers
# ----------------------------------------------------------------------------
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def stable_name_from_pdf(pdf_path):
    """Deterministic ASCII-folded stem for use as a text-file basename.

    Input  : Path object pointing to a PDF.
    Output : string, e.g. '10_1109_trpms_2024_3388872'.

    Folding rules:
      - NFKD normalise, strip combining marks (ASCII fold)
      - Lowercase
      - Replace any non-alphanumeric run with single '_'
      - Trim leading/trailing '_'
      - Truncate to 120 chars
    """
    stem = pdf_path.stem
    stem = unicodedata.normalize("NFKD", stem)
    stem = stem.encode("ascii", "ignore").decode("ascii")
    stem = stem.lower()
    stem = _NON_ALNUM_RE.sub("_", stem).strip("_")
    return stem[:120] if stem else "untitled"


# ----------------------------------------------------------------------------
# pdftotext / OCR runners
# ----------------------------------------------------------------------------
def _run_pdftotext(pdf_path, layout):
    """Run pdftotext and return (stdout_text, returncode, stderr_text).

    layout=True   -> uses -layout flag (preserves columns)
    layout=False  -> default reading-order (collapses columns)
    """
    cmd = ["pdftotext"]
    if layout:
        cmd.append("-layout")
    cmd += [str(pdf_path), "-"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,  # generous; large papers can take ~30s
        )
    except subprocess.TimeoutExpired:
        return "", 124, "pdftotext timeout"
    text = proc.stdout.decode("utf-8", errors="replace")
    err = proc.stderr.decode("utf-8", errors="replace")
    return text, proc.returncode, err


def _pdf_page_count(pdf_path):
    """Return the page count via pdfinfo, or -1 if unavailable."""
    try:
        proc = subprocess.run(
            ["pdfinfo", str(pdf_path)],
            capture_output=True,
            timeout=30,
        )
        out = proc.stdout.decode("utf-8", errors="replace")
        for line in out.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return -1


def _ocr_pdf(pdf_path):
    """Run OCR over every page at OCR_DPI. Returns (text, page_count, note)."""
    # Imported lazily so a missing OCR dependency does not block the
    # primary pdftotext path on systems where OCR is never needed.
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError as exc:
        return "", 0, "ocr_dependency_missing: {}".format(exc)

    try:
        pages = convert_from_path(str(pdf_path), dpi=P.OCR_DPI)
    except Exception as exc:
        return "", 0, "pdf2image_failed: {}".format(exc)

    parts = []
    for i, img in enumerate(pages, 1):
        try:
            txt = pytesseract.image_to_string(img, lang="eng")
        except Exception as exc:
            txt = ""
            parts.append("\n[OCR_ERROR page {} : {}]\n".format(i, exc))
            continue
        parts.append("\n[PAGE {}]\n".format(i))
        parts.append(txt)
    return "".join(parts), len(pages), ""


# ----------------------------------------------------------------------------
# DOI lookup (best-effort)
# ----------------------------------------------------------------------------
def _build_doi_map():
    """Read step4_candidates_v2.xlsx (sheet all_candidates) and return a dict
    mapping stable_name -> doi where the source PDF basename can be inferred
    from the workbook's file_name column.

    If openpyxl is unavailable or the workbook cannot be opened, return {}.
    The script continues without DOI tagging; warning logged.
    """
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        return {}, "openpyxl_missing"

    xlsx_path = P.CANDIDATES_XLSX
    if not xlsx_path.exists():
        return {}, "candidates_xlsx_missing"

    try:
        from openpyxl import load_workbook
        wb = load_workbook(str(xlsx_path), data_only=True, read_only=True)
    except Exception as exc:
        return {}, "openpyxl_open_failed: {}".format(exc)

    # Try to find a sheet that has both a file/path column and a doi column.
    candidate_sheets = ["all_candidates", "pdfs_to_retrieve"]
    doi_map = {}
    for sheet_name in candidate_sheets:
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            continue
        header_lc = [str(h).lower() if h is not None else "" for h in header]
        # Find columns
        doi_idx = None
        fname_idx = None
        for i, h in enumerate(header_lc):
            if h == "doi":
                doi_idx = i
            if h in ("pdf_filename", "filename", "file_name", "pdf_path"):
                fname_idx = i
        if doi_idx is None:
            continue
        for r in rows:
            if r is None:
                continue
            doi_val = r[doi_idx] if doi_idx < len(r) else None
            fname_val = r[fname_idx] if (fname_idx is not None and fname_idx < len(r)) else None
            if not doi_val:
                continue
            doi_str = str(doi_val).strip().lower()
            # Map by DOI-derived stable name (workbook may store filename
            # directly or only the DOI itself)
            if fname_val:
                key = stable_name_from_pdf(Path(str(fname_val)))
                doi_map[key] = doi_str
            # Always map by DOI-derived stable too (fallback if file basename
            # happens to match the DOI shape)
            doi_stable = _NON_ALNUM_RE.sub("_", doi_str).strip("_")[:120]
            doi_map.setdefault(doi_stable, doi_str)
    wb.close()
    return doi_map, ""


# ----------------------------------------------------------------------------
# Per-PDF processing
# ----------------------------------------------------------------------------
def process_one_pdf(pdf_path, doi_map, force=False):
    """Extract text from a single PDF. Returns a dict row for the CSV index.

    Strategy:
      1. pdftotext -layout
      2. pdftotext (no layout)
      3. OCR

    Acceptance threshold: char_count >= PDFTOTEXT_MIN_BYTES from layout pass.
    If layout pass is below threshold, no-layout pass runs. If that is also
    below threshold, OCR runs. The mode chosen is whichever pass yielded the
    longest text; OCR only chosen when layout & no-layout both undershoot.

    A note: the threshold logic differs subtly from a naive "use layout
    unless empty". Some scanned PDFs return a tiny garbled string from
    pdftotext (page-numbers only); we treat anything below 2KB as "needs
    OCR" rather than "extraction succeeded".
    """
    stable = stable_name_from_pdf(pdf_path)
    out_path = P.PDF_TEXT_DIR / (stable + ".txt")
    row = {
        "doi": doi_map.get(stable, ""),
        "stable_name": stable,
        "text_path": str(out_path),
        "source_pdf": str(pdf_path),
        "extraction_mode": "",
        "char_count": 0,
        "page_count": -1,
        "ocr_source": False,
        "status": "",
        "notes": "",
    }

    if out_path.exists() and not force:
        try:
            existing_text = out_path.read_text(encoding="utf-8", errors="replace")
            row["char_count"] = len(existing_text)
            row["status"] = "cached"
            row["extraction_mode"] = "cached"
            row["page_count"] = _pdf_page_count(pdf_path)
            return row
        except Exception:
            pass  # fall through and reprocess

    row["page_count"] = _pdf_page_count(pdf_path)

    # Pass 1: pdftotext -layout
    text_layout, rc_layout, err_layout = _run_pdftotext(pdf_path, layout=True)
    len_layout = len(text_layout)

    if rc_layout == 0 and len_layout >= P.PDFTOTEXT_MIN_BYTES:
        chosen_text = text_layout
        row["extraction_mode"] = "layout"
        row["char_count"] = len_layout
        row["status"] = "ok"
    else:
        # Pass 2: pdftotext no-layout
        text_nl, rc_nl, err_nl = _run_pdftotext(pdf_path, layout=False)
        len_nl = len(text_nl)

        if rc_nl == 0 and len_nl >= P.PDFTOTEXT_MIN_BYTES:
            chosen_text = text_nl
            row["extraction_mode"] = "no_layout"
            row["char_count"] = len_nl
            row["status"] = "ok"
        elif max(len_layout, len_nl) >= P.PDFTOTEXT_MIN_BYTES:
            # Both passes ran but neither was clearly "good"; pick longer.
            if len_layout >= len_nl:
                chosen_text = text_layout
                row["extraction_mode"] = "layout_short"
            else:
                chosen_text = text_nl
                row["extraction_mode"] = "no_layout_short"
            row["char_count"] = len(chosen_text)
            row["status"] = "ok_short"
            row["notes"] = "both pdftotext modes below threshold; took longer"
        else:
            # Pass 3: OCR
            text_ocr, n_pages_ocr, ocr_note = _ocr_pdf(pdf_path)
            if text_ocr.strip():
                chosen_text = text_ocr
                row["extraction_mode"] = "ocr"
                row["char_count"] = len(text_ocr)
                row["ocr_source"] = True
                row["status"] = "ok_ocr"
                row["notes"] = ocr_note if ocr_note else "ocr {} pages".format(n_pages_ocr)
            else:
                chosen_text = ""
                row["extraction_mode"] = "failed"
                row["status"] = "failed"
                row["notes"] = "; ".join([
                    "layout_rc={} err={}".format(rc_layout, (err_layout or "").strip()[:100]),
                    "no_layout_rc={} err={}".format(rc_nl, (err_nl or "").strip()[:100]),
                    "ocr={}".format((ocr_note or "empty_ocr")[:100]),
                ])

    if chosen_text:
        out_path.write_text(chosen_text, encoding="utf-8")
    return row


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N PDFs (smoke test).")
    parser.add_argument("--pdf", type=str, default="",
                        help="Process a single PDF by filename (relative to PDFs_step4 or absolute).")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess even if text file already exists.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be processed and stop.")
    args = parser.parse_args()

    P.ensure_dirs()

    # Collect PDFs
    if args.pdf:
        candidate = Path(args.pdf)
        if not candidate.is_absolute():
            candidate = P.PDFS_STEP4 / candidate
        if not candidate.exists():
            print("ERROR: PDF not found:", candidate, file=sys.stderr)
            return 2
        pdfs = [candidate]
    else:
        if not P.PDFS_STEP4.exists():
            print("ERROR: PDFs_step4 missing at", P.PDFS_STEP4, file=sys.stderr)
            return 2
        pdfs = sorted(P.PDFS_STEP4.glob("*.pdf"))
        if not pdfs:
            print("ERROR: no PDFs found in", P.PDFS_STEP4, file=sys.stderr)
            return 2
    if args.limit and args.limit > 0:
        pdfs = pdfs[: args.limit]

    print("Found {} PDF(s) to process.".format(len(pdfs)))
    if args.dry_run:
        for p in pdfs:
            print(" -", p.name, "->", stable_name_from_pdf(p) + ".txt")
        return 0

    # Tool presence check
    for tool in ("pdftotext", "pdfinfo"):
        if shutil.which(tool) is None:
            print("ERROR: required tool '{}' not on PATH".format(tool), file=sys.stderr)
            return 2

    # DOI mapping (best-effort)
    doi_map, doi_note = _build_doi_map()
    if doi_note:
        print("DOI mapping: degraded ({}); CSV doi column may be empty.".format(doi_note))
    else:
        print("DOI mapping: {} entries loaded.".format(len(doi_map)))

    # Process
    rows = []
    t0 = time.time()
    for i, pdf in enumerate(pdfs, 1):
        t_pdf = time.time()
        try:
            row = process_one_pdf(pdf, doi_map, force=args.force)
        except Exception:
            row = {
                "doi": doi_map.get(stable_name_from_pdf(pdf), ""),
                "stable_name": stable_name_from_pdf(pdf),
                "text_path": "",
                "source_pdf": str(pdf),
                "extraction_mode": "exception",
                "char_count": 0,
                "page_count": -1,
                "ocr_source": False,
                "status": "exception",
                "notes": traceback.format_exc().splitlines()[-1][:200],
            }
        dt = time.time() - t_pdf
        rows.append(row)
        print("[{:3d}/{:3d}] {:<30s} mode={:<14s} chars={:>7d} pages={:>3d} ocr={} {:.1f}s {}".format(
            i, len(pdfs),
            pdf.name[:30],
            row["extraction_mode"],
            row["char_count"],
            row["page_count"],
            "Y" if row["ocr_source"] else "n",
            dt,
            row["status"],
        ))

    # Write CSV index (always overwrite to reflect the latest run)
    fieldnames = [
        "doi", "stable_name", "text_path", "source_pdf", "extraction_mode",
        "char_count", "page_count", "ocr_source", "status", "notes",
    ]
    with open(P.PDF_TEXT_INDEX, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    # Summary
    n_ok = sum(1 for r in rows if r["status"] in ("ok", "ok_short", "ok_ocr", "cached"))
    n_ocr = sum(1 for r in rows if r["ocr_source"])
    n_failed = sum(1 for r in rows if r["status"] in ("failed", "exception"))
    total_chars = sum(r["char_count"] for r in rows)
    print()
    print("Done. {} ok, {} ocr-rescued, {} failed. Total chars: {:,}. Wall time {:.1f}s".format(
        n_ok, n_ocr, n_failed, total_chars, time.time() - t0
    ))
    print("Index written to:", P.PDF_TEXT_INDEX)
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
