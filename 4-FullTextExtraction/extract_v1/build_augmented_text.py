"""
build_augmented_text.py - Build the OCR-augmented grounding corpus for the
Step-4 B/C re-run (Phase 1, after ocr_pages.py).

What it does
------------
For each paper it interleaves, page by page:

    <original pdftotext text of page N>
    [OCR-PAGE-N]
    <300-DPI OCR text of page N, with lines already present in the pdftotext
     page dropped to limit duplication>

Pages are separated by FORM_FEED (\\x0c) exactly as pdftotext does, so the
SAME offset logic (pdf_page_render.compute_page_offsets) recovers per-page
char ranges over the augmented text.

It writes, isolated from production (production .txt and page_offsets.json
are NOT touched):

    PDFs_step4_text_aug/<stable>.txt
    PDFs_step4_images/<stable>/page_offsets_aug.json   (same schema as
        page_offsets.json: pages = [{page, char_start, char_end, image}])
    augmented_text_index.csv

Why isolated: Passes A, D, E already validated and merged against the
original text. We do not re-validate them, so their source must remain
unchanged. Only the B/C refill consumes the augmented corpus.

Inputs
------
  pdf_text_index.csv                       (original text + page_count)
  PDFs_step4_images/<stable>/page_offsets.json   (original per-page ranges)
  PDFs_step4_ocr/<stable>/page_NNN.txt     (from ocr_pages.py)

Usage
-----
  python3 build_augmented_text.py
  python3 build_augmented_text.py --only 10_1002_ima_23015
  python3 build_augmented_text.py --limit 5
  python3 build_augmented_text.py --no-dedup    # keep all OCR lines verbatim
  python3 build_augmented_text.py --force

Python 3.8 compatible.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import paths_step4 as P
from pdf_page_render import compute_page_offsets


FORM_FEED = "\x0c"

AUG_TEXT_DIR = P.EXTRACT_DIR / "PDFs_step4_text_aug"
IMAGES_ROOT = P.PDF_TEXT_DIR.parent / "PDFs_step4_images"
OCR_DIR = P.EXTRACT_DIR / "PDFs_step4_ocr"
AUG_INDEX = P.EXTRACT_DIR / "augmented_text_index.csv"

# Offsets schema name (kept distinct from production page_offsets.json)
OFFSETS_AUG_NAME = "page_offsets_aug.json"


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------
def load_text_index():
    rows = []
    if not P.PDF_TEXT_INDEX.exists():
        print("ERROR: missing", P.PDF_TEXT_INDEX, file=sys.stderr)
        return rows
    with open(P.PDF_TEXT_INDEX, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows


def load_original_pages(stable):
    """Return the original page_offsets pages list, or None."""
    path = IMAGES_ROOT / stable / "page_offsets.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload.get("pages")


def load_ocr_page(stable, page_no):
    """Return OCR text for a 1-based page, or '' if absent."""
    path = OCR_DIR / stable / "page_{:03d}.txt".format(page_no)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


# ----------------------------------------------------------------------------
# Dedup: drop OCR lines that already appear (normalised) in the pdftotext page
# ----------------------------------------------------------------------------
def _norm_line(s):
    return " ".join(s.lower().split())


def dedup_ocr_against_text(orig_page_text, ocr_page_text):
    """Return OCR text with lines already present in orig_page_text removed.

    Conservative: only drops an OCR line if its normalised form exactly
    matches a normalised line already in the pdftotext page. Keeps everything
    pdftotext missed (tables, figure captions, two-column panels). This bounds
    token inflation without discarding any genuinely-new groundable text.
    """
    have = set()
    for ln in orig_page_text.splitlines():
        n = _norm_line(ln)
        if n:
            have.add(n)
    kept = []
    for ln in ocr_page_text.splitlines():
        n = _norm_line(ln)
        if not n:
            continue
        if n in have:
            continue
        kept.append(ln.rstrip())
    return "\n".join(kept)


# ----------------------------------------------------------------------------
# Build one paper
# ----------------------------------------------------------------------------
def build_paper(stable, dedup=True):
    """Build augmented text + offsets for one paper.

    Returns (status, note, n_pages, n_chars). Writes files on success.
    """
    orig_txt_path = P.PDF_TEXT_DIR / (stable + ".txt")
    if not orig_txt_path.exists():
        return "no_text", "missing original .txt", 0, 0

    orig_text = orig_txt_path.read_text(encoding="utf-8", errors="replace")
    pages = load_original_pages(stable)
    if not pages:
        # No page map: fall back to a single page = whole text + whole-doc OCR.
        ocr_all = load_ocr_page(stable, 1)
        body = orig_text
        ocr_block = dedup_ocr_against_text(orig_text, ocr_all) if dedup else ocr_all
        if ocr_block.strip():
            body = body + "\n[OCR-PAGE-1]\n" + ocr_block
        aug_text = body
        note = "no_page_offsets; single-block augmentation"
    else:
        parts = []
        for p in pages:
            n = p["page"]
            cs = p.get("char_start", 0)
            ce = p.get("char_end", len(orig_text))
            orig_page = orig_text[cs:ce]
            ocr_page = load_ocr_page(stable, n)
            ocr_block = (dedup_ocr_against_text(orig_page, ocr_page)
                         if dedup else ocr_page)
            page_body = orig_page
            if ocr_block.strip():
                # Newline guard so the OCR marker starts on its own line.
                if not page_body.endswith("\n"):
                    page_body = page_body + "\n"
                page_body = page_body + "[OCR-PAGE-{}]\n".format(n) + ocr_block
            parts.append(page_body)
        # Re-insert FORM_FEED between pages so compute_page_offsets works.
        aug_text = FORM_FEED.join(parts)
        note = "ok"

    # Write augmented text
    AUG_TEXT_DIR.mkdir(parents=True, exist_ok=True)
    aug_txt_path = AUG_TEXT_DIR / (stable + ".txt")
    aug_txt_path.write_text(aug_text, encoding="utf-8")

    # Recompute offsets over the augmented text
    aug_pages, delimiter = compute_page_offsets(aug_text)
    if not aug_pages:
        # Single page covering the whole augmented text
        aug_pages = [{"page": 1, "char_start": 0, "char_end": len(aug_text)}]
        delimiter = "single_block"

    # Re-attach image filenames from the original page map (page->image),
    # so the refill chunker can pair text chunks with page images.
    image_lookup = {}
    if pages:
        for p in pages:
            image_lookup[p["page"]] = p.get("image", "")
    for ap in aug_pages:
        ap["image"] = image_lookup.get(ap["page"], "")

    offsets_payload = {
        "stable_name": stable,
        "text_path": str(aug_txt_path),
        "page_count": len(aug_pages),
        "delimiter": delimiter,
        "pages": aug_pages,
        "_augmented": True,
    }
    (IMAGES_ROOT / stable).mkdir(parents=True, exist_ok=True)
    (IMAGES_ROOT / stable / OFFSETS_AUG_NAME).write_text(
        json.dumps(offsets_payload, indent=2), encoding="utf-8")

    return "ok", note, len(aug_pages), len(aug_text)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Build OCR-augmented grounding text.")
    ap.add_argument("--only", default="", help="Comma-separated stable_names.")
    ap.add_argument("--limit", type=int, default=0, help="At most N papers.")
    ap.add_argument("--no-dedup", action="store_true",
                    help="Keep all OCR lines (no overlap removal).")
    ap.add_argument("--force", action="store_true",
                    help="Rebuild even if augmented .txt exists.")
    args = ap.parse_args()

    rows = load_text_index()
    if not rows:
        return 2

    only = set(s.strip() for s in args.only.split(",") if s.strip())
    if only:
        rows = [r for r in rows if r.get("stable_name") in only]
    if args.limit:
        rows = rows[: args.limit]

    dedup = not args.no_dedup
    index_rows = []
    n_ok = n_skip = n_fail = 0

    for idx, r in enumerate(rows, 1):
        stable = r.get("stable_name", "")
        if not stable:
            n_fail += 1
            continue
        aug_path = AUG_TEXT_DIR / (stable + ".txt")
        if aug_path.exists() and not args.force:
            n_skip += 1
            index_rows.append([stable, str(aug_path), "skipped_exists"])
            print("[{:3d}/{:3d}] {:<32s} exists".format(idx, len(rows), stable))
            continue
        status, note, n_pages, n_chars = build_paper(stable, dedup=dedup)
        if status == "ok":
            n_ok += 1
        else:
            n_fail += 1
        index_rows.append([stable, str(aug_path),
                           "{}|{}|{}p|{}c".format(status, note, n_pages, n_chars)])
        print("[{:3d}/{:3d}] {:<32s} {:<6s} {:>3d}p {:>7d}c  {}".format(
            idx, len(rows), stable, status, n_pages, n_chars, note))

    with open(AUG_INDEX, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["stable_name", "aug_text_path", "status"])
        w.writerows(index_rows)

    print("\nDone. ok={} skipped={} fail={}".format(n_ok, n_skip, n_fail))
    print("Augmented text :", AUG_TEXT_DIR)
    print("Offsets        : PDFs_step4_images/<stable>/{}".format(OFFSETS_AUG_NAME))
    print("Index          :", AUG_INDEX)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
