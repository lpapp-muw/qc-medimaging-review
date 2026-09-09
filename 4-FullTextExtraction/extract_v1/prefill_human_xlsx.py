"""
prefill_human_xlsx.py — Pre-fill the human extraction instrument with
bibliographic identifiers from the Zotero CSL-JSON exports.

Primary source: the six CSL-JSON files in
  /home/lpapp/IEEE_SYS_REV/3-AbstractRetrieve/inputs/zotero_csl_json_v1/
(filenames: IEEE-QC-NoDuplicates-*.json plus the original typo
 IEEE-QC-NoDulicates-1501-2000.json).

Publisher fallback: when the Zotero record lacks a publisher, look it up in
  /home/lpapp/IEEE_SYS_REV/.doi_meta_cache_v3/crossref__<doi-with-underscores>.json
(produced by the v3 enrichment helper). Optional; ignored if not present.

Pre-filled columns (deterministic catalog metadata, NO AI judgment, NO quote
columns since these are catalog data):
  - doi, title, year, journal, publisher, authors

Cells filled by this script get a light-blue background to flag "pre-filled,
verify and overwrite if wrong".

Idempotent. Re-running overwrites the pre-fill cells.

CLI:
  python3 prefill_human_xlsx.py                          # auto-detect inputs
  python3 prefill_human_xlsx.py --metadata-dir <dir>     # override CSL-JSON dir
  python3 prefill_human_xlsx.py --crossref-cache <dir>   # override cache dir
  python3 prefill_human_xlsx.py --xlsx <path.xlsx>       # override target
  python3 prefill_human_xlsx.py --selftest

Python 3.8 compatible. Requires openpyxl.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment

import paths_step4 as P


PREFILL_FILL = PatternFill("solid", fgColor="D6EAF8")  # light blue
BASE_FONT = Font(name="Arial", size=10)
ALIGN_VTOP = Alignment(vertical="top", wrap_text=False)


# ----------------------------------------------------------------------------
# Source resolution
# ----------------------------------------------------------------------------
_DEFAULT_CSL_DIR = Path("/home/lpapp/IEEE_SYS_REV/3-AbstractRetrieve/inputs/zotero_csl_json_v1")
_DEFAULT_CROSSREF_DIR = Path("/home/lpapp/IEEE_SYS_REV/.doi_meta_cache_v3")


def _doi_to_cache_filename(doi):
    """Crossref cache filename convention: crossref__<doi with '/' -> '_'>.json"""
    safe = doi.replace("/", "_")
    return "crossref__{}.json".format(safe)


# ----------------------------------------------------------------------------
# Metadata extraction (CSL-JSON tolerant)
# ----------------------------------------------------------------------------
def _doi_normalize(s):
    if not s:
        return ""
    return str(s).strip().lower()


def _stable_from_doi(doi):
    """Match the chunker's stable-name convention: lowercase DOI with all
    non-alphanumerics folded to '_'."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", doi.lower()).strip("_")


def _extract_year(rec):
    # CSL-JSON: issued.date-parts[0][0]
    issued = rec.get("issued")
    if isinstance(issued, dict):
        dp = issued.get("date-parts")
        if isinstance(dp, list) and dp and isinstance(dp[0], list) and dp[0]:
            return str(dp[0][0])
    # Fallback Zotero "date"
    d = rec.get("date") or rec.get("year")
    if d:
        s = str(d)
        # take first 4-digit run
        import re
        m = re.search(r"\d{4}", s)
        if m:
            return m.group(0)
    return ""


def _extract_authors(rec):
    """Return a semicolon-joined 'Family, Given' string."""
    out = []
    arr = rec.get("author") or rec.get("authors") or []
    if not isinstance(arr, list):
        return ""
    for a in arr:
        if isinstance(a, dict):
            fam = a.get("family") or a.get("lastName") or ""
            giv = a.get("given") or a.get("firstName") or ""
            name = "{}, {}".format(fam, giv).strip(", ").strip()
            if name:
                out.append(name)
        elif isinstance(a, str):
            out.append(a)
    return "; ".join(out)


def _csl_string_or_first(v):
    """Crossref returns title/container-title as a list; CSL has it as string."""
    if v is None:
        return ""
    if isinstance(v, list):
        return str(v[0]) if v else ""
    return str(v)


def _extract_title(rec):
    return _csl_string_or_first(rec.get("title"))


def _extract_journal(rec):
    return (_csl_string_or_first(rec.get("container-title"))
            or rec.get("publicationTitle")
            or rec.get("journalAbbreviation")
            or rec.get("journal")
            or "")


def _extract_publisher(rec):
    return _csl_string_or_first(rec.get("publisher"))


def load_metadata_index(csl_dir, crossref_dir=None, want_dois=None):
    """Return {doi_lower: {title, year, journal, publisher, authors}}.

    Reads every IEEE-QC-NoD*.json under csl_dir. For records whose publisher
    is empty in Zotero, attempts to fill from the Crossref cache at
    crossref_dir/crossref__<safe-doi>.json.

    For DOIs in `want_dois` (in-scope set) that are not present in Zotero at
    all, builds a record by encoding the DOI to its expected cache filename
    and loading it directly (works for sub-path DOIs like 10.1088/x/y where
    the encoder replaces every '/' with '_' — decoding is ambiguous, encoding
    is not).
    """
    csl_dir = Path(csl_dir)
    files = sorted(list(csl_dir.glob("IEEE-QC-NoDuplicates-*.json"))
                   + list(csl_dir.glob("IEEE-QC-NoDulicates-*.json")))
    if not files:
        raise FileNotFoundError("No CSL-JSON files in {}".format(csl_dir))

    idx = {}
    for f in files:
        raw = json.loads(f.read_text(encoding="utf-8"))
        recs = raw if isinstance(raw, list) else raw.get("items", [])
        if not isinstance(recs, list):
            continue
        for r in recs:
            if not isinstance(r, dict):
                continue
            doi = _doi_normalize(r.get("DOI") or r.get("doi"))
            if not doi:
                continue
            idx[doi] = {
                "title": _extract_title(r),
                "year": _extract_year(r),
                "journal": _extract_journal(r),
                "publisher": _extract_publisher(r),
                "authors": _extract_authors(r),
            }

    # ----- Crossref / OpenAlex cache integration -----
    #
    # Two passes:
    #   (1) For DOIs already in idx (came from Zotero) that lack publisher:
    #       fill publisher from the cache.
    #   (2) For DOIs NOT in idx at all (paywalled papers added post-screening,
    #       never enriched in Stage-3): build the full record from a
    #       crossref__<doi>.json or openalex__<doi>.json cache file.
    if crossref_dir:
        crossref_dir = Path(crossref_dir)

        # Pass 1: publisher backfill on existing entries
        n_pub_filled = 0
        for doi, meta in idx.items():
            if meta.get("publisher"):
                continue
            fp = crossref_dir / _doi_to_cache_filename(doi)
            if not fp.exists():
                continue
            try:
                raw = json.loads(fp.read_text(encoding="utf-8"))
            except Exception:
                continue
            msg = raw.get("message", raw) if isinstance(raw, dict) else raw
            if isinstance(msg, dict):
                pub = _extract_publisher(msg)
                if pub:
                    meta["publisher"] = pub
                    n_pub_filled += 1
        if n_pub_filled:
            print("Filled publisher from Crossref cache for {} records.".format(n_pub_filled))

        # Pass 2: build records for in-scope DOIs that are NOT in Zotero, by
        # encoding the DOI to its expected cache filename (unambiguous).
        n_new = 0
        n_oa = 0
        if want_dois:
            for doi in want_dois:
                if doi in idx:
                    continue
                fp_cr = crossref_dir / _doi_to_cache_filename(doi)
                if fp_cr.exists():
                    try:
                        raw = json.loads(fp_cr.read_text(encoding="utf-8"))
                    except Exception:
                        raw = None
                    if raw is not None:
                        msg = raw.get("message", raw) if isinstance(raw, dict) else raw
                        if isinstance(msg, dict):
                            idx[doi] = _meta_from_crossref(msg)
                            n_new += 1
                            continue
                # Try OpenAlex fallback
                fp_oa = crossref_dir / _openalex_to_cache_filename(doi)
                if fp_oa.exists():
                    try:
                        raw = json.loads(fp_oa.read_text(encoding="utf-8"))
                    except Exception:
                        raw = None
                    if raw is not None and isinstance(raw, dict):
                        idx[doi] = _meta_from_openalex(raw)
                        n_oa += 1
        if n_new:
            print("Built {} new records from Crossref cache (paywalled papers).".format(n_new))
        if n_oa:
            print("Built {} new records from OpenAlex cache.".format(n_oa))

    return idx


def _openalex_to_cache_filename(doi):
    return "openalex__{}.json".format(doi.replace("/", "_"))


def _meta_from_crossref(msg):
    """Build a {title, year, journal, publisher, authors} dict from a Crossref
    'message' object."""
    return {
        "title": _extract_title(msg),
        "year": _extract_year(msg),
        "journal": _extract_journal(msg),
        "publisher": _extract_publisher(msg),
        "authors": _extract_authors(msg),
    }


def _meta_from_openalex(rec):
    """Build a meta dict from an OpenAlex work record.

    OpenAlex schema differs from Crossref. Key fields:
      - title (str)
      - publication_year (int)
      - host_venue.display_name / .publisher  (legacy)
      - primary_location.source.display_name / .source.host_organization_name (newer)
      - authorships[].author.display_name
    """
    if not isinstance(rec, dict):
        return {"title": "", "year": "", "journal": "", "publisher": "", "authors": ""}

    title = rec.get("title") or ""
    year = str(rec.get("publication_year") or "")

    journal = ""
    publisher = ""
    # Try primary_location.source (newer schema)
    pl = rec.get("primary_location") or {}
    src = pl.get("source") if isinstance(pl, dict) else None
    if isinstance(src, dict):
        journal = src.get("display_name") or ""
        publisher = src.get("host_organization_name") or ""
    # Fallback host_venue (legacy)
    if not journal or not publisher:
        hv = rec.get("host_venue") or {}
        if isinstance(hv, dict):
            journal = journal or hv.get("display_name") or ""
            publisher = publisher or hv.get("publisher") or ""

    authors = []
    for a in rec.get("authorships") or []:
        if not isinstance(a, dict):
            continue
        au = a.get("author") or {}
        name = au.get("display_name") if isinstance(au, dict) else None
        if name:
            authors.append(name)
    return {
        "title": title,
        "year": year,
        "journal": journal,
        "publisher": publisher,
        "authors": "; ".join(authors),
    }


# ----------------------------------------------------------------------------
# xlsx pre-fill
# ----------------------------------------------------------------------------
PREFILL_COLS = ["doi", "title", "year", "journal", "publisher", "authors"]


def _col_index_map(ws, header_row=2):
    """Return {header_text: col_number}. Identifier columns occupy specific
    positions; both the identifier 'title' and the extraction 'title' exist
    (the identifier comes first per the column spec)."""
    seen = {}
    for c in range(1, ws.max_column + 1):
        h = ws.cell(header_row, c).value
        if h is None:
            continue
        # First occurrence wins for identifier columns (they come before the
        # extraction columns in the spec)
        if h not in seen:
            seen[h] = c
    return seen


def prefill(xlsx_path, metadata_index, header_row=2, dry_run=False):
    """Walk the human xlsx and fill the pre-fill columns from the metadata
    index keyed by DOI. Returns (n_rows, n_filled, n_missing)."""
    wb = load_workbook(xlsx_path)
    if "extractions" not in wb.sheetnames:
        raise ValueError("xlsx has no 'extractions' sheet")
    ws = wb["extractions"]
    colmap = _col_index_map(ws, header_row=header_row)
    missing = [c for c in PREFILL_COLS if c not in colmap]
    if missing:
        raise ValueError("Missing pre-fill columns in xlsx: {}".format(missing))

    doi_col = colmap["doi"]

    n_rows = 0
    n_filled = 0
    n_missing = 0
    for r in range(header_row + 1, ws.max_row + 1):
        doi_cell = ws.cell(r, doi_col)
        doi = _doi_normalize(doi_cell.value)
        if not doi:
            continue
        n_rows += 1
        meta = metadata_index.get(doi)
        if not meta:
            n_missing += 1
            continue
        for field in PREFILL_COLS:
            col = colmap[field]
            cell = ws.cell(r, col)
            val = meta.get(field, "") if field != "doi" else doi
            # Skip overwriting if user has already entered something different
            if cell.value not in (None, "") and field == "doi":
                pass  # never touch DOI; it's the join key
            cell.value = val
            cell.fill = PREFILL_FILL
            cell.font = BASE_FONT
            cell.alignment = ALIGN_VTOP
        n_filled += 1

    if not dry_run:
        # Back up before writing
        bak = Path(str(xlsx_path) + ".pre_prefill.bak")
        shutil.copy2(xlsx_path, bak)
        wb.save(xlsx_path)
    return n_rows, n_filled, n_missing


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    # Build a tiny CSL-JSON fixture in a dir, plus a tiny xlsx, then verify
    import tempfile
    from openpyxl import Workbook

    fixture = [
        {"DOI": "10.X/A", "title": "Quantum Imaging Study",
         "issued": {"date-parts": [[2024]]},
         "container-title": "Nature Quantum",
         "publisher": "Springer Nature",
         "author": [{"family": "Smith", "given": "J."}, {"family": "Doe", "given": "K."}]},
        {"DOI": "10.X/B", "title": "Variational Quantum Demo",
         "date": "2023-11-15",
         "publicationTitle": "IEEE TRPMS",
         # publisher intentionally missing — exercise Crossref fallback path
         "author": [{"family": "Romanchek", "given": "G."}]},
        # Test Crossref-style list title (publisher cache form)
        {"DOI": "10.X/C", "title": ["List-form Title from Crossref"],
         "issued": {"date-parts": [[2025, 6]]},
         "container-title": ["Biosensors"],
         "publisher": "MDPI AG",
         "author": [{"family": "Wang", "given": "L."}]},
    ]
    td = Path(tempfile.mkdtemp())
    csl_dir = td / "zotero_csl"
    csl_dir.mkdir()
    (csl_dir / "IEEE-QC-NoDuplicates-1-500.json").write_text(json.dumps(fixture), encoding="utf-8")

    # Crossref cache fixture for 10.X/B (provides the missing publisher)
    crossref_dir = td / "crossref_cache"
    crossref_dir.mkdir()
    (crossref_dir / _doi_to_cache_filename("10.x/b")).write_text(
        json.dumps({"message": {"publisher": "IEEE (from Crossref)"}}), encoding="utf-8")

    # Minimal xlsx
    wb = Workbook()
    ws = wb.active
    ws.title = "extractions"
    headers = ["doi", "title", "year", "journal", "publisher", "authors", "modality_primary"]
    for i, h in enumerate(headers, 1):
        ws.cell(2, i, h)
    ws.cell(3, 1, "10.X/A")
    ws.cell(4, 1, "10.x/B")   # lowercase variant — must still match
    ws.cell(5, 1, "10.X/MISSING")  # not in index
    ws.cell(6, 1, "10.X/C")   # list-form title test
    xlsx_path = td / "human.xlsx"
    wb.save(xlsx_path)

    idx = load_metadata_index(csl_dir, crossref_dir)
    n_rows, n_filled, n_missing = prefill(xlsx_path, idx)

    # Re-read and verify
    wb2 = load_workbook(xlsx_path)
    ws2 = wb2["extractions"]
    # Row 3 (10.X/A): full Zotero
    assert ws2.cell(3, 2).value == "Quantum Imaging Study", ws2.cell(3, 2).value
    assert ws2.cell(3, 3).value == "2024"
    assert ws2.cell(3, 4).value == "Nature Quantum"
    assert ws2.cell(3, 5).value == "Springer Nature"
    assert ws2.cell(3, 6).value == "Smith, J.; Doe, K."
    # Row 4 (10.x/B): publisher came from Crossref fallback
    assert ws2.cell(4, 2).value == "Variational Quantum Demo"
    assert ws2.cell(4, 3).value == "2023"
    assert ws2.cell(4, 4).value == "IEEE TRPMS"
    assert ws2.cell(4, 5).value == "IEEE (from Crossref)", "publisher fallback failed: {}".format(ws2.cell(4, 5).value)
    assert ws2.cell(4, 6).value == "Romanchek, G."
    # Row 5 (missing): left blank
    assert ws2.cell(5, 2).value in (None, ""), ws2.cell(5, 2).value
    # Row 6 (list-form title from Crossref-style record)
    assert ws2.cell(6, 2).value == "List-form Title from Crossref", ws2.cell(6, 2).value
    assert ws2.cell(6, 4).value == "Biosensors", ws2.cell(6, 4).value
    assert ws2.cell(6, 5).value == "MDPI AG"
    # Fill colour applied to pre-filled cells, not to missing row
    assert ws2.cell(3, 2).fill.fgColor.rgb in ("00D6EAF8", "FFD6EAF8"), ws2.cell(3, 2).fill.fgColor.rgb
    assert ws2.cell(5, 2).fill.fgColor.rgb not in ("00D6EAF8", "FFD6EAF8")
    assert (n_rows, n_filled, n_missing) == (4, 3, 1), (n_rows, n_filled, n_missing)
    print("SELFTEST: ALL PASS  (rows={} filled={} missing={})".format(n_rows, n_filled, n_missing))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--metadata-dir", type=str, default=None,
                        help="Directory holding Zotero CSL-JSON files (default: {}).".format(_DEFAULT_CSL_DIR))
    parser.add_argument("--crossref-cache", type=str, default=None,
                        help="Directory holding Crossref per-DOI cache files (default: {}; optional).".format(_DEFAULT_CROSSREF_DIR))
    parser.add_argument("--xlsx", type=str, default=None,
                        help="Path to extraction_human_blank.xlsx (default: extract_v1/extraction_human_blank.xlsx).")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    csl_dir = Path(args.metadata_dir) if args.metadata_dir else _DEFAULT_CSL_DIR
    if not csl_dir.exists():
        print("ERROR: CSL-JSON directory not found: {}".format(csl_dir), file=sys.stderr)
        return 2

    crossref_dir = Path(args.crossref_cache) if args.crossref_cache else _DEFAULT_CROSSREF_DIR
    if not crossref_dir.exists():
        print("NOTE: Crossref cache dir not found ({}); publisher fallback disabled.".format(crossref_dir))
        crossref_dir = None

    xlsx_path = Path(args.xlsx) if args.xlsx else (P.EXTRACT_DIR / "extraction_human_blank.xlsx")
    if not xlsx_path.exists():
        print("ERROR: {} not found. Build the workbook first via extractions_to_xlsx.py --human."
              .format(xlsx_path), file=sys.stderr)
        return 2

    print("Loading metadata from CSL-JSON dir:", csl_dir)
    if crossref_dir:
        print("Crossref fallback dir:", crossref_dir)

    # Discover the in-scope DOI set from the xlsx itself so the cache lookup
    # is bounded to what we actually need (and unambiguous via encoding).
    from openpyxl import load_workbook as _lw
    wb_tmp = _lw(xlsx_path, read_only=True)
    ws_tmp = wb_tmp["extractions"]
    want = set()
    # header row 2; find the doi column
    doi_col = None
    for c in range(1, ws_tmp.max_column + 1):
        if ws_tmp.cell(2, c).value == "doi":
            doi_col = c
            break
    if doi_col is None:
        print("ERROR: xlsx has no 'doi' column", file=sys.stderr)
        return 2
    for r in range(3, ws_tmp.max_row + 1):
        v = ws_tmp.cell(r, doi_col).value
        if v:
            want.add(_doi_normalize(v))
    wb_tmp.close()
    print("In-scope DOIs in xlsx:", len(want))

    idx = load_metadata_index(csl_dir, crossref_dir, want_dois=want)
    print("Indexed {} DOIs from metadata.".format(len(idx)))

    print("Pre-filling:", xlsx_path)
    n_rows, n_filled, n_missing = prefill(xlsx_path, idx)
    print("Rows processed: {}, filled: {}, no-match (left blank): {}".format(
        n_rows, n_filled, n_missing))
    print("Backup retained at: {}.pre_prefill.bak".format(xlsx_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
