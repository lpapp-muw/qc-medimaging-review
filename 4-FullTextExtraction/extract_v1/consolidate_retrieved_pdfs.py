"""
consolidate_retrieved_pdfs.py — Normalise newly-retrieved paywalled PDFs
into the canonical PDFs_step4/ folder with DOI-derived stable filenames.

Problem this solves:
  Co-authors (GK, MS) downloaded paywalled PDFs directly from publishers.
  Those files have arbitrary publisher names (main.pdf, 1-s2.0-S....pdf,
  s13360-025-06587-4.pdf, etc.). The extraction pipeline keys everything on
  DOI-derived stable names inside PDFs_step4/. This script identifies each
  retrieved PDF by the DOI PRINTED ON ITS FIRST PAGES (not by filename),
  cross-checks against the 191-candidate DOI list, and copies it into
  PDFs_step4/ under the stable name.

Matching strategy (in order):
  1. Content DOI: pdftotext first 3 pages, regex-hunt printed DOI(s),
     match against the candidate DOI set. Primary and most reliable;
     does not depend on filename.
  2. Filename DOI: parse a DOI out of the filename itself (handles cases
     where the file was already named with its DOI).
  3. Unmatched: written to consolidation_unmatched.csv for manual mapping.
     Nothing is silently dropped.

Safety:
  - Windows drag-and-drop adds NTFS Zone.Identifier streams; this script
    removes any '<file>:Zone.Identifier' sidecar it finds in the source
    folder (handoff §5).
  - Collision handling: if the stable target already exists in PDFs_step4/
    (e.g. an OA copy was fetched earlier), the retrieved file is NOT
    overwritten by default. --overwrite forces replacement. Either way a
    'collision' row is logged.
  - Copy, not move: source files are preserved (audit trail). Use
    --move to relocate instead of copy.

Candidate DOI source:
  step4_candidates_v2.xlsx, sheet 'all_candidates', column 'DOI'.
  Falls back to scanning all sheets for a 'DOI' column if needed.

Outputs:
  - PDFs_step4/<stable_name>.pdf for each matched file
  - consolidation_log.csv     (every source file: action, matched DOI, target)
  - consolidation_unmatched.csv (source files with no confident DOI match)

Python 3.8 compatible.

Usage:
  python3 consolidate_retrieved_pdfs.py --dry-run   # report, copy nothing
  python3 consolidate_retrieved_pdfs.py             # copy matched files
  python3 consolidate_retrieved_pdfs.py --overwrite # replace existing targets
  python3 consolidate_retrieved_pdfs.py --move      # move instead of copy
"""

import argparse
import csv
import re
import shutil
import subprocess
import sys
from pathlib import Path

import paths_step4 as P
from pdf_text_extract import stable_name_from_pdf


# ----------------------------------------------------------------------------
# DOI regex
# ----------------------------------------------------------------------------
# Standard DOI shape. Deliberately conservative on the suffix to avoid
# swallowing trailing punctuation / whitespace. We post-trim common
# trailing junk afterwards.
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9A-Z]+", re.IGNORECASE)

# Characters that frequently get glued to the end of a DOI in running text
_DOI_TRAILING_JUNK = ".,;)]}>\"'"


def _clean_doi(raw):
    """Lowercase, strip trailing junk, collapse known artifacts."""
    d = raw.strip().lower()
    # Strip trailing junk characters
    while d and d[-1] in _DOI_TRAILING_JUNK:
        d = d[:-1]
    # Some PDFs print 'https://doi.org/10....'; the regex already starts at
    # '10.' so no prefix handling needed. Strip a trailing '.pdf' if a
    # filename-derived DOI carried it.
    if d.endswith(".pdf"):
        d = d[:-4]
    return d


def extract_dois_from_text(text):
    """Return an ordered, de-duplicated list of candidate DOIs found in text."""
    seen = []
    seen_set = set()
    for m in _DOI_RE.finditer(text):
        d = _clean_doi(m.group(0))
        if d and d not in seen_set:
            seen_set.add(d)
            seen.append(d)
    return seen


def doi_from_filename(path):
    """Best-effort DOI parse from a filename.

    Publisher filenames sometimes encode the DOI with '_' substituted for
    '/' and '.', e.g. '10.1140_epjp_s13360-025-06587-4.pdf'. We try two
    interpretations:
      (a) the raw stem matched directly by the DOI regex
      (b) the stem with '_' -> '/' for the first separator and '_' -> '.'
          heuristics. This is lossy; only used as a fallback and always
          cross-checked against the candidate set, so a wrong guess simply
          fails to match rather than mis-assigning.
    """
    stem = path.stem
    candidates = []
    # (a) direct
    for d in extract_dois_from_text(stem):
        candidates.append(d)
    # (b) underscore-decoded: replace underscores with '/' then test;
    #     also a variant replacing underscores with '.'
    for repl in ("/", "."):
        decoded = stem.replace("_", repl)
        for d in extract_dois_from_text(decoded):
            if d not in candidates:
                candidates.append(d)
    return candidates


# ----------------------------------------------------------------------------
# Candidate DOI set
# ----------------------------------------------------------------------------
def load_candidate_dois():
    """Return a set of lowercased candidate DOIs from the workbook.

    Tries sheet 'all_candidates' first; falls back to any sheet with a
    'DOI' column. Requires openpyxl.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:
        print("ERROR: openpyxl not installed in this venv. Run: pip install openpyxl",
              file=sys.stderr)
        sys.exit(2)

    if not P.CANDIDATES_XLSX.exists():
        print("ERROR: candidates workbook missing at", P.CANDIDATES_XLSX, file=sys.stderr)
        sys.exit(2)

    wb = load_workbook(str(P.CANDIDATES_XLSX), data_only=True, read_only=True)
    dois = set()

    def harvest(ws):
        rows = ws.iter_rows(values_only=True)
        header = next(rows, None)
        if not header:
            return 0
        header_lc = [str(h).strip().lower() if h is not None else "" for h in header]
        if "doi" not in header_lc:
            return 0
        idx = header_lc.index("doi")
        n = 0
        for r in rows:
            if r is None or idx >= len(r):
                continue
            v = r[idx]
            if v:
                dois.add(str(v).strip().lower())
                n += 1
        return n

    if "all_candidates" in wb.sheetnames:
        harvest(wb["all_candidates"])
    if not dois:
        for sn in wb.sheetnames:
            if harvest(wb[sn]):
                break
    wb.close()
    return dois


# ----------------------------------------------------------------------------
# DOI -> stable name
# ----------------------------------------------------------------------------
_NON_ALNUM_RE = re.compile(r"[^A-Za-z0-9]+")


def stable_name_from_doi(doi):
    """Derive the canonical stable basename from a DOI.

    MUST be consistent with how PDFs_step4 files were originally named so
    that the text/image extractors (which derive stable names from PDF
    basenames) land on the same key. The existing PDFs_step4 convention is
    DOI with '/' -> '_' and '.' kept, e.g. '10.1142/s1469026825500105'
    became '10.1142_s1469026825500105'. We replicate by replacing only '/'
    with '_' and leaving '.' intact, then let stable_name_from_pdf-style
    folding apply downstream.

    Returns a filename WITHOUT extension.
    """
    # Replace path separator with underscore; keep dots and hyphens.
    name = doi.replace("/", "_")
    return name


# ----------------------------------------------------------------------------
# Zone.Identifier cleanup
# ----------------------------------------------------------------------------
def strip_zone_identifiers(folder):
    """Remove NTFS Zone.Identifier sidecar files left by Windows drag-drop."""
    removed = 0
    for f in folder.glob("*:Zone.Identifier"):
        try:
            f.unlink()
            removed += 1
        except Exception:
            pass
    # Some appear as a separate stream-file named '<name>:Zone.Identifier'
    for f in folder.iterdir():
        if ":Zone.Identifier" in f.name:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    return removed


# ----------------------------------------------------------------------------
# pdftotext first-N-pages
# ----------------------------------------------------------------------------
def first_pages_text(pdf_path, last_page=3):
    """Return text from pages 1..last_page via pdftotext."""
    cmd = ["pdftotext", "-f", "1", "-l", str(last_page), str(pdf_path), "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=60)
        return proc.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


# ----------------------------------------------------------------------------
# Per-file matcher
# ----------------------------------------------------------------------------
def match_pdf(pdf_path, candidate_dois):
    """Return (matched_doi, method, all_found) or (None, 'unmatched', all_found)."""
    # Strategy 1: content DOI
    text = first_pages_text(pdf_path, last_page=3)
    found = extract_dois_from_text(text)
    for d in found:
        if d in candidate_dois:
            return d, "content_doi", found

    # Strategy 2: filename DOI
    fname_candidates = doi_from_filename(pdf_path)
    for d in fname_candidates:
        if d in candidate_dois:
            return d, "filename_doi", found + fname_candidates

    return None, "unmatched", found + fname_candidates


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="Report actions but copy/move nothing.")
    parser.add_argument("--overwrite", action="store_true",
                        help="Replace an existing target in PDFs_step4.")
    parser.add_argument("--move", action="store_true",
                        help="Move source files instead of copying.")
    parser.add_argument("--last-page", type=int, default=3,
                        help="How many leading pages to scan for a DOI (default 3).")
    args = parser.parse_args()

    P.ensure_dirs()
    P.PDFS_STEP4.mkdir(parents=True, exist_ok=True)

    src = P.PDFS_PAYWALL_SRC
    if not src.exists():
        print("ERROR: source folder missing at", src, file=sys.stderr)
        print("Create it and copy the retrieved PDFs there first.", file=sys.stderr)
        return 2

    # Tool check
    if shutil.which("pdftotext") is None:
        print("ERROR: pdftotext not on PATH", file=sys.stderr)
        return 2

    # Zone.Identifier cleanup
    n_zone = strip_zone_identifiers(src)
    if n_zone:
        print("Removed {} Zone.Identifier sidecar(s).".format(n_zone))

    # Candidate DOIs
    candidate_dois = load_candidate_dois()
    print("Loaded {} candidate DOIs from workbook.".format(len(candidate_dois)))

    # Collect source PDFs
    src_pdfs = sorted(p for p in src.glob("*.pdf") if ":Zone.Identifier" not in p.name)
    if not src_pdfs:
        print("No PDFs found in", src)
        return 0
    print("Found {} retrieved PDF(s) in {}".format(len(src_pdfs), src))
    print()

    log_rows = []
    unmatched_rows = []
    n_copied = 0
    n_collision = 0
    n_unmatched = 0
    matched_dois_this_run = set()

    for i, pdf in enumerate(src_pdfs, 1):
        doi, method, found = match_pdf(pdf, candidate_dois)
        if doi is None:
            n_unmatched += 1
            unmatched_rows.append({
                "source_file": pdf.name,
                "dois_found": ";".join(found[:5]),
                "note": "no DOI matched candidate set; manual mapping needed",
            })
            print("[{:3d}/{:3d}] UNMATCHED  {:<40s} found={}".format(
                i, len(src_pdfs), pdf.name[:40], ";".join(found[:3]) or "none"))
            log_rows.append({
                "source_file": pdf.name, "matched_doi": "", "method": "unmatched",
                "target": "", "action": "skip",
            })
            continue

        stable = stable_name_from_doi(doi)
        target = P.PDFS_STEP4 / (stable + ".pdf")

        # Duplicate DOI within this run (two source files claim same DOI)
        dup_in_run = doi in matched_dois_this_run
        matched_dois_this_run.add(doi)

        action = ""
        if target.exists():
            n_collision += 1
            if args.overwrite and not args.dry_run:
                if args.move:
                    shutil.move(str(pdf), str(target))
                else:
                    shutil.copy2(str(pdf), str(target))
                action = "overwrote_existing"
                n_copied += 1
            else:
                action = "collision_kept_existing"
            print("[{:3d}/{:3d}] COLLISION  {:<40s} -> {} ({}, {})".format(
                i, len(src_pdfs), pdf.name[:40], target.name, method, action))
        else:
            if not args.dry_run:
                if args.move:
                    shutil.move(str(pdf), str(target))
                else:
                    shutil.copy2(str(pdf), str(target))
            action = "moved" if args.move else "copied"
            n_copied += 1
            dup_flag = " [DUP-DOI-IN-RUN]" if dup_in_run else ""
            print("[{:3d}/{:3d}] OK         {:<40s} -> {} ({}){}".format(
                i, len(src_pdfs), pdf.name[:40], target.name, method, dup_flag))

        log_rows.append({
            "source_file": pdf.name, "matched_doi": doi, "method": method,
            "target": target.name, "action": action,
        })

    # Write logs
    with open(P.CONSOLIDATION_LOG, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["source_file", "matched_doi", "method", "target", "action"])
        w.writeheader()
        w.writerows(log_rows)
    if unmatched_rows:
        with open(P.CONSOLIDATION_UNMATCHED, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["source_file", "dois_found", "note"])
            w.writeheader()
            w.writerows(unmatched_rows)

    # Summary
    print()
    mode = "DRY-RUN (no files written)" if args.dry_run else ("MOVE" if args.move else "COPY")
    print("Mode: {}".format(mode))
    print("Matched & {}     : {}".format("would-copy" if args.dry_run else "copied", n_copied))
    print("Collisions (existing): {}".format(n_collision))
    print("Unmatched            : {}".format(n_unmatched))
    print("Consolidation log    :", P.CONSOLIDATION_LOG)
    if unmatched_rows:
        print("Unmatched report     :", P.CONSOLIDATION_UNMATCHED)
    print()
    # Post-state count
    existing = len(list(P.PDFS_STEP4.glob("*.pdf")))
    print("PDFs_step4 now holds {} PDF(s).".format(existing))
    if n_unmatched:
        print("ACTION REQUIRED: {} file(s) need manual DOI mapping (see report).".format(n_unmatched))
    return 0


if __name__ == "__main__":
    sys.exit(main())
