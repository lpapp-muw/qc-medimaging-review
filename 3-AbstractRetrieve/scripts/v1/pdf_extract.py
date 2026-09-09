"""
pdf_extract.py
Extract abstracts from PDFs identified by rdf_inventory.py.

For each PDF (default: pages 1-2 only):
    1. Try pdftotext. If output >= --min-text-chars chars, use that.
    2. Otherwise rasterize pages 1-2 with pdf2image and OCR with tesseract.
    3. Find the abstract section heuristically:
         - Look for headers: 'abstract', 'a b s t r a c t', 'summary', 'background'.
         - Capture from header until a sectional break: 'introduction',
           '1. introduction', 'keywords', 'methods', 'materials and methods',
           a double newline, or N chars max.
       Fallback: first 3000 chars of page 1 after title/author lines.
    4. Validate (same rules as merge_abstracts.py): >=250 chars, >=30 words,
       no placeholder, low title-jaccard, not contained in title.
    5. Write one JSONL line per record to --output.

Resumable: skips records already present in --output by DOI.

Usage:
    python pdf_extract.py \\
        --index rdf_pdf_index.csv \\
        --corpus merged_dataset.json \\
        --output rescued_abstracts_pdf.jsonl \\
        --pages 2

Optional:
    --limit N            stop after N records (smoke test)
    --no-ocr             skip OCR step (text PDFs only)
    --ocr-dpi 200        rasterization DPI for OCR (default 200)
    --min-text-chars 500 if pdftotext returns less, fall back to OCR
    --min-chars 250      validation: minimum abstract char length
    --min-words 30       validation: minimum word count
    --max-title-jaccard 0.7
"""

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

try:
    import pdfplumber  # noqa: F401  (probe import; not strictly required if pdftotext works)
except ImportError:
    pass

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")

PLACEHOLDER = [
    re.compile(r"\babstract\s+(is\s+)?not\s+available\b", re.I),
    re.compile(r"\bno\s+abstract(\s+available)?\b", re.I),
    re.compile(r"\babstract\s+unavailable\b", re.I),
]

# Abstract header detection (start). Matches:
#   "Abstract\n"
#   "Abstract:"  "Abstract."  "Abstract—"  "Abstract-"
#   "ABSTRACT"   "A B S T R A C T" (letter-spaced)
#   inline: "Abstract Quantum machine learning..."
ABSTRACT_HDR = re.compile(
    r"""(?ix)
        (?:^|\n|\s{2,})
        [\s\u00A0]*
        (?:
            a[\s\u00A0]*b[\s\u00A0]*s[\s\u00A0]*t[\s\u00A0]*r[\s\u00A0]*a[\s\u00A0]*c[\s\u00A0]*t |
            summary |
            background
        )
        [\s\u00A0:.\-—–]*
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)
# Stop tokens
STOP_HDR = re.compile(
    r"""(?ix)
        (?:^|\n|\s{2,})
        [\s\u00A0]*
        (?:
            \d+\.?\s*(introduction|background|methods|materials\s+and\s+methods|results|conclusions?) |
            introduction |
            i\.\s+introduction |
            keywords? |
            index\s+terms |
            methods |
            materials\s+and\s+methods |
            results |
            conclusions? |
            ©\s* |
            doi: |
            received: |
            corresponding\s+author |
            article\s+history |
            references
        )
        \b
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


# -----------------------------------------------------------------------------
# Text extraction
# -----------------------------------------------------------------------------

def pdftotext_pages(pdf_path, last_page, layout=True):
    """Run pdftotext. layout=True preserves columns/spacing, layout=False uses reading-order."""
    args = ["pdftotext"]
    if layout:
        args.append("-layout")
    args += ["-f", "1", "-l", str(last_page), pdf_path, "-"]
    try:
        out = subprocess.run(args, capture_output=True, timeout=60)
        return out.stdout.decode("utf-8", errors="ignore")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""

def ocr_pages(pdf_path, last_page, dpi):
    """Rasterize pages 1..last_page and OCR via tesseract. Return concatenated text."""
    try:
        from pdf2image import convert_from_path
        import pytesseract
    except ImportError:
        return ""
    out_text = []
    try:
        images = convert_from_path(pdf_path, dpi=dpi,
                                   first_page=1, last_page=last_page,
                                   fmt="png")
        for img in images:
            try:
                out_text.append(pytesseract.image_to_string(img, lang="eng"))
            except Exception:
                out_text.append("")
    except Exception:
        return ""
    return "\n".join(out_text)

# Cruft patterns that often precede the real abstract (repository stamps, etc.)
# When the FIRST ABSTRACT_HDR match falls within the first ~600 chars AND text before
# it matches cruft, prefer a LATER match if available.
CRUFT_RE = re.compile(
    r"(?i)(citation\s+for\s+published\s+version|"
    r"please\s+cite\s+(this\s+article\s+)?as|"
    r"document\s+version|"
    r"publication\s+date|"
    r"link\s+to\s+publication|"
    r"this\s+is\s+(the\s+)?(author|accepted|peer)|"
    r"take-down\s+policy|"
    r"licens(e|ed)\s+under|"
    r"©\s*\d{4})"
)

AUTHOR_BLOCK_RE = re.compile(
    r"(?i)(department\s+of|university|institute\s+of|"
    r"college\s+of|faculty\s+of|school\s+of|"
    r"corresponding\s+author|"
    r"\w+@[\w.]+|"     # emails
    r"\d{5,}\s*,\s*[a-z]+\s*,?\s*[a-z]+)"     # postcode-style
)

# -----------------------------------------------------------------------------
# Abstract extraction heuristic
# -----------------------------------------------------------------------------

def normalize_text(s):
    s = re.sub(r"\r\n", "\n", s or "")
    # Drop running headers/footers (lines with mostly digits/page indicators)
    s = re.sub(r"\f", "\n", s)
    # Compact multiple blank lines
    s = re.sub(r"\n[ \t]+\n", "\n\n", s)
    return s

def find_abstract(text):
    """Return the abstract substring or '' if not found heuristically."""
    if not text:
        return ""
    txt = normalize_text(text)

    # Find ALL Abstract-style headers
    matches = list(ABSTRACT_HDR.finditer(txt))
    if matches:
        # Choose the best match:
        # - If there's text BEFORE the first match that looks like repository cruft (citation
        #   stamp, license notice, etc.), prefer a LATER match if available.
        # - Otherwise the first match is normally correct.
        chosen = matches[0]
        pre = txt[: chosen.start()]
        if CRUFT_RE.search(pre) and len(matches) > 1:
            chosen = matches[-1]
        start = chosen.end()
        stop_m = STOP_HDR.search(txt, pos=start + 10)
        end = stop_m.start() if stop_m else start + 4000
        candidate = txt[start:end].strip()
        # Compact line breaks; PDF text often has artificial wraps within paragraphs
        candidate = re.sub(r"-\n", "", candidate)
        candidate = re.sub(r"\n+", " ", candidate)
        candidate = re.sub(r"\s+", " ", candidate).strip()
        # Strip remaining "Keywords:" prefix that occasionally leaks through
        candidate = re.sub(r"^(keywords?|index\s+terms)\s*[:\-—–]\s*", "", candidate, flags=re.I)
        if 150 <= len(candidate) <= 6000:
            return candidate

    # Fallback: take the first paragraph >250 chars that looks abstract-like
    # (skip author blocks, citation lines, page headers, emails, license boilerplate).
    paragraphs = re.split(r"\n\s*\n", txt)
    for para in paragraphs:
        cleaned = re.sub(r"-\n", "", para)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if len(cleaned) < 250:
            continue
        if "." not in cleaned:
            continue
        if cleaned.isupper():
            continue
        if AUTHOR_BLOCK_RE.search(cleaned):
            continue
        if CRUFT_RE.search(cleaned):
            continue
        # Skip paragraphs dominated by names (lots of commas, few sentences)
        n_commas = cleaned.count(",")
        n_periods = cleaned.count(".")
        if n_commas > 8 and n_periods < 3:
            continue
        return cleaned[:3000]
    return ""

# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def validate(abstract, title, min_chars, min_words, max_jaccard):
    if not abstract:
        return False, "empty", 0, 0, 0.0
    char_len = len(abstract)
    words = WORD_RE.findall(abstract)
    wc = len(words)
    for pat in PLACEHOLDER:
        if pat.search(abstract):
            return False, "placeholder", char_len, wc, 0.0
    if char_len < min_chars:
        return False, f"chars<{min_chars}", char_len, wc, 0.0
    if wc < min_words:
        return False, f"words<{min_words}", char_len, wc, 0.0
    t_norm = re.sub(r"\s+", " ", (title or "").lower()).strip()
    a_norm = re.sub(r"\s+", " ", abstract.lower()).strip()
    if t_norm and (a_norm in t_norm or t_norm in a_norm):
        return False, "abstract_is_title", char_len, wc, 1.0
    if t_norm:
        t_tok = set(WORD_RE.findall(title.lower()))
        a_tok = set(WORD_RE.findall(abstract.lower()))
        if t_tok and a_tok:
            j = len(t_tok & a_tok) / len(t_tok | a_tok)
            if j >= max_jaccard:
                return False, f"title_jaccard>={max_jaccard:.2f}", char_len, wc, j
        else:
            j = 0.0
    else:
        j = 0.0
    return True, "ok", char_len, wc, j

# -----------------------------------------------------------------------------
# I/O
# -----------------------------------------------------------------------------

def load_index(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            if row["match_status"] == "matched":
                rows.append(row)
    return rows

def load_corpus(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_processed_dois(path):
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                d = (r.get("doi") or "").lower().strip()
                if d:
                    done.add(d)
            except json.JSONDecodeError:
                continue
    return done

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", default="rdf_pdf_index.csv")
    p.add_argument("--corpus", default="merged_dataset.json")
    p.add_argument("--output", default="rescued_abstracts_pdf.jsonl")
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--no-ocr", action="store_true")
    p.add_argument("--ocr-dpi", type=int, default=200)
    p.add_argument("--min-text-chars", type=int, default=500)
    p.add_argument("--min-chars", type=int, default=250)
    p.add_argument("--min-words", type=int, default=30)
    p.add_argument("--max-title-jaccard", type=float, default=0.7)
    args = p.parse_args()

    if not os.path.exists(args.index):
        sys.exit(f"Missing {args.index}")
    if not os.path.exists(args.corpus):
        sys.exit(f"Missing {args.corpus}")

    print(f"Loading inputs ...", file=sys.stderr)
    index_rows = load_index(args.index)
    corpus = load_corpus(args.corpus)
    by_rid = {r["id"]: r for r in corpus if r.get("id")}
    done = load_processed_dois(args.output)

    print(f"  Index rows with matched PDF: {len(index_rows)}", file=sys.stderr)
    print(f"  Already in {args.output}:    {len(done)}", file=sys.stderr)

    todo = [r for r in index_rows
            if (r["doi"] or "").lower().strip() not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"  To process this run:         {len(todo)}", file=sys.stderr)
    print(f"  Pages per PDF:               {args.pages}", file=sys.stderr)
    print(f"  OCR enabled:                 {not args.no_ocr}", file=sys.stderr)

    counts = {"pdftotext_hit": 0, "ocr_hit": 0, "validated": 0, "rejected": 0, "no_text": 0}
    rejection_reasons = {}

    started = time.time()
    with open(args.output, "a", encoding="utf-8") as out:
        for i, row in enumerate(todo, 1):
            doi = row["doi"]
            pdf_path = row["pdf_abs_path"]
            rid = row["record_id"]
            src = by_rid.get(rid, {})
            title = (src.get("title") or "").strip()

            # 1) pdftotext: try both layout and reading-order modes; pick the one that yields
            #    an abstract via the header-based heuristic. Falls back to OCR only if neither
            #    has enough text at all.
            t0 = time.time()
            text_layout = pdftotext_pages(pdf_path, args.pages, layout=True)
            text_flow   = pdftotext_pages(pdf_path, args.pages, layout=False)

            # Try the abstract heuristic on both extractions and pick the one with a longer
            # result (often the reading-order version handles 2-column journal layouts better).
            ab_layout = find_abstract(text_layout)
            ab_flow   = find_abstract(text_flow)
            if len(ab_flow) > len(ab_layout):
                text = text_flow
                abstract = ab_flow
                method = "pdftotext_flow"
            else:
                text = text_layout
                abstract = ab_layout
                method = "pdftotext_layout"

            # OCR fallback if we have neither extracted abstract nor much text overall
            text_chars = max(len(text_layout), len(text_flow))
            if not abstract and text_chars < args.min_text_chars and not args.no_ocr:
                text_ocr = ocr_pages(pdf_path, args.pages, args.ocr_dpi)
                if text_ocr:
                    ab_ocr = find_abstract(text_ocr)
                    if ab_ocr:
                        text = text_ocr
                        abstract = ab_ocr
                        method = "ocr"

            if method.startswith("pdftotext"):
                counts["pdftotext_hit"] += 1
            elif method == "ocr":
                counts["ocr_hit"] += 1
            if not abstract:
                counts["no_text"] += 1
                result = {
                    "doi": doi, "record_id": rid,
                    "source": f"pdf_{method}",
                    "abstract": None,
                    "char_len": 0, "word_count": 0,
                    "accepted": False, "reason": "no_abstract_found",
                    "pdf_abs_path": pdf_path,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "duration_s": round(time.time() - t0, 2),
                }
                out.write(json.dumps(result, ensure_ascii=False) + "\n"); out.flush()
            else:
                accepted, reason, cl, wc, tj = validate(
                    abstract, title, args.min_chars, args.min_words, args.max_title_jaccard
                )
                if accepted:
                    counts["validated"] += 1
                else:
                    counts["rejected"] += 1
                    rejection_reasons[reason] = rejection_reasons.get(reason, 0) + 1
                result = {
                    "doi": doi, "record_id": rid,
                    "source": f"pdf_{method}",
                    "abstract": abstract,
                    "char_len": cl, "word_count": wc,
                    "title_jaccard": round(tj, 3),
                    "accepted": accepted, "reason": reason,
                    "pdf_abs_path": pdf_path,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "duration_s": round(time.time() - t0, 2),
                }
                out.write(json.dumps(result, ensure_ascii=False) + "\n"); out.flush()

            if i == 1 or i % 10 == 0 or i == len(todo):
                el = time.time() - started
                eta = (len(todo) - i) / max(1, i) * el
                print(
                    f"[{i}/{len(todo)}]  validated={counts['validated']}  "
                    f"rejected={counts['rejected']}  no_text={counts['no_text']}  "
                    f"pdftotext={counts['pdftotext_hit']}  ocr={counts['ocr_hit']}  "
                    f"elapsed={int(el)}s  eta={int(eta)}s",
                    file=sys.stderr,
                )

    print("\n" + "=" * 60, file=sys.stderr)
    print(f"Processed {len(todo)} PDFs.", file=sys.stderr)
    for k, v in counts.items():
        print(f"  {k:18s} {v}", file=sys.stderr)
    if rejection_reasons:
        print(f"\nRejection reasons:", file=sys.stderr)
        for k, v in sorted(rejection_reasons.items(), key=lambda x: -x[1]):
            print(f"  {k:30s} {v}", file=sys.stderr)
    print(f"\nOutput: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
