"""
ocr_pages.py - Per-page 300-DPI OCR cache for the Step-4 OCR-augmentation
re-run (Phase 1 of the B/C quote-grounding fix).

Why this exists
---------------
The production grounding text (PDFs_step4_text/<stable>.txt) is pdftotext
output only. Results tables, comparison charts, two-column abstract panels,
and code/data-availability boxes that pdftotext drops were rendered to page
images and shown to the subagent, but their text was NEVER added to the
groundable TEXT. The B/C subagents therefore could not quote those facts and
escaped via `_image_only: true, quote: null`, which the validator accepts.
The result was ~0 quote coverage on Pass B and Pass C.

This script OCRs every page of every PDF at OCR_DPI (300) and caches the
per-page OCR text. build_augmented_text.py then interleaves this OCR text
with the original pdftotext page text so a verbatim substring exists for
facts that were previously image-only.

Output
------
PDFs_step4_ocr/<stable_name>/page_001.txt ... page_NNN.txt
PDFs_step4_ocr/<stable_name>/ocr_meta.json   {stable_name, source_pdf,
                                              dpi, page_count, seconds, note}
ocr_pages_index.csv                          one row per paper (status)

Idempotent: a paper whose ocr_meta.json reports status=ok and the expected
page count is skipped unless --force.

Usage
-----
  python3 ocr_pages.py                 # OCR every paper in pdf_text_index.csv
  python3 ocr_pages.py --limit 5       # smoke test on 5 papers
  python3 ocr_pages.py --only 10_1002_ima_23015,10_1002_jemt_24054
  python3 ocr_pages.py --force         # re-OCR even if cache exists
  python3 ocr_pages.py --dpi 300       # override DPI (default OCR_DPI)
  python3 ocr_pages.py --dry-run       # list what would be OCR'd

Python 3.8 compatible (no PEP-604/585, no @dataclass, no future annotations).
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import paths_step4 as P


# ----------------------------------------------------------------------------
# Output locations (derived from paths_step4 conventions)
# ----------------------------------------------------------------------------
OCR_DIR = P.EXTRACT_DIR / "PDFs_step4_ocr"
OCR_INDEX = P.EXTRACT_DIR / "ocr_pages_index.csv"


# ----------------------------------------------------------------------------
# Index reader
# ----------------------------------------------------------------------------
def load_text_index():
    """Return list of dict rows from pdf_text_index.csv."""
    if not P.PDF_TEXT_INDEX.exists():
        print("ERROR: missing", P.PDF_TEXT_INDEX, file=sys.stderr)
        return []
    rows = []
    with open(P.PDF_TEXT_INDEX, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows


# ----------------------------------------------------------------------------
# OCR runner (per page)
# ----------------------------------------------------------------------------
def ocr_pdf_pages(pdf_path, dpi):
    """OCR every page of pdf_path at `dpi`.

    Returns (list_of_page_text, note). list_of_page_text[i] is the OCR text
    of page i+1. On import/convert failure returns ([], "<reason>").

    Imported lazily so a host without the OCR stack still runs --dry-run and
    the index reader.
    """
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except Exception as exc:  # pragma: no cover - env dependent
        return [], "ocr_stack_unavailable: {}".format(exc)

    try:
        images = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as exc:
        return [], "pdf2image_failed: {}".format(exc)

    out = []
    for img in images:
        try:
            txt = pytesseract.image_to_string(img, lang="eng")
        except Exception as exc:
            txt = ""
            out.append(txt)
            return out, "pytesseract_failed_page_{}: {}".format(len(out), exc)
        out.append(txt if txt is not None else "")
    return out, ""


# ----------------------------------------------------------------------------
# Cache state
# ----------------------------------------------------------------------------
def cache_is_complete(stable, expected_pages):
    """True iff ocr_meta.json reports ok and page files exist."""
    meta_path = OCR_DIR / stable / "ocr_meta.json"
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if meta.get("status") != "ok":
        return False
    n = int(meta.get("page_count", 0))
    if expected_pages and n != expected_pages:
        return False
    for i in range(1, n + 1):
        if not (OCR_DIR / stable / "page_{:03d}.txt".format(i)).exists():
            return False
    return True


def write_cache(stable, source_pdf, dpi, page_texts, note):
    """Write per-page OCR text + ocr_meta.json. Returns the meta dict."""
    out_dir = OCR_DIR / stable
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, txt in enumerate(page_texts, 1):
        (out_dir / "page_{:03d}.txt".format(i)).write_text(
            txt, encoding="utf-8")
    status = "ok" if page_texts else "failed"
    meta = {
        "stable_name": stable,
        "source_pdf": str(source_pdf),
        "dpi": dpi,
        "page_count": len(page_texts),
        "note": note,
        "status": status,
    }
    (out_dir / "ocr_meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Per-page OCR cache for B/C refill.")
    ap.add_argument("--limit", type=int, default=0,
                    help="OCR at most N papers (smoke test).")
    ap.add_argument("--only", default="",
                    help="Comma-separated stable_names to OCR.")
    ap.add_argument("--force", action="store_true",
                    help="Re-OCR even if a complete cache exists.")
    ap.add_argument("--dpi", type=int, default=P.OCR_DPI,
                    help="OCR DPI (default OCR_DPI={}).".format(P.OCR_DPI))
    ap.add_argument("--dry-run", action="store_true",
                    help="List what would be OCR'd; do nothing.")
    args = ap.parse_args()

    rows = load_text_index()
    if not rows:
        return 2

    only = set(s.strip() for s in args.only.split(",") if s.strip())
    if only:
        rows = [r for r in rows if r.get("stable_name") in only]
    if args.limit:
        rows = rows[: args.limit]

    OCR_DIR.mkdir(parents=True, exist_ok=True)

    index_rows = []
    n_ok = n_skip = n_fail = 0
    t_start = time.time()

    for idx, r in enumerate(rows, 1):
        stable = r.get("stable_name", "")
        source_pdf = r.get("source_pdf", "")
        try:
            expected_pages = int(r.get("page_count", 0) or 0)
        except ValueError:
            expected_pages = 0

        if not stable or not source_pdf:
            n_fail += 1
            index_rows.append([stable, source_pdf, 0, "skipped_no_pdf_or_stable"])
            continue

        if not args.force and cache_is_complete(stable, expected_pages):
            n_skip += 1
            index_rows.append([stable, source_pdf, expected_pages, "cached"])
            print("[{:3d}/{:3d}] {:<32s} cached".format(idx, len(rows), stable))
            continue

        if args.dry_run:
            print("[{:3d}/{:3d}] {:<32s} would OCR ({}p) at {} DPI".format(
                idx, len(rows), stable, expected_pages, args.dpi))
            continue

        t0 = time.time()
        page_texts, note = ocr_pdf_pages(Path(source_pdf), args.dpi)
        if not page_texts:
            n_fail += 1
            write_cache(stable, source_pdf, args.dpi, [], note or "no_pages")
            index_rows.append([stable, source_pdf, 0, note or "failed"])
            print("[{:3d}/{:3d}] {:<32s} FAIL  {}".format(
                idx, len(rows), stable, note))
            continue

        write_cache(stable, source_pdf, args.dpi, page_texts, note)
        n_ok += 1
        dt = time.time() - t0
        index_rows.append([stable, source_pdf, len(page_texts), note or "ok"])
        print("[{:3d}/{:3d}] {:<32s} ok  {:>3d}p  {:.1f}s".format(
            idx, len(rows), stable, len(page_texts), dt))

    if not args.dry_run:
        with open(OCR_INDEX, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["stable_name", "source_pdf", "ocr_pages", "note"])
            w.writerows(index_rows)

    print("\nDone. ok={} cached={} fail={}  wall={:.1f}s".format(
        n_ok, n_skip, n_fail, time.time() - t_start))
    print("OCR cache:", OCR_DIR)
    if not args.dry_run:
        print("Index:", OCR_INDEX)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
