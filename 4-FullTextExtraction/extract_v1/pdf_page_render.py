"""
pdf_page_render.py — Render every PDF page to an image, and compute the
character-offset map of each page inside the corresponding .txt file.

Rationale (hybrid subagent feeding):
  Subagents (Claude Opus 4.7) receive BOTH the pdftotext output AND the
  page images. Text gives lossless characters for quote-grounding; images
  give visual layout, figures, tables, equations, and embedded text that
  pdftotext cannot reach. The chunker (next item) uses the page-offset map
  to determine which page images accompany each text chunk.

Outputs per PDF:
  PDFs_step4_images/<stable_name>/page_001.jpg
                                 /page_002.jpg
                                 /...
                                 /page_offsets.json
                                 /render_meta.json

  page_offsets.json:
    {
      "stable_name": "...",
      "text_path": "...",
      "page_count": <int>,
      "pages": [
        {"page": 1, "char_start": 0,     "char_end": 4823,  "image": "page_001.jpg"},
        {"page": 2, "char_start": 4823,  "char_end": 9214,  "image": "page_002.jpg"},
        ...
      ],
      "delimiter": "form_feed"  // or "fallback_estimate" if no form feeds present
    }

  render_meta.json:
    {
      "dpi": 150,
      "format": "jpeg",
      "quality": 85,
      "render_seconds": <float>,
      "total_bytes": <int>
    }

Top-level index file (rebuilt every run):
  pdf_image_index.csv — doi, stable_name, image_dir, page_count, total_bytes,
                        delimiter, status, notes

Python 3.8 compatible.

Usage:
  python3 pdf_page_render.py               # render every PDF in PDFS_STEP4
  python3 pdf_page_render.py --limit 5     # smoke test
  python3 pdf_page_render.py --pdf NAME.pdf
  python3 pdf_page_render.py --force       # re-render even if images exist
  python3 pdf_page_render.py --dry-run     # list what would be rendered
  python3 pdf_page_render.py --dpi 200     # override DPI
  python3 pdf_page_render.py --format png  # use PNG instead of JPEG
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

import paths_step4 as P
from pdf_text_extract import stable_name_from_pdf, _build_doi_map


FORM_FEED = "\x0c"


# ----------------------------------------------------------------------------
# Page-offset map
# ----------------------------------------------------------------------------
def compute_page_offsets(text):
    """Compute per-page character offsets from pdftotext output.

    pdftotext (default and -layout modes) inserts FORM_FEED (0x0c) between
    pages. We split on that to recover page boundaries.

    Returns: (pages_list, delimiter_used)
      pages_list: list of {"page", "char_start", "char_end"} dicts
      delimiter_used: "form_feed" if found, "fallback_estimate" if not
    """
    if FORM_FEED in text:
        # Split keeps semantics correct
        chunks = text.split(FORM_FEED)
        # Each chunk corresponds to one page. The form-feed itself is one
        # char and lives BETWEEN chunks — we attribute it to the page that
        # immediately precedes it (i.e. include FF in that page's slice).
        pages = []
        cursor = 0
        for i, chunk in enumerate(chunks, 1):
            start = cursor
            end = start + len(chunk)
            # Add 1 for the form-feed character itself, except after the
            # last chunk where no form-feed follows
            if i < len(chunks):
                end += 1
            pages.append({"page": i, "char_start": start, "char_end": end})
            cursor = end
        # Drop a trailing empty page (some PDFs end with a final \x0c)
        if pages and pages[-1]["char_end"] - pages[-1]["char_start"] <= 1:
            pages = pages[:-1]
        return pages, "form_feed"

    # Fallback: no form-feed found. Estimate evenly by total length and
    # page count from pdfinfo (looked up by caller; here we just signal).
    return [], "fallback_estimate"


def compute_page_offsets_fallback(text, page_count):
    """Even-split fallback when pdftotext did not emit form-feeds.

    Used only when delimiter detection fails; chunker will still work but
    page-to-text mapping is approximate. Flag _page_offset_approximate set.
    """
    if page_count <= 0:
        return []
    n = len(text)
    per_page = n // page_count
    pages = []
    for i in range(1, page_count + 1):
        start = (i - 1) * per_page
        end = i * per_page if i < page_count else n
        pages.append({"page": i, "char_start": start, "char_end": end})
    return pages


# ----------------------------------------------------------------------------
# pdftoppm runner
# ----------------------------------------------------------------------------
def _run_pdftoppm(pdf_path, out_prefix, dpi, fmt, quality):
    """Render every page of pdf_path to <out_prefix>-N.<ext> via pdftoppm.

    Returns (returncode, stderr_text).

    Newer pdftoppm versions output unpadded page numbers (page-1.jpg,
    page-10.jpg, page-100.jpg). We rename afterwards to zero-padded
    page_NNN.<ext>.
    """
    cmd = ["pdftoppm", "-r", str(dpi)]
    if fmt == "jpeg":
        cmd += ["-jpeg", "-jpegopt", "quality={}".format(quality)]
    elif fmt == "png":
        cmd += ["-png"]
    else:
        raise ValueError("Unsupported format: " + fmt)
    cmd += [str(pdf_path), str(out_prefix)]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
    except subprocess.TimeoutExpired:
        return 124, "pdftoppm timeout"
    return proc.returncode, proc.stderr.decode("utf-8", errors="replace")


def _rename_to_padded(image_dir, src_prefix, fmt):
    """Rename <src_prefix>-N.<ext> -> page_NNN.<ext> with zero-padding.

    Returns (renamed_count, page_count_seen).
    """
    ext = "jpg" if fmt == "jpeg" else "png"
    # pdftoppm output: <prefix>-1.<ext>, <prefix>-2.<ext>, etc.
    candidates = sorted(image_dir.glob(src_prefix.name + "-*.{}".format(ext)))
    if not candidates:
        return 0, 0
    # Determine page numbers from suffix
    page_nums = []
    for f in candidates:
        stem = f.stem  # e.g. "render-12"
        suffix = stem.rsplit("-", 1)[-1]
        try:
            page_nums.append((int(suffix), f))
        except ValueError:
            continue
    if not page_nums:
        return 0, 0
    page_nums.sort()
    n_pages = page_nums[-1][0]
    width = max(3, len(str(n_pages)))  # at least 3 digits
    renamed = 0
    for page_n, src in page_nums:
        dst = image_dir / "page_{}.{}".format(str(page_n).zfill(width), ext)
        if src != dst:
            src.rename(dst)
        renamed += 1
    return renamed, n_pages


# ----------------------------------------------------------------------------
# Per-PDF processing
# ----------------------------------------------------------------------------
def process_one_pdf(pdf_path, doi_map, dpi, fmt, quality, force=False):
    """Render a single PDF and write its page-offset map.

    Returns a dict row for the CSV index.
    """
    stable = stable_name_from_pdf(pdf_path)
    image_dir = P.PDF_TEXT_DIR.parent / "PDFs_step4_images" / stable
    text_path = P.PDF_TEXT_DIR / (stable + ".txt")
    offsets_path = image_dir / "page_offsets.json"
    meta_path = image_dir / "render_meta.json"

    row = {
        "doi": doi_map.get(stable, ""),
        "stable_name": stable,
        "image_dir": str(image_dir),
        "page_count": 0,
        "total_bytes": 0,
        "delimiter": "",
        "status": "",
        "notes": "",
    }

    # Cached short-circuit
    if image_dir.exists() and offsets_path.exists() and not force:
        try:
            existing = json.loads(offsets_path.read_text(encoding="utf-8"))
            total_bytes = sum(
                f.stat().st_size
                for f in image_dir.glob("page_*.*")
                if f.is_file()
            )
            row["page_count"] = existing.get("page_count", 0)
            row["total_bytes"] = total_bytes
            row["delimiter"] = existing.get("delimiter", "")
            row["status"] = "cached"
            return row
        except Exception:
            pass  # fall through and re-render

    if not text_path.exists():
        row["status"] = "no_text"
        row["notes"] = "missing text file at {}".format(text_path)
        return row

    image_dir.mkdir(parents=True, exist_ok=True)

    # Render
    t0 = time.time()
    src_prefix = image_dir / "render"
    rc, err = _run_pdftoppm(pdf_path, src_prefix, dpi, fmt, quality)
    if rc != 0:
        row["status"] = "render_failed"
        row["notes"] = "pdftoppm rc={} err={}".format(rc, (err or "").strip()[:200])
        return row

    renamed, n_pages_rendered = _rename_to_padded(image_dir, src_prefix, fmt)
    if renamed == 0:
        row["status"] = "no_images_produced"
        row["notes"] = "pdftoppm produced no recognisable output"
        return row

    render_seconds = time.time() - t0

    # Build page-offset map
    text = text_path.read_text(encoding="utf-8", errors="replace")
    pages, delimiter = compute_page_offsets(text)
    if not pages:
        # Fallback: even-split
        pages = compute_page_offsets_fallback(text, n_pages_rendered)
        delimiter = "fallback_estimate"
        row["notes"] = (row["notes"] + "; " if row["notes"] else "") + \
                       "no form-feed in text; even-split fallback"

    # Cross-check: do page count from text and from render agree?
    n_pages_text = len(pages)
    if n_pages_text != n_pages_rendered:
        delimiter = (delimiter + ";page_count_mismatch")
        row["notes"] = (row["notes"] + "; " if row["notes"] else "") + \
                       "text={}p, render={}p".format(n_pages_text, n_pages_rendered)
        # Truncate the longer side to match the shorter so the chunker
        # always has a valid mapping.
        if n_pages_text > n_pages_rendered:
            pages = pages[:n_pages_rendered]
        # If render has more pages than text says, leave pages as-is
        # and let the chunker treat any over-the-end pages as appendix.

    # Attach image filenames
    ext = "jpg" if fmt == "jpeg" else "png"
    image_files = sorted(image_dir.glob("page_*.{}".format(ext)))
    image_lookup = {}
    for f in image_files:
        # f.stem = "page_001"
        try:
            n = int(f.stem.split("_")[-1])
            image_lookup[n] = f.name
        except ValueError:
            continue
    for p in pages:
        p["image"] = image_lookup.get(p["page"], "")

    offsets_payload = {
        "stable_name": stable,
        "text_path": str(text_path),
        "page_count": n_pages_rendered,
        "pages": pages,
        "delimiter": delimiter,
    }
    offsets_path.write_text(json.dumps(offsets_payload, indent=2), encoding="utf-8")

    total_bytes = sum(f.stat().st_size for f in image_files)

    meta_payload = {
        "dpi": dpi,
        "format": fmt,
        "quality": quality if fmt == "jpeg" else None,
        "render_seconds": round(render_seconds, 2),
        "total_bytes": total_bytes,
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    row["page_count"] = n_pages_rendered
    row["total_bytes"] = total_bytes
    row["delimiter"] = delimiter
    row["status"] = "ok"
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
                        help="Re-render even if images already exist.")
    parser.add_argument("--dry-run", action="store_true",
                        help="List what would be processed and stop.")
    parser.add_argument("--dpi", type=int, default=150,
                        help="Render DPI (default 150).")
    parser.add_argument("--format", choices=["jpeg", "png"], default="jpeg",
                        help="Image format (default jpeg).")
    parser.add_argument("--quality", type=int, default=85,
                        help="JPEG quality 1-100 (default 85; ignored if --format=png).")
    args = parser.parse_args()

    P.ensure_dirs()

    # Tool presence
    for tool in ("pdftoppm",):
        if shutil.which(tool) is None:
            print("ERROR: required tool '{}' not on PATH".format(tool), file=sys.stderr)
            return 2

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

    print("Found {} PDF(s). DPI={}, format={}{}".format(
        len(pdfs), args.dpi, args.format,
        ", quality={}".format(args.quality) if args.format == "jpeg" else ""))
    if args.dry_run:
        for p in pdfs:
            stable = stable_name_from_pdf(p)
            target_dir = P.PDF_TEXT_DIR.parent / "PDFs_step4_images" / stable
            print(" -", p.name, "->", target_dir)
        return 0

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
            row = process_one_pdf(pdf, doi_map, args.dpi, args.format,
                                  args.quality, force=args.force)
        except Exception:
            row = {
                "doi": doi_map.get(stable_name_from_pdf(pdf), ""),
                "stable_name": stable_name_from_pdf(pdf),
                "image_dir": "",
                "page_count": 0,
                "total_bytes": 0,
                "delimiter": "",
                "status": "exception",
                "notes": traceback.format_exc().splitlines()[-1][:200],
            }
        dt = time.time() - t_pdf
        rows.append(row)
        mb = row["total_bytes"] / (1024 * 1024) if row["total_bytes"] else 0
        print("[{:3d}/{:3d}] {:<32s} pages={:>3d}  size={:>6.1f}MB  {:.1f}s  {}".format(
            i, len(pdfs),
            pdf.name[:32],
            row["page_count"],
            mb,
            dt,
            row["status"],
        ))

    # Write CSV index
    csv_path = P.EXTRACT_DIR / "pdf_image_index.csv"
    fieldnames = [
        "doi", "stable_name", "image_dir", "page_count", "total_bytes",
        "delimiter", "status", "notes",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    # Summary
    n_ok = sum(1 for r in rows if r["status"] in ("ok", "cached"))
    n_failed = sum(1 for r in rows if r["status"] not in ("ok", "cached"))
    n_fallback = sum(1 for r in rows if r["delimiter"] == "fallback_estimate")
    n_mismatch = sum(1 for r in rows if "page_count_mismatch" in r["delimiter"])
    total_mb = sum(r["total_bytes"] for r in rows) / (1024 * 1024)
    total_pages = sum(r["page_count"] for r in rows)
    print()
    print("Done. {} ok, {} failed.  pages={}  images={:,.1f}MB  fallback_offset={}  page_count_mismatch={}  wall={:.1f}s".format(
        n_ok, n_failed, total_pages, total_mb, n_fallback, n_mismatch, time.time() - t0,
    ))
    print("Index written to:", csv_path)
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
