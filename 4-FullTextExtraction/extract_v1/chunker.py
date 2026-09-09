"""
chunker.py — Token-budget chunk planner for the hybrid (text + page-image)
subagent feed.

Design (locked):
  - With page images in the feed, IMAGE tokens dominate input budget. The
    binding constraint is page count, not char count.
  - A paper is fed SINGLE-SHOT per pass whenever its estimated input tokens
    fit under the ceiling (100K for A/B/C, 85K for D).
  - Papers exceeding the ceiling are split BY PAGES, each chunk carrying its
    contiguous page range plus the corresponding text slice (via
    page_offsets.json). 1-page overlap between adjacent chunks. When a split
    point is near a section boundary (±10% of the running budget), snap to
    the section break for cleaner semantics.
  - Nothing is truncated. Large papers are chunked, never cut.

Token model (from paths_step4):
  est_tokens(pages, chars) = pages * TOKENS_PER_PAGE_IMAGE + chars * TOKENS_PER_CHAR

Inputs:
  - pdf_text_index.csv         (stable_name -> text_path, char_count, doi)
  - PDFs_step4_images/<stable>/page_offsets.json  (page -> char range + image)
  - section_tags/<stable>.json (optional; for boundary snapping)

Output per paper:
  chunks/<stable_name>.json
    {
      "stable_name": "...",
      "doi": "...",
      "text_path": "...",
      "image_dir": "...",
      "page_count": <int>,
      "char_count": <int>,
      "single_shot": {"A": bool, "B": bool, "C": bool, "D": bool},
      "plans": {
        "A": [ {chunk_id, page_start, page_end, char_start, char_end,
                images: [...], est_tokens}, ... ],
        "B": [...],
        "C": [...],
        "D": [...]
      }
    }

Note: Passes A/B/C share an identical plan (same 100K ceiling), so they are
computed once and referenced by all three. Pass D uses the 85K ceiling and
may therefore have a different (finer) plan for the largest papers.

Python 3.8 compatible.

Usage:
  python3 chunker.py                 # plan every paper in pdf_text_index.csv
  python3 chunker.py --limit 5
  python3 chunker.py --stable NAME
  python3 chunker.py --force
  python3 chunker.py --report        # print summary table, write nothing
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import paths_step4 as P


def load_text_index():
    """Return list of dicts from pdf_text_index.csv."""
    rows = []
    with open(P.PDF_TEXT_INDEX, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append(r)
    return rows


def load_page_offsets(stable):
    """Return the page_offsets.json payload for a paper, or None."""
    path = P.PDF_TEXT_DIR.parent / "PDFs_step4_images" / stable / "page_offsets.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_section_tags(stable):
    """Return the list of section dicts for a paper, or []."""
    path = P.SECTION_TAGS_DIR / (stable + ".json")
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sections", [])
    except Exception:
        return []


def _section_break_chars(sections):
    """Return a sorted list of character offsets where sections start."""
    return sorted(s["start"] for s in sections if "start" in s)


def plan_pages(pages, ceiling, sections):
    """Greedy page-packing under a token ceiling, with section-snap and overlap.

    `pages` : list of {"page", "char_start", "char_end", "image"} dicts
              (already sorted by page).
    `ceiling`: token ceiling for this pass.
    `sections`: section dicts for optional boundary snapping.

    Returns a list of chunk dicts. If everything fits in one chunk, returns a
    single-element list.
    """
    if not pages:
        return []

    total_pages = len(pages)
    total_chars = pages[-1]["char_end"] - pages[0]["char_start"]
    total_tokens = P.estimate_input_tokens(total_pages, total_chars)

    # Single-shot path
    if total_tokens <= ceiling:
        return [{
            "chunk_id": 0,
            "page_start": pages[0]["page"],
            "page_end": pages[-1]["page"],
            "char_start": pages[0]["char_start"],
            "char_end": pages[-1]["char_end"],
            "images": [p["image"] for p in pages if p.get("image")],
            "est_tokens": total_tokens,
        }]

    section_breaks = _section_break_chars(sections)

    chunks = []
    i = 0
    chunk_id = 0
    n = len(pages)
    while i < n:
        # Greedily accumulate pages until adding the next would exceed ceiling
        j = i
        acc_pages = 0
        start_char = pages[i]["char_start"]
        while j < n:
            cand_pages = (j - i) + 1
            cand_chars = pages[j]["char_end"] - start_char
            cand_tokens = P.estimate_input_tokens(cand_pages, cand_chars)
            if cand_tokens > ceiling and j > i:
                break  # adding page j overflows; stop before it
            j += 1
            acc_pages = cand_pages
        # [i, j) is the page slice for this chunk; j is exclusive
        end_idx = j - 1

        # Section-snap: if a section break falls within the last 10% of this
        # chunk's char span, prefer to end the chunk at the page containing
        # that break (cleaner semantic boundary). Only snap backwards (never
        # extend past ceiling).
        if end_idx > i and section_breaks:
            chunk_char_start = pages[i]["char_start"]
            chunk_char_end = pages[end_idx]["char_end"]
            span = chunk_char_end - chunk_char_start
            tol = span * P.CHUNK_BOUNDARY_TOLERANCE_PCT
            # find a section break within the tail tolerance window
            window_lo = chunk_char_end - tol
            snap_break = None
            for b in section_breaks:
                if window_lo <= b <= chunk_char_end:
                    snap_break = b
            if snap_break is not None:
                # find the page whose range contains snap_break; end there
                for k in range(i, end_idx + 1):
                    if pages[k]["char_start"] <= snap_break < pages[k]["char_end"]:
                        end_idx = max(i, k - 1) if k > i else k
                        break

        chunk_pages = pages[i:end_idx + 1]
        chunks.append({
            "chunk_id": chunk_id,
            "page_start": chunk_pages[0]["page"],
            "page_end": chunk_pages[-1]["page"],
            "char_start": chunk_pages[0]["char_start"],
            "char_end": chunk_pages[-1]["char_end"],
            "images": [p["image"] for p in chunk_pages if p.get("image")],
            "est_tokens": P.estimate_input_tokens(
                len(chunk_pages),
                chunk_pages[-1]["char_end"] - chunk_pages[0]["char_start"]),
        })
        chunk_id += 1

        # Advance with overlap: next chunk starts CHUNK_PAGE_OVERLAP pages back
        next_i = end_idx + 1
        if next_i < n and P.CHUNK_PAGE_OVERLAP > 0:
            next_i = max(i + 1, next_i - P.CHUNK_PAGE_OVERLAP)
        i = next_i

    return chunks


def plan_paper(row, force=False):
    """Build the full chunk plan for one paper. Returns the payload dict."""
    stable = row["stable_name"]
    out_path = P.CHUNKS_DIR / (stable + ".json")
    if out_path.exists() and not force:
        try:
            return json.loads(out_path.read_text(encoding="utf-8")), True
        except Exception:
            pass

    offsets = load_page_offsets(stable)
    if offsets is None:
        return {"stable_name": stable, "error": "no page_offsets.json"}, False
    pages = offsets.get("pages", [])
    sections = load_section_tags(stable)

    char_count = int(row.get("char_count") or 0)
    page_count = offsets.get("page_count", len(pages))
    image_dir = str(P.PDF_TEXT_DIR.parent / "PDFs_step4_images" / stable)

    plan_abc = plan_pages(pages, P.CHUNK_TOKEN_CEILING, sections)
    plan_d = plan_pages(pages, P.CHUNK_TOKEN_CEILING_PASS_D, sections)

    payload = {
        "stable_name": stable,
        "doi": row.get("doi", ""),
        "text_path": row.get("text_path", ""),
        "image_dir": image_dir,
        "page_count": page_count,
        "char_count": char_count,
        "single_shot": {
            "A": len(plan_abc) == 1,
            "B": len(plan_abc) == 1,
            "C": len(plan_abc) == 1,
            "D": len(plan_d) == 1,
        },
        "plans": {
            "A": plan_abc,
            "B": plan_abc,
            "C": plan_abc,
            "D": plan_d,
        },
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload, False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--stable", type=str, default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", action="store_true",
                        help="Print summary, write nothing.")
    args = parser.parse_args()

    P.ensure_dirs()

    if not P.PDF_TEXT_INDEX.exists():
        print("ERROR: pdf_text_index.csv missing. Run pdf_text_extract.py first.",
              file=sys.stderr)
        return 2

    rows = load_text_index()
    if args.stable:
        rows = [r for r in rows if r["stable_name"] == args.stable]
        if not rows:
            print("ERROR: stable_name not found in index:", args.stable, file=sys.stderr)
            return 2
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]

    print("Planning chunks for {} paper(s).".format(len(rows)))
    print("Ceilings: A/B/C={:,} tokens, D={:,} tokens. Page={} tok, char={} tok.".format(
        P.CHUNK_TOKEN_CEILING, P.CHUNK_TOKEN_CEILING_PASS_D,
        P.TOKENS_PER_PAGE_IMAGE, P.TOKENS_PER_CHAR))
    print()

    multi_abc = []
    multi_d = []
    errors = []
    total_chunks_abc = 0
    total_chunks_d = 0

    for i, row in enumerate(rows, 1):
        if args.report:
            offsets = load_page_offsets(row["stable_name"])
            if offsets is None:
                errors.append(row["stable_name"])
                continue
            pages = offsets.get("pages", [])
            sections = load_section_tags(row["stable_name"])
            pa = plan_pages(pages, P.CHUNK_TOKEN_CEILING, sections)
            pd = plan_pages(pages, P.CHUNK_TOKEN_CEILING_PASS_D, sections)
        else:
            payload, cached = plan_paper(row, force=args.force)
            if "error" in payload:
                errors.append(row["stable_name"])
                continue
            pa = payload["plans"]["A"]
            pd = payload["plans"]["D"]

        total_chunks_abc += len(pa)
        total_chunks_d += len(pd)
        flag = ""
        if len(pa) > 1:
            multi_abc.append((row["stable_name"], len(pa)))
            flag = "  <-- MULTI A/B/C ({} chunks)".format(len(pa))
        if len(pd) > 1 and len(pd) != len(pa):
            multi_d.append((row["stable_name"], len(pd)))
            flag += "  [D={} chunks]".format(len(pd))

        est = pa[0]["est_tokens"] if len(pa) == 1 else sum(c["est_tokens"] for c in pa)
        print("[{:3d}/{:3d}] {:<42s} pages={:>3d} ~{:>6,}tok A/B/C-chunks={}{}".format(
            i, len(rows), row["stable_name"][:42],
            (load_page_offsets(row['stable_name']) or {}).get('page_count', 0)
                if args.report else payload["page_count"],
            est, len(pa), flag))

    print()
    print("Summary:")
    print("  Papers planned         :", len(rows) - len(errors))
    print("  Errors (no offsets)    :", len(errors))
    print("  Single-shot A/B/C      :", (len(rows) - len(errors)) - len(multi_abc))
    print("  Multi-chunk A/B/C      :", len(multi_abc))
    print("  Total A/B/C chunks     :", total_chunks_abc)
    print("  Total D chunks         :", total_chunks_d)
    # Per-pass subagent call estimate: A+B+C each run their plan, D runs its
    # plan, E runs once per paper.
    papers_ok = len(rows) - len(errors)
    est_calls = 3 * total_chunks_abc + total_chunks_d + papers_ok  # +E once each
    print("  Est. subagent calls    : ~{:,}  (3xA/B/C-chunks + D-chunks + E-per-paper)".format(est_calls))
    if multi_abc:
        print()
        print("  Multi-chunk papers (A/B/C):")
        for name, n in sorted(multi_abc, key=lambda x: -x[1]):
            print("    {:<44s} {} chunks".format(name, n))
    if errors:
        print()
        print("  ERROR papers (no page_offsets.json):")
        for e in errors:
            print("   ", e)
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
