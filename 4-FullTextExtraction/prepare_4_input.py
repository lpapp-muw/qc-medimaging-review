#!/usr/bin/env python3
"""
prepare_4_input.py
==================

Join the Step 4 candidate set (step4_candidates.jsonl) with cached full text
produced by pdf_to_text_4.py (fulltext_index.csv + fulltext_cache/) to produce
step4_input.jsonl: one record per candidate with all metadata AND the full
text inlined, ready to be consumed by the Step 4 extract-fulltext subagent.

Output schema (one JSON object per line, in candidate_number order)
-------------------------------------------------------------------
{
  "record_id":      "<Zotero URI>",
  "candidate_number": <int>,
  "source":         "step3_high" | "step3_uncertain" | "step3_5_promote",
  "doi":            "<DOI>",
  "title":          "<str>",
  "abstract":       "<str>",
  "authors":        "<str>",
  "journal":        "<str>",
  "year":           "<str>",
  "publisher":      "<str>",
  "pdf_path":       "<absolute path>",
  "pdf_source":     "rdf_index" | "manual",
  "fulltext":       "<plain text from pdf_to_text_4.py>",
  "fulltext_quality": "ok" | "low_text_density" | "likely_ocr_needed" | "extraction_failed" | "no_pdf",
  "fulltext_extraction_method": "pdftotext_layout" | "pdftotext" | "tesseract_ocr" | "failed",
  "fulltext_char_count": <int>,
  "fulltext_page_count": <int>,
  "step3_category": "<str>",
  "step3_confidence": "<str>" | null,
  "step3_quote":    "<str>" | null,
  "step3_reasoning":"<str>" | null,
  "step3_5_verdict":"<str>" | null,
  "step3_5_modality_name": "<str>" | null
}

Exit codes
----------
0  ok
1  inputs missing or unreadable
2  candidate / index mismatch

Python 3.8+ compatible.

Author : Laszlo Papp
Date   : 2026-05-11
"""

import argparse
import csv
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from paths import (
    CANDIDATES_JSONL, FULLTEXT_CACHE_DIR, STEP_DIR,
)

INDEX_DEFAULT = STEP_DIR / "fulltext_index.csv"
INPUT_JSONL_DEFAULT = STEP_DIR / "step4_input.jsonl"
INPUT_REPORT_DEFAULT = STEP_DIR / "step4_input_report.txt"


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


def load_fulltext_index(index_path):
    """Return dict record_id -> row dict."""
    if not index_path.is_file():
        logging.error("Fulltext index not found: %s. Run pdf_to_text_4.py first.", index_path)
        sys.exit(1)
    out = {}
    with index_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rid = (row.get("record_id") or "").strip()
            if not rid:
                continue
            out[rid] = row
    logging.info("Loaded fulltext index: %d entries", len(out))
    return out


def read_cached_text(path):
    """Read a cached text file. Returns ('', err_string) on failure."""
    if not path:
        return "", "empty_path"
    p = Path(path)
    if not p.is_file():
        return "", "file_not_found"
    try:
        return p.read_text(encoding="utf-8", errors="replace"), ""
    except Exception as exc:
        return "", "read_error:{}".format(exc)


def assemble(candidates_path, index_path, out_jsonl_path, out_report_path,
             max_fulltext_chars):
    if not candidates_path.is_file():
        logging.error("Candidates file not found: %s", candidates_path)
        sys.exit(1)

    fti = load_fulltext_index(index_path)
    candidates = list(iter_jsonl(candidates_path))
    logging.info("Loaded candidates: %d", len(candidates))

    missing_text = []
    quality_counts = Counter()
    truncated = 0
    total_chars = 0
    written = 0

    with out_jsonl_path.open("w", encoding="utf-8") as out:
        for c in candidates:
            rid = c.get("record_id", "")
            fti_row = fti.get(rid, {})
            text_path = fti_row.get("cached_text_path", "")
            text, err = read_cached_text(text_path)
            quality = fti_row.get("quality", "")
            if not text and quality != "no_pdf":
                missing_text.append({
                    "candidate_number": c.get("candidate_number"),
                    "record_id": rid,
                    "doi": c.get("doi"),
                    "title": c.get("title"),
                    "reason": err or quality or "unknown",
                })
            quality_counts[quality or "missing"] += 1

            # Optional truncation guard
            if max_fulltext_chars and len(text) > max_fulltext_chars:
                text = text[: max_fulltext_chars] + "\n\n[TRUNCATED]"
                truncated += 1
            total_chars += len(text)

            payload = {
                "record_id": rid,
                "candidate_number": c.get("candidate_number"),
                "source": c.get("source"),
                "doi": c.get("doi"),
                "title": c.get("title"),
                "abstract": c.get("abstract"),
                "authors": c.get("authors"),
                "journal": c.get("journal"),
                "year": c.get("year"),
                "publisher": c.get("publisher"),
                "pdf_path": c.get("pdf_path"),
                "pdf_source": c.get("pdf_source"),
                "fulltext": text,
                "fulltext_quality": quality or "missing",
                "fulltext_extraction_method": fti_row.get("extraction_method", ""),
                "fulltext_char_count": int(fti_row.get("char_count") or 0),
                "fulltext_page_count": int(fti_row.get("page_count") or 0),
                "step3_category": c.get("step3_category"),
                "step3_confidence": c.get("step3_confidence"),
                "step3_quote": c.get("step3_quote"),
                "step3_reasoning": c.get("step3_reasoning"),
                "step3_5_verdict": c.get("step3_5_verdict"),
                "step3_5_modality_name": c.get("step3_5_modality_name"),
            }
            out.write(json.dumps(payload, ensure_ascii=False) + "\n")
            written += 1

    # Text report
    lines = []
    a = lines.append
    a("Step 4 input preparation report")
    a("===============================")
    a("")
    a("Candidates                 : {}".format(len(candidates)))
    a("Input records emitted      : {}".format(written))
    a("Total fulltext characters  : {:,}".format(total_chars))
    a("Mean characters per record : {:,.0f}".format(total_chars / max(1, written)))
    if max_fulltext_chars:
        a("Records truncated to {:,} chars: {}".format(max_fulltext_chars, truncated))
    a("")
    a("Fulltext quality distribution:")
    for q in ("ok", "low_text_density", "likely_ocr_needed", "extraction_failed",
              "no_pdf", "missing"):
        a("  {:<24s} {:>4d}".format(q, quality_counts.get(q, 0)))
    if missing_text:
        a("")
        a("Candidates with missing fulltext ({}):".format(len(missing_text)))
        for m in missing_text:
            a("  #{:>3d}  {}  {}  ({})".format(
                m["candidate_number"] or 0, m["doi"] or "(no doi)",
                (m["title"] or "")[:60], m["reason"]))

    out_report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print("")
    print("JSONL  : {}".format(out_jsonl_path))
    print("Report : {}".format(out_report_path))
    return 0 if not missing_text else 0  # don't fail the run; surface in report


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="Prepare Step 4 input (candidates + fulltext).")
    p.add_argument("--candidates", type=Path, default=CANDIDATES_JSONL)
    p.add_argument("--index", type=Path, default=INDEX_DEFAULT)
    p.add_argument("--output", type=Path, default=INPUT_JSONL_DEFAULT)
    p.add_argument("--report", type=Path, default=INPUT_REPORT_DEFAULT)
    p.add_argument("--max-fulltext-chars", type=int, default=0,
                   help="If >0, truncate fulltext at this many chars (with [TRUNCATED] marker).")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(message)s")
    return assemble(args.candidates, args.index, args.output, args.report,
                    args.max_fulltext_chars)


if __name__ == "__main__":
    sys.exit(main())
