"""
predatory_check.py — Screen the Step-4 corpus against free predatory-journal
lists, the Retraction Watch Database, and OpenAlex DOAJ-indexing flag.

For each paper in extractions_derived.jsonl:
  1. Match journal/publisher against:
       - Beall's List (archived static)
       - Stop Predatory Journals (SPJ, GitHub)
  2. Look up DOI in Retraction Watch DB (free CSV).
  3. Check OpenAlex `is_in_doaj` (soft positive-indicator, not predatory).

Emit per-paper verdict:
  predatory_status   : clean | flagged_predatory | retracted | unknown
  predatory_sources  : list of which check(s) flagged
  predatory_notes    : free-text adjudication note

Write predatory_results.jsonl + a human-readable predatory_report.txt for
manual review. Cabells false-positive risk is real; the report is for human
adjudication, not auto-exclusion. Final exclusion verdict is YOUR call after
reviewing the flagged subset.

Designed to be run ONCE after Step 4 completes. Idempotent.

CLI:
  python3 predatory_check.py                # full run (downloads lists)
  python3 predatory_check.py --no-download  # use cached lists in ./predatory_data/
  python3 predatory_check.py --selftest

Python 3.8 compatible. Requires `requests`.
"""

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. pip install requests", file=sys.stderr)
    sys.exit(2)

import paths_step4 as P


DATA_DIR = P.EXTRACT_DIR / "predatory_data"
RESULTS_JSONL = P.EXTRACT_DIR / "predatory_results.jsonl"
REPORT_TXT = P.EXTRACT_DIR / "predatory_report.txt"

# Free list sources (verified 2025)
# SPJ moved from .json to .csv format. Beall's standalone repo is gone; SPJ
# is the successor.
SPJ_JOURNALS_URL = "https://raw.githubusercontent.com/stop-predatory-journals/stop-predatory-journals.github.io/master/_data/journals.csv"
SPJ_PUBLISHERS_URL = "https://raw.githubusercontent.com/stop-predatory-journals/stop-predatory-journals.github.io/master/_data/publishers.csv"
SPJ_HIJACKED_URL = "https://raw.githubusercontent.com/stop-predatory-journals/stop-predatory-journals.github.io/master/_data/hijacked.csv"
# Crossref Labs serves the Retraction Watch full CSV via mailto query param.
RETRACTION_WATCH_URL = "https://api.labs.crossref.org/data/retractionwatch?mailto=laszlo.papp@meduniwien.ac.at"

OPENALEX_API = "https://api.openalex.org/works/https://doi.org/{doi}"
TIMEOUT = 60   # Retraction Watch CSV is large; needs more


# ----------------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------------
_WS = re.compile(r"\s+")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")


def normalise(s):
    if not s:
        return ""
    t = str(s).lower()
    t = _NON_ALNUM.sub(" ", t)
    t = _WS.sub(" ", t).strip()
    return t


# ----------------------------------------------------------------------------
# Free-list loaders
# ----------------------------------------------------------------------------
def _download(url, dest):
    headers = {"User-Agent": "QC-MedImg-SysRev/1.0 (mailto:laszlo.papp@meduniwien.ac.at)"}
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return True
        print("  HTTP {} for {}".format(r.status_code, url), file=sys.stderr)
        return False
    except requests.RequestException as e:
        print("  request_failed: {} ({})".format(url, e), file=sys.stderr)
        return False


def _read_csv_2cols(fp):
    """Read SPJ-style CSV and return list of {url, name, abbreviation}.
    Format observed in 2025: url, name[, abbreviation]. Some rows lack the
    third column. No header row in the master CSVs."""
    out = []
    try:
        with open(fp, encoding="utf-8") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                url = (row[0] if len(row) > 0 else "").strip()
                name = (row[1] if len(row) > 1 else "").strip()
                abbr = (row[2] if len(row) > 2 else "").strip()
                out.append({"url": url, "name": name, "abbreviation": abbr})
    except Exception as e:
        print("  CSV parse error in {}: {}".format(fp, e), file=sys.stderr)
    return out


def load_spj_journals(do_download=True):
    """SPJ journals.csv: columns url, name, abbreviation."""
    fp = DATA_DIR / "spj_journals.csv"
    if do_download or not fp.exists():
        if not _download(SPJ_JOURNALS_URL, fp):
            return []
    return _read_csv_2cols(fp)


def load_spj_publishers(do_download=True):
    """SPJ publishers.csv: same shape as journals."""
    fp = DATA_DIR / "spj_publishers.csv"
    if do_download or not fp.exists():
        if not _download(SPJ_PUBLISHERS_URL, fp):
            return []
    return _read_csv_2cols(fp)


def load_spj_hijacked(do_download=True):
    """SPJ hijacked.csv: titles that have been hijacked by predatory clones."""
    fp = DATA_DIR / "spj_hijacked.csv"
    if do_download or not fp.exists():
        if not _download(SPJ_HIJACKED_URL, fp):
            return []
    return _read_csv_2cols(fp)


def load_retraction_watch(do_download=True):
    """Crossref Labs hosts the Retraction Watch CSV. Columns include
    OriginalPaperDOI, RetractionDOI, Journal, Publisher, Subject, Reason, etc.
    Header row present. Returns set of lower-cased DOIs (original paper)."""
    fp = DATA_DIR / "retraction_watch.csv"
    if do_download or not fp.exists():
        if not _download(RETRACTION_WATCH_URL, fp):
            return set()
    dois = set()
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                for k in ("OriginalPaperDOI", "OriginalDOI", "DOI"):
                    v = row.get(k)
                    if v and v.strip() and v.strip().lower() != "unavailable":
                        dois.add(v.strip().lower())
                        break
    except Exception as e:
        print("  Retraction Watch CSV parse error: {}".format(e), file=sys.stderr)
    return dois


# ----------------------------------------------------------------------------
# Build lookup indices
# ----------------------------------------------------------------------------
def build_indices(do_download=True):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading predatory-journal lists...")
    spj_j = load_spj_journals(do_download)
    spj_p = load_spj_publishers(do_download)
    spj_h = load_spj_hijacked(do_download)
    rw = load_retraction_watch(do_download)

    # Index SPJ entries: rows are {url, name, abbreviation}
    spj_journal_titles = set()
    spj_journal_abbrevs = set()
    for rec in spj_j:
        if rec.get("name"):
            spj_journal_titles.add(normalise(rec["name"]))
        if rec.get("abbreviation"):
            spj_journal_abbrevs.add(normalise(rec["abbreviation"]))

    spj_publisher_names = set()
    for rec in spj_p:
        if rec.get("name"):
            spj_publisher_names.add(normalise(rec["name"]))

    spj_hijacked_titles = set()
    for rec in spj_h:
        if rec.get("name"):
            spj_hijacked_titles.add(normalise(rec["name"]))

    print("Loaded:")
    print("  SPJ journals:        {}".format(len(spj_journal_titles)))
    print("  SPJ journal abbrevs: {}".format(len(spj_journal_abbrevs)))
    print("  SPJ publishers:      {}".format(len(spj_publisher_names)))
    print("  SPJ hijacked:        {}".format(len(spj_hijacked_titles)))
    print("  Retraction Watch:    {} DOIs".format(len(rw)))
    return {
        "spj_journal_titles": spj_journal_titles,
        "spj_journal_abbrevs": spj_journal_abbrevs,
        "spj_publisher_names": spj_publisher_names,
        "spj_hijacked_titles": spj_hijacked_titles,
        "retraction_watch": rw,
    }


# ----------------------------------------------------------------------------
# OpenAlex DOAJ flag
# ----------------------------------------------------------------------------
def openalex_doaj(doi):
    """Return (is_in_doaj, openalex_record_found). None if request failed."""
    try:
        r = requests.get(OPENALEX_API.format(doi=doi),
                         headers={"User-Agent": "QC-MedImg-SysRev/1.0 (mailto:laszlo.papp@meduniwien.ac.at)"},
                         timeout=TIMEOUT)
    except requests.RequestException:
        return None, False
    if r.status_code != 200:
        return None, False
    try:
        rec = r.json()
    except Exception:
        return None, False
    # is_in_doaj lives on primary_location.source
    pl = rec.get("primary_location") or {}
    src = pl.get("source") or {}
    if isinstance(src, dict):
        return bool(src.get("is_in_doaj")), True
    return None, True


# ----------------------------------------------------------------------------
# Per-paper check
# ----------------------------------------------------------------------------
def check_paper(rec, idx, openalex=True):
    """Return verdict dict for one paper."""
    doi = (rec.get("doi") or "").strip().lower()
    passes = rec.get("passes", {})
    a = passes.get("A", {}) if isinstance(passes, dict) else {}
    journal = ""
    publisher = ""
    jentry = a.get("journal") if isinstance(a, dict) else None
    pentry = a.get("publisher") if isinstance(a, dict) else None
    if isinstance(jentry, dict):
        journal = jentry.get("value") or ""
    if isinstance(pentry, dict):
        publisher = pentry.get("value") or ""

    nj = normalise(journal)
    npub = normalise(publisher)

    sources = []
    notes = []

    # Predatory-journal name match (SPJ)
    if nj and nj in idx["spj_journal_titles"]:
        sources.append("SPJ_journal")
        notes.append("journal '{}' on SPJ predatory list".format(journal))
    if nj and nj in idx["spj_journal_abbrevs"]:
        sources.append("SPJ_journal_abbrev")
        notes.append("journal abbrev '{}' on SPJ predatory list".format(journal))
    if npub and npub in idx["spj_publisher_names"]:
        sources.append("SPJ_publisher")
        notes.append("publisher '{}' on SPJ predatory list".format(publisher))
    if nj and nj in idx["spj_hijacked_titles"]:
        sources.append("SPJ_hijacked")
        notes.append("journal '{}' appears on SPJ hijacked-titles list (predatory clone of legit journal)".format(journal))

    # Retraction
    retracted = False
    if doi and doi in idx["retraction_watch"]:
        sources.append("RetractionWatch")
        notes.append("DOI listed in Retraction Watch")
        retracted = True

    # OpenAlex DOAJ (soft positive)
    is_doaj = None
    if openalex and doi:
        try:
            is_doaj, found = openalex_doaj(doi)
        except Exception:
            is_doaj, found = None, False
        if is_doaj is True:
            notes.append("OpenAlex: journal indexed in DOAJ (positive signal)")
        time.sleep(0.05)  # polite

    # Status
    if retracted:
        status = "retracted"
    elif sources:
        status = "flagged_predatory"
    else:
        status = "clean"

    return {
        "doi": doi,
        "journal": journal,
        "publisher": publisher,
        "predatory_status": status,
        "predatory_sources": sources,
        "predatory_notes": "; ".join(notes),
        "openalex_in_doaj": is_doaj,
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-download", action="store_true",
                        help="Use cached lists in predatory_data/; do not refetch.")
    parser.add_argument("--no-openalex", action="store_true",
                        help="Skip OpenAlex DOAJ lookups (offline mode).")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not P.DERIVED_EXTRACTIONS.exists():
        print("ERROR: {} not found. Run compute_derived.py first.".format(
            P.DERIVED_EXTRACTIONS), file=sys.stderr)
        return 2

    idx = build_indices(do_download=not args.no_download)

    print()
    print("Screening papers against predatory lists + retraction DB...")
    results = []
    with open(P.DERIVED_EXTRACTIONS, encoding="utf-8") as fh:
        for i, line in enumerate(fh, 1):
            rec = json.loads(line)
            v = check_paper(rec, idx, openalex=not args.no_openalex)
            v["stable_name"] = rec.get("stable_name", "")
            results.append(v)
            if i % 20 == 0:
                print("  {} papers screened".format(i))

    # Write per-paper JSONL
    with open(RESULTS_JSONL, "w", encoding="utf-8") as fh:
        for v in results:
            fh.write(json.dumps(v) + "\n")
    print("Wrote", RESULTS_JSONL)

    # Counts
    n = len(results)
    n_flagged = sum(1 for v in results if v["predatory_status"] == "flagged_predatory")
    n_retracted = sum(1 for v in results if v["predatory_status"] == "retracted")
    n_clean = sum(1 for v in results if v["predatory_status"] == "clean")
    n_doaj = sum(1 for v in results if v.get("openalex_in_doaj") is True)

    # Human-readable report
    with open(REPORT_TXT, "w", encoding="utf-8") as out:
        out.write("Predatory journal screen — IEEE TRPMS systematic review\n")
        out.write("=" * 70 + "\n\n")
        out.write("Corpus: {} papers\n".format(n))
        out.write("  clean:              {}\n".format(n_clean))
        out.write("  flagged_predatory:  {}\n".format(n_flagged))
        out.write("  retracted:          {}\n".format(n_retracted))
        out.write("  DOAJ-indexed:       {} (positive signal, informational)\n".format(n_doaj))
        out.write("\n")
        out.write("Flagged and retracted papers require HUMAN ADJUDICATION.\n")
        out.write("The SPJ list can produce false positives; do not auto-exclude.\n")
        out.write("Read each entry and decide.\n\n")
        out.write("=" * 70 + "\n\n")

        if n_retracted:
            out.write("RETRACTED PAPERS ({}):\n".format(n_retracted))
            out.write("-" * 40 + "\n")
            for v in results:
                if v["predatory_status"] == "retracted":
                    out.write("\nDOI: {}\n".format(v["doi"]))
                    out.write("  stable: {}\n".format(v["stable_name"]))
                    out.write("  journal: {}\n".format(v["journal"]))
                    out.write("  publisher: {}\n".format(v["publisher"]))
                    out.write("  notes: {}\n".format(v["predatory_notes"]))
            out.write("\n" + "=" * 70 + "\n\n")

        if n_flagged:
            out.write("PREDATORY-FLAGGED PAPERS ({}):\n".format(n_flagged))
            out.write("-" * 40 + "\n")
            for v in results:
                if v["predatory_status"] == "flagged_predatory":
                    out.write("\nDOI: {}\n".format(v["doi"]))
                    out.write("  stable: {}\n".format(v["stable_name"]))
                    out.write("  journal: {}\n".format(v["journal"]))
                    out.write("  publisher: {}\n".format(v["publisher"]))
                    out.write("  sources: {}\n".format(", ".join(v["predatory_sources"])))
                    out.write("  notes: {}\n".format(v["predatory_notes"]))
            out.write("\n" + "=" * 70 + "\n\n")

        if not n_flagged and not n_retracted:
            out.write("No predatory or retracted papers detected.\n")

    print()
    print("Summary:")
    print("  Total:               {}".format(n))
    print("  Clean:               {}".format(n_clean))
    print("  Flagged predatory:   {}".format(n_flagged))
    print("  Retracted:           {}".format(n_retracted))
    print("  DOAJ-indexed:        {} (positive signal)".format(n_doaj))
    print()
    print("Report:", REPORT_TXT)
    print()
    print("Next step: review predatory_report.txt manually. Beall's flags often")
    print("are false positives. Decide per paper whether to exclude. Once you")
    print("have a final exclusion list, run patch_xlsx_predatory.py (TBD) to")
    print("add the predatory_status column to extractions_ai.xlsx and")
    print("extraction_human_blank.xlsx.")
    return 0


def _selftest():
    # Build a fake idx and one paper; verify match logic
    idx = {
        "spj_journal_titles": {normalise("Bogus Journal of Quantum Imaging")},
        "spj_journal_abbrevs": set(),
        "spj_publisher_names": {normalise("ScamCo Publishing")},
        "spj_hijacked_titles": {normalise("Hijacked Journal Name")},
        "retraction_watch": {"10.x/retracted"},
    }
    # Test 1: clean
    rec1 = {"doi": "10.x/ok", "passes": {"A": {
        "journal": {"value": "Nature Quantum"},
        "publisher": {"value": "Springer Nature"}}}}
    v1 = check_paper(rec1, idx, openalex=False)
    assert v1["predatory_status"] == "clean", v1
    print("Test 1 (clean):", "PASS")
    # Test 2: predatory by journal
    rec2 = {"doi": "10.x/spj", "passes": {"A": {
        "journal": {"value": "Bogus Journal of Quantum Imaging"},
        "publisher": {"value": "Whoever"}}}}
    v2 = check_paper(rec2, idx, openalex=False)
    assert v2["predatory_status"] == "flagged_predatory"
    assert "SPJ_journal" in v2["predatory_sources"]
    print("Test 2 (SPJ journal hit):", "PASS")
    # Test 3: predatory by publisher
    rec3 = {"doi": "10.x/pub", "passes": {"A": {
        "journal": {"value": "Some Title"},
        "publisher": {"value": "ScamCo Publishing"}}}}
    v3 = check_paper(rec3, idx, openalex=False)
    assert v3["predatory_status"] == "flagged_predatory"
    assert "SPJ_publisher" in v3["predatory_sources"]
    print("Test 3 (SPJ publisher hit):", "PASS")
    # Test 4: retracted
    rec4 = {"doi": "10.x/retracted", "passes": {"A": {
        "journal": {"value": "Some Title"},
        "publisher": {"value": "Some Publisher"}}}}
    v4 = check_paper(rec4, idx, openalex=False)
    assert v4["predatory_status"] == "retracted"
    print("Test 4 (retracted):", "PASS")
    # Test 5: hijacked
    rec5 = {"doi": "10.x/hijacked", "passes": {"A": {
        "journal": {"value": "Hijacked Journal Name"},
        "publisher": {"value": "Whoever"}}}}
    v5 = check_paper(rec5, idx, openalex=False)
    assert v5["predatory_status"] == "flagged_predatory"
    assert "SPJ_hijacked" in v5["predatory_sources"]
    print("Test 5 (hijacked):", "PASS")
    # Test 6: retracted overrides predatory flag in status
    rec6 = {"doi": "10.x/retracted", "passes": {"A": {
        "journal": {"value": "Bogus Journal of Quantum Imaging"},
        "publisher": {"value": "ScamCo Publishing"}}}}
    v6 = check_paper(rec6, idx, openalex=False)
    assert v6["predatory_status"] == "retracted"
    assert len(v6["predatory_sources"]) >= 3  # retraction + spj_journal + spj_publisher
    print("Test 6 (retracted+predatory):", "PASS")
    print("SELFTEST: ALL PASS")


if __name__ == "__main__":
    sys.exit(main())
