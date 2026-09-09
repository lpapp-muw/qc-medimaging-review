"""
section_tagger.py — Phase 1 Pre-pass section tagging.

Reads each PDF-text file produced by pdf_text_extract.py and emits a JSONL
record listing the byte offsets of section boundaries inside the text. The
chunker (next item) uses these offsets to snap chunk boundaries to section
breaks when within ±10% of the chunk target size.

Sections recognised (in priority order):
  - abstract
  - introduction
  - background / related_work
  - methods / methodology / materials_and_methods
  - experiments / experimental_setup
  - results
  - discussion
  - conclusion / conclusions
  - acknowledgments
  - references / bibliography
  - appendix
  - supplementary / supplementary_materials

Header-recognition heuristics:
  - Numbered headers (e.g. '1. Introduction', '2 Methods', 'III. Results',
    'IV Discussion') — Arabic, Roman, with optional period.
  - Unnumbered short-line headers (line of <= 60 chars, ends without
    punctuation, matches the section vocabulary).
  - 'ABSTRACT' or letter-spaced 'A B S T R A C T' (Stage-3 PDF-extraction
    heuristic reused).
  - Empty-line separation: a candidate header must be preceded by at least
    one empty line OR be at the start of the file, to avoid mid-paragraph
    false positives.

Outputs:
  - section_tags/<stable_name>.json per text file
    Format:
      {
        "stable_name": "...",
        "text_path": "...",
        "char_count": <int>,
        "sections": [
          {"name": "abstract",     "start": <byte>, "end": <byte>, "header_line": "Abstract"},
          {"name": "introduction", "start": <byte>, "end": <byte>, "header_line": "1. Introduction"},
          ...
        ],
        "notes": [...]
      }

Python 3.8 compatible.

Usage:
  python3 section_tagger.py                # process every text file
  python3 section_tagger.py --limit 5
  python3 section_tagger.py --stable NAME  # single text file
  python3 section_tagger.py --force        # reprocess even if json exists
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import paths_step4 as P


# ----------------------------------------------------------------------------
# Section vocabulary
# ----------------------------------------------------------------------------
# Each canonical section name maps to a list of header phrase regexes.
# All regexes are matched against a single trimmed line.
SECTION_VOCAB = [
    ("abstract", [
        r"abstract",
        r"a\s*b\s*s\s*t\s*r\s*a\s*c\s*t",  # letter-spaced
        r"summary",
    ]),
    ("introduction", [
        r"introduction",
        r"motivation",
    ]),
    ("background", [
        r"background",
        r"related\s+work",
        r"prior\s+work",
        r"preliminaries",
    ]),
    ("methods", [
        r"methods?",
        r"methodology",
        r"materials?\s+and\s+methods?",
        r"proposed\s+method(?:s|ology)?",
        r"approach",
        r"theory",
    ]),
    ("experiments", [
        r"experiments?",
        r"experimental\s+setup",
        r"experimental\s+protocol",
        r"experimental\s+evaluation",
        r"implementation",
        r"implementation\s+details",
        r"data\s+and\s+experiments",
    ]),
    ("results", [
        r"results?",
        r"results?\s+and\s+discussion",
        r"findings",
        r"evaluation",
    ]),
    ("discussion", [
        r"discussion",
        r"limitations",
    ]),
    ("conclusion", [
        r"conclusions?",
        r"concluding\s+remarks",
    ]),
    ("acknowledgments", [
        r"acknowledg(?:e)?ments?",
        r"acknowledgements",
    ]),
    ("references", [
        r"references",
        r"bibliography",
    ]),
    ("appendix", [
        r"appendix(?:\s+[a-z0-9]+)?",
        r"appendices",
    ]),
    ("supplementary", [
        r"supplementary",
        r"supplementary\s+material(s)?",
        r"supplementary\s+information",
        r"supporting\s+information",
    ]),
]

# Compiled per-section regex bundle
_COMPILED_VOCAB = [
    (name, re.compile("|".join("(?:{})".format(p) for p in patterns),
                       flags=re.IGNORECASE))
    for name, patterns in SECTION_VOCAB
]

# Numbering prefix: '1.', '2 ', 'III.', 'IV ', 'A.', etc.
# Allow up to two-level decimal (e.g. '2.1') because some papers begin a
# section with '2.1 Quantum encoding' without a top-level '2.' header above.
_NUMBER_PREFIX = re.compile(
    r"^(?:"
    r"(?:[0-9]{1,2}(?:\.[0-9]{1,2})?)\s*[\.\)]?\s+"  # Arabic with optional dot
    r"|"
    r"[IVXLC]{1,5}\s*[\.\)]?\s+"  # Roman numerals
    r"|"
    r"[A-H]\s*[\.\)]\s+"  # Lettered (A., B., etc., uppercase only)
    r")",
    flags=re.IGNORECASE,
)


# ----------------------------------------------------------------------------
# Header-line classifier
# ----------------------------------------------------------------------------
def classify_header_line(line):
    """If `line` looks like a section header, return the canonical section
    name. Otherwise return None.

    A line qualifies if it is short (<= 80 chars when trimmed), the trailing
    punctuation is light (no full sentences with period+space inside), and
    after stripping the numbering prefix the residual matches one of the
    section vocab regexes (full-line match).
    """
    if line is None:
        return None
    stripped = line.strip()
    if not stripped:
        return None
    if len(stripped) > 80:
        return None

    # Reject lines containing punctuation patterns typical of prose
    # (full stop followed by space + lowercase letter, or a colon followed
    # by significant content).
    if re.search(r"\.\s+[a-z]", stripped):
        return None
    if re.search(r":\s+\S{3,}", stripped):
        return None

    residual = stripped
    m = _NUMBER_PREFIX.match(stripped)
    if m:
        residual = stripped[m.end():].strip()
    if not residual:
        return None

    # Strip trailing punctuation that does not affect classification.
    residual_clean = residual.rstrip(":.;").strip()
    if not residual_clean:
        return None

    for name, regex in _COMPILED_VOCAB:
        if regex.fullmatch(residual_clean):
            return name
    return None


# ----------------------------------------------------------------------------
# Section extraction
# ----------------------------------------------------------------------------
def tag_sections(text):
    """Scan `text` and return a list of section dicts and any notes.

    Algorithm:
      1. Split into lines, keeping track of each line's byte offset.
      2. For each line, evaluate classify_header_line().
      3. A header candidate is accepted only if preceded by an empty line
         (or is the start of the file) — this rejects mid-paragraph false
         positives like '... in our methods we ...'.
      4. Build sections by pairing each accepted header with the byte
         offset of the NEXT header (or end-of-text).
      5. Repository-cruft handling (mirrors Stage-3 v1 §4.4 finding):
         if multiple 'abstract' headers are found, keep only the LAST one
         that precedes any 'introduction' / 'methods' header. The earlier
         ones are often citation/title pages on university-repository PDFs.
    """
    lines = text.split("\n")
    offsets = []
    cursor = 0
    for ln in lines:
        offsets.append(cursor)
        cursor += len(ln) + 1  # +1 for the newline
    offsets.append(cursor)  # sentinel = total length

    candidates = []
    for i, ln in enumerate(lines):
        name = classify_header_line(ln)
        if name is None:
            continue
        # Must be preceded by empty line OR be start-of-file
        if i > 0 and lines[i - 1].strip() != "":
            continue
        candidates.append({
            "name": name,
            "start": offsets[i],
            "header_line": ln.strip(),
            "line_index": i,
        })

    notes = []

    # Repository-cruft handling: if multiple 'abstract' headers exist,
    # keep only the last one that precedes any 'introduction' header.
    abstract_indices = [k for k, c in enumerate(candidates) if c["name"] == "abstract"]
    if len(abstract_indices) > 1:
        intro_indices = [k for k, c in enumerate(candidates) if c["name"] == "introduction"]
        first_intro = intro_indices[0] if intro_indices else None
        if first_intro is not None:
            keep = [k for k in abstract_indices if k < first_intro]
            keep_one = keep[-1] if keep else abstract_indices[-1]
        else:
            keep_one = abstract_indices[-1]
        drop_set = set(k for k in abstract_indices if k != keep_one)
        if drop_set:
            notes.append("dropped {} repeated 'abstract' header(s) (repository-cruft heuristic)"
                         .format(len(drop_set)))
        candidates = [c for k, c in enumerate(candidates) if k not in drop_set]

    # Sort by start (should already be sorted, but defensive)
    candidates.sort(key=lambda c: c["start"])

    # Build sections by pairing each header with the next header's start
    sections = []
    for i, c in enumerate(candidates):
        end_offset = candidates[i + 1]["start"] if (i + 1) < len(candidates) else offsets[-1]
        sections.append({
            "name": c["name"],
            "start": c["start"],
            "end": end_offset,
            "header_line": c["header_line"],
        })

    return sections, notes


# ----------------------------------------------------------------------------
# Per-file processing
# ----------------------------------------------------------------------------
def process_one_text(text_path, force=False):
    stable = text_path.stem
    out_path = P.SECTION_TAGS_DIR / (stable + ".json")
    if out_path.exists() and not force:
        try:
            existing = json.loads(out_path.read_text(encoding="utf-8"))
            return existing, True  # cached=True
        except Exception:
            pass  # fall through and reprocess

    text = text_path.read_text(encoding="utf-8", errors="replace")
    sections, notes = tag_sections(text)

    record = {
        "stable_name": stable,
        "text_path": str(text_path),
        "char_count": len(text),
        "sections": sections,
        "notes": notes,
    }
    out_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record, False


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N text files.")
    parser.add_argument("--stable", type=str, default="",
                        help="Process a single text file by stable_name (no .txt).")
    parser.add_argument("--force", action="store_true",
                        help="Reprocess even if json already exists.")
    args = parser.parse_args()

    P.ensure_dirs()

    if not P.PDF_TEXT_DIR.exists():
        print("ERROR: PDF text directory missing at", P.PDF_TEXT_DIR, file=sys.stderr)
        print("Run pdf_text_extract.py first.", file=sys.stderr)
        return 2

    if args.stable:
        target = P.PDF_TEXT_DIR / (args.stable + ".txt")
        if not target.exists():
            print("ERROR: text file not found:", target, file=sys.stderr)
            return 2
        text_files = [target]
    else:
        text_files = sorted(P.PDF_TEXT_DIR.glob("*.txt"))
        if not text_files:
            print("ERROR: no .txt files in", P.PDF_TEXT_DIR, file=sys.stderr)
            return 2
    if args.limit and args.limit > 0:
        text_files = text_files[: args.limit]

    print("Tagging sections in {} text file(s).".format(len(text_files)))

    n_cached = 0
    n_processed = 0
    n_sections_total = 0
    coverage_buckets = {"0": 0, "1-2": 0, "3-5": 0, "6+": 0}
    t0 = time.time()

    for i, tp in enumerate(text_files, 1):
        try:
            record, cached = process_one_text(tp, force=args.force)
        except Exception as exc:
            print("[{:3d}/{:3d}] {} ERROR: {}".format(i, len(text_files), tp.name, exc))
            continue
        n = len(record["sections"])
        n_sections_total += n
        if cached:
            n_cached += 1
        else:
            n_processed += 1
        if n == 0:
            coverage_buckets["0"] += 1
        elif n <= 2:
            coverage_buckets["1-2"] += 1
        elif n <= 5:
            coverage_buckets["3-5"] += 1
        else:
            coverage_buckets["6+"] += 1
        names = ",".join(s["name"] for s in record["sections"][:8])
        print("[{:3d}/{:3d}] {:<35s} sections={:>2d} {}".format(
            i, len(text_files), tp.name[:35], n, names))

    print()
    print("Done. processed={}, cached={}, total_sections={}".format(
        n_processed, n_cached, n_sections_total))
    print("Section-count distribution: 0:{}  1-2:{}  3-5:{}  6+:{}".format(
        coverage_buckets["0"], coverage_buckets["1-2"],
        coverage_buckets["3-5"], coverage_buckets["6+"]))
    print("Wall time: {:.1f}s".format(time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
