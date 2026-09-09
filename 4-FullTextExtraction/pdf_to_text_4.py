#!/usr/bin/env python3
"""
pdf_to_text_4.py
================

Convert every PDF in the Step 4 candidate set to plain text and cache it on
disk. Each cached text file is keyed by record_id and used as input to the
Step 4 full-text extraction subagent.

Strategy
--------
1. For each candidate in step4_candidates.jsonl:
   a. Skip if pdf_available != "yes".
   b. Skip if a fresh cached text already exists (idempotent by default).
   c. Run pdftotext -layout (preserves columns, page breaks as \f).
   d. Compute quality metrics: total chars, alpha char ratio, page count.
   e. Classify quality:
        ok                    - >= MIN_OK_CHARS chars AND alpha ratio >= MIN_ALPHA_RATIO
        low_text_density      - chars present but below threshold (probably partial image PDF)
        likely_ocr_needed     - chars very few or alpha ratio very low (image-only PDF)
        extraction_failed     - pdftotext error
   f. If quality is likely_ocr_needed AND --ocr-fallback enabled, OCR via tesseract.

2. Emit fulltext_index.csv summarising every PDF's cached text path and quality.

Outputs
-------
fulltext_cache/<safe_record_id>.txt          (one cached text per record)
fulltext_cache/<safe_record_id>.meta.json    (one metadata sidecar per record)
fulltext_index.csv                           (master index for prepare_4_input.py)

CLI
---
--candidates PATH           Path to step4_candidates.jsonl (default: from paths.py)
--cache-dir PATH            Cache dir (default: from paths.py)
--index PATH                Output index CSV (default: STEP_DIR/fulltext_index.csv)
--force                     Re-extract even if cached text exists
--ocr-fallback              Run tesseract OCR for likely_ocr_needed PDFs
--limit N                   Process only the first N candidates (smoke testing)
--dpi N                     OCR rasterisation DPI (default 200; only if --ocr-fallback)

Python 3.8+ compatible.

Author : Laszlo Papp
Date   : 2026-05-11
"""

import argparse
import csv
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from paths import (
    CANDIDATES_JSONL, FULLTEXT_CACHE_DIR, STEP_DIR,
)

INDEX_DEFAULT = STEP_DIR / "fulltext_index.csv"

# Quality thresholds (heuristic; tuned for academic PDFs)
MIN_OK_CHARS = 5000              # roughly 1-2 pages of text
MIN_ALPHA_RATIO = 0.55           # alpha chars / total non-whitespace chars
LIKELY_OCR_CHARS = 300           # below this is almost certainly image-only

PDFTOTEXT_BIN = "pdftotext"
TESSERACT_BIN = "tesseract"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def which(cmd):
    return shutil.which(cmd) is not None


def safe_record_id(rid):
    """Make a record_id safe to use as a filename."""
    if not rid:
        return "unknown"
    # Most record_ids are Zotero URIs. Take a sha1 short prefix + last URL chunk.
    last = rid.rsplit("/", 1)[-1] if "/" in rid else rid
    last = re.sub(r"[^A-Za-z0-9_.-]", "_", last)
    if len(last) > 40 or not last:
        h = hashlib.sha1(rid.encode("utf-8")).hexdigest()[:10]
        return last[:30] + "_" + h if last else h
    return last


def iter_jsonl(path):
    if not path.is_file():
        return
    with path.open("r", encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue


def alpha_ratio(text):
    if not text:
        return 0.0
    non_ws = re.sub(r"\s+", "", text)
    if not non_ws:
        return 0.0
    alpha = sum(1 for c in non_ws if c.isalpha())
    return alpha / len(non_ws)


def page_count_from_pdftotext(text):
    """pdftotext separates pages with form-feed \\f. Count + 1."""
    if not text:
        return 0
    return text.count("\f") + 1 if text else 0


def classify_quality(text):
    n = len(text or "")
    if n < LIKELY_OCR_CHARS:
        return "likely_ocr_needed"
    ar = alpha_ratio(text)
    if n < MIN_OK_CHARS or ar < MIN_ALPHA_RATIO:
        if ar < 0.35:
            return "likely_ocr_needed"
        return "low_text_density"
    return "ok"


# ---------------------------------------------------------------------------
# Extraction backends
# ---------------------------------------------------------------------------


def extract_pdftotext(pdf_path, layout=True, timeout=120):
    args = [PDFTOTEXT_BIN]
    if layout:
        args.append("-layout")
    args += ["-enc", "UTF-8", str(pdf_path), "-"]  # write to stdout
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return None, "pdftotext_not_installed"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    if proc.returncode != 0:
        return None, "pdftotext_returncode={}".format(proc.returncode)
    try:
        text = proc.stdout.decode("utf-8", errors="replace")
    except Exception as exc:
        return None, "decode_error:{}".format(exc)
    return text, None


def extract_ocr(pdf_path, dpi, timeout=900):
    """Tesseract OCR fallback. Rasterise via pdf2image, then tesseract per page."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return None, "pdf2image_not_installed"
    if not which(TESSERACT_BIN):
        return None, "tesseract_not_installed"
    try:
        pages = convert_from_path(str(pdf_path), dpi=dpi)
    except Exception as exc:
        return None, "pdf2image_error:{}".format(exc)
    if not pages:
        return None, "pdf2image_no_pages"
    out_chunks = []
    for i, page in enumerate(pages):
        tmp_png = "/tmp/_pdfocr_{}.png".format(os.getpid())
        page.save(tmp_png, "PNG")
        try:
            proc = subprocess.run(
                [TESSERACT_BIN, tmp_png, "-", "-l", "eng", "--psm", "1"],
                capture_output=True, timeout=timeout, check=False,
            )
        except subprocess.TimeoutExpired:
            return None, "tesseract_timeout_page={}".format(i)
        finally:
            try:
                os.unlink(tmp_png)
            except OSError:
                pass
        if proc.returncode != 0:
            return None, "tesseract_returncode={}_page={}".format(proc.returncode, i)
        out_chunks.append(proc.stdout.decode("utf-8", errors="replace"))
    # Use form-feed to separate pages, matching pdftotext convention
    return "\f".join(out_chunks), None


# ---------------------------------------------------------------------------
# Per-record pipeline
# ---------------------------------------------------------------------------


def process_record(cand, cache_dir, force, ocr_fallback, ocr_dpi):
    rid = cand.get("record_id", "")
    doi = cand.get("doi", "")
    pdf_path = cand.get("pdf_path", "")
    safe_id = safe_record_id(rid)
    text_path = cache_dir / (safe_id + ".txt")
    meta_path = cache_dir / (safe_id + ".meta.json")

    row = {
        "record_id": rid,
        "doi": doi,
        "candidate_number": cand.get("candidate_number"),
        "source": cand.get("source"),
        "pdf_path": pdf_path,
        "pdf_source": cand.get("pdf_source", ""),
        "safe_record_id": safe_id,
        "cached_text_path": str(text_path),
        "extraction_method": "",
        "char_count": 0,
        "page_count": 0,
        "alpha_ratio": 0.0,
        "quality": "",
        "error": "",
        "cache_hit": "no",
    }

    if not pdf_path or cand.get("pdf_available") != "yes":
        row["quality"] = "no_pdf"
        return row

    # Idempotent: skip if already cached and not forced
    if text_path.is_file() and meta_path.is_file() and not force:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            row.update({
                "extraction_method": meta.get("extraction_method", "cached"),
                "char_count": meta.get("char_count", 0),
                "page_count": meta.get("page_count", 0),
                "alpha_ratio": meta.get("alpha_ratio", 0.0),
                "quality": meta.get("quality", "ok"),
                "cache_hit": "yes",
            })
            return row
        except json.JSONDecodeError:
            pass  # fall through to re-extract

    # First pass: pdftotext -layout
    text, err = extract_pdftotext(Path(pdf_path), layout=True)
    method = "pdftotext_layout"

    if text is None or len(text) < LIKELY_OCR_CHARS:
        # Try without -layout as a fallback (sometimes -layout fails on odd PDFs)
        text2, err2 = extract_pdftotext(Path(pdf_path), layout=False)
        if text2 is not None and (text is None or len(text2) > len(text)):
            text = text2
            method = "pdftotext"
            err = err2

    if text is None:
        if ocr_fallback:
            ocr_text, ocr_err = extract_ocr(Path(pdf_path), ocr_dpi)
            if ocr_text is not None:
                text = ocr_text
                method = "tesseract_ocr"
                err = None
            else:
                row.update({"extraction_method": "failed",
                            "quality": "extraction_failed",
                            "error": "pdftotext_err={};ocr_err={}".format(err, ocr_err)})
                return row
        else:
            row.update({"extraction_method": "failed",
                        "quality": "extraction_failed",
                        "error": err or "unknown"})
            return row

    quality = classify_quality(text)

    # OCR fallback if requested and quality says image-only
    if quality == "likely_ocr_needed" and ocr_fallback and method != "tesseract_ocr":
        ocr_text, ocr_err = extract_ocr(Path(pdf_path), ocr_dpi)
        if ocr_text is not None and len(ocr_text) > len(text):
            text = ocr_text
            method = "tesseract_ocr"
            quality = classify_quality(text)

    # Persist text
    text_path.parent.mkdir(parents=True, exist_ok=True)
    text_path.write_text(text, encoding="utf-8")

    meta = {
        "record_id": rid,
        "doi": doi,
        "pdf_path": pdf_path,
        "extraction_method": method,
        "char_count": len(text),
        "page_count": page_count_from_pdftotext(text),
        "alpha_ratio": round(alpha_ratio(text), 4),
        "quality": quality,
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    row.update({
        "extraction_method": method,
        "char_count": meta["char_count"],
        "page_count": meta["page_count"],
        "alpha_ratio": meta["alpha_ratio"],
        "quality": quality,
        "error": "",
    })
    return row


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Extract full text from Step 4 candidate PDFs.")
    p.add_argument("--candidates", type=Path, default=CANDIDATES_JSONL)
    p.add_argument("--cache-dir", type=Path, default=FULLTEXT_CACHE_DIR)
    p.add_argument("--index", type=Path, default=INDEX_DEFAULT)
    p.add_argument("--force", action="store_true",
                   help="Re-extract even if cached text already exists.")
    p.add_argument("--ocr-fallback", action="store_true",
                   help="Run tesseract OCR for image-only PDFs.")
    p.add_argument("--limit", type=int, default=0,
                   help="Process only the first N candidates (0 = all).")
    p.add_argument("--dpi", type=int, default=200,
                   help="OCR rasterisation DPI (default 200).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")

    if not which(PDFTOTEXT_BIN):
        logging.error("pdftotext not installed. Install poppler-utils:")
        logging.error("  sudo apt-get install -y poppler-utils")
        return 1
    if args.ocr_fallback:
        if not which(TESSERACT_BIN):
            logging.error("tesseract not installed. Install:")
            logging.error("  sudo apt-get install -y tesseract-ocr")
            return 1
        try:
            import pdf2image  # noqa: F401
        except ImportError:
            logging.error("pdf2image not installed. Install:")
            logging.error("  pip install pdf2image")
            return 1

    args.cache_dir.mkdir(parents=True, exist_ok=True)

    if not args.candidates.is_file():
        logging.error("Candidates file not found: %s", args.candidates)
        logging.error("Run build_step4_candidates.py first.")
        return 1

    candidates = list(iter_jsonl(args.candidates))
    if args.limit > 0:
        candidates = candidates[: args.limit]
    logging.info("Processing %d candidates", len(candidates))

    rows = []
    qcounts = {"ok": 0, "low_text_density": 0, "likely_ocr_needed": 0,
               "extraction_failed": 0, "no_pdf": 0}
    cache_hits = 0
    t0 = time.time()
    for i, cand in enumerate(candidates, start=1):
        row = process_record(cand, args.cache_dir,
                             force=args.force,
                             ocr_fallback=args.ocr_fallback,
                             ocr_dpi=args.dpi)
        rows.append(row)
        q = row["quality"] or "extraction_failed"
        qcounts[q] = qcounts.get(q, 0) + 1
        if row["cache_hit"] == "yes":
            cache_hits += 1
        elapsed = time.time() - t0
        if i % 5 == 0 or i == len(candidates):
            logging.info("Progress %d/%d (%.1fs elapsed, %d cache hits, quality so far: %s)",
                         i, len(candidates), elapsed, cache_hits,
                         {k: v for k, v in qcounts.items() if v})

    # Write index CSV
    fieldnames = ["candidate_number", "record_id", "doi", "source", "pdf_path",
                  "pdf_source", "safe_record_id", "cached_text_path",
                  "extraction_method", "char_count", "page_count",
                  "alpha_ratio", "quality", "cache_hit", "error"]
    with args.index.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

    # Final report
    print("")
    print("Step 4 PDF -> text extraction complete")
    print("--------------------------------------")
    print("Total candidates processed : {}".format(len(rows)))
    print("Cache hits (skipped)       : {}".format(cache_hits))
    print("Wall time                  : {:.1f}s".format(time.time() - t0))
    print("")
    print("Quality distribution:")
    for k in ("ok", "low_text_density", "likely_ocr_needed", "extraction_failed", "no_pdf"):
        print("  {:<24s} {:>4d}".format(k, qcounts.get(k, 0)))
    print("")
    print("Cache dir : {}".format(args.cache_dir))
    print("Index     : {}".format(args.index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
