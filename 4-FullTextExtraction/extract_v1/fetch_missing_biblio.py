"""
fetch_missing_biblio.py — One-shot Crossref/OpenAlex fetch for DOIs that have
no on-disk biblio metadata.

Use case: paywalled papers added post-screening that were never enriched in
Stage-3. Reads the list of in-scope stable_names from chunks/*.json (giving
us the DOIs), checks the Zotero CSL-JSON exports and the existing Crossref
cache, and for any DOI absent from both sources, fetches from Crossref's
public API (polite-pool with a contact email user-agent) and writes the
response to .doi_meta_cache_v3/crossref__<safe-doi>.json. OpenAlex is the
fallback if Crossref has no record.

prefill_human_xlsx.py picks up the new cache entries automatically on its
next run (the publisher-fallback path was upgraded inline here too: now
fills every missing field, not just publisher, when the DOI is absent from
Zotero entirely).

Run-once script. Idempotent: re-runs skip DOIs already cached.

CLI:
  python3 fetch_missing_biblio.py
  python3 fetch_missing_biblio.py --email <you@host>   # override contact email
  python3 fetch_missing_biblio.py --dry-run            # list missing DOIs only

Python 3.8 compatible. Requires `requests`.
"""

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests", file=sys.stderr)
    sys.exit(2)

import paths_step4 as P


CSL_DIR = Path("/home/lpapp/IEEE_SYS_REV/3-AbstractRetrieve/inputs/zotero_csl_json_v1")
CROSSREF_CACHE = Path("/home/lpapp/IEEE_SYS_REV/.doi_meta_cache_v3")
DEFAULT_EMAIL = "laszlo.papp@meduniwien.ac.at"

CROSSREF_API = "https://api.crossref.org/works/{doi}"
OPENALEX_API = "https://api.openalex.org/works/https://doi.org/{doi}"
TIMEOUT = 15
DELAY_S = 0.10  # polite delay between requests


def _doi_to_cache_filename(doi):
    return "crossref__{}.json".format(doi.replace("/", "_"))


def _openalex_to_cache_filename(doi):
    return "openalex__{}.json".format(doi.replace("/", "_"))


# ----------------------------------------------------------------------------
# Discover scope: in-scope DOIs from chunks/
# ----------------------------------------------------------------------------
def load_inscope_dois():
    dois = []
    for f in sorted(P.CHUNKS_DIR.glob("*.json")):
        try:
            plan = json.loads(f.read_text(encoding="utf-8"))
            doi = (plan.get("doi") or "").strip().lower()
            if doi:
                dois.append(doi)
        except Exception:
            pass
    return dois


def zotero_dois():
    out = set()
    for f in sorted(list(CSL_DIR.glob("IEEE-QC-NoDuplicates-*.json"))
                    + list(CSL_DIR.glob("IEEE-QC-NoDulicates-*.json"))):
        try:
            recs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(recs, list):
            for r in recs:
                if isinstance(r, dict):
                    doi = (r.get("DOI") or r.get("doi") or "").strip().lower()
                    if doi:
                        out.add(doi)
    return out


def crossref_cached(doi):
    return (CROSSREF_CACHE / _doi_to_cache_filename(doi)).exists()


# ----------------------------------------------------------------------------
# Fetchers
# ----------------------------------------------------------------------------
def fetch_crossref(doi, email):
    url = CROSSREF_API.format(doi=quote(doi, safe="/"))
    headers = {"User-Agent": "QC-MedImg-SysRev/1.0 (mailto:{})".format(email)}
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, "request_failed:{}".format(e)
    if r.status_code == 404:
        return None, "not_found"
    if r.status_code != 200:
        return None, "http_{}".format(r.status_code)
    try:
        return r.json(), None
    except Exception as e:
        return None, "json_parse:{}".format(e)


def fetch_openalex(doi, email):
    url = OPENALEX_API.format(doi=doi)
    headers = {"User-Agent": "QC-MedImg-SysRev/1.0 (mailto:{})".format(email)}
    try:
        r = requests.get(url, headers=headers, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, "request_failed:{}".format(e)
    if r.status_code == 404:
        return None, "not_found"
    if r.status_code != 200:
        return None, "http_{}".format(r.status_code)
    try:
        return r.json(), None
    except Exception as e:
        return None, "json_parse:{}".format(e)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    CROSSREF_CACHE.mkdir(parents=True, exist_ok=True)

    scope = set(load_inscope_dois())
    zotero = zotero_dois()
    print("In-scope DOIs from chunks/:        {}".format(len(scope)))
    print("DOIs covered by v1 Zotero CSL:     {}".format(len(scope & zotero)))

    # Missing = in scope, NOT in Zotero, AND not already cached
    missing = []
    cached_already = 0
    for doi in sorted(scope):
        if doi in zotero:
            continue
        if crossref_cached(doi):
            cached_already += 1
            continue
        missing.append(doi)

    print("Already cached (Crossref):         {}".format(cached_already))
    print("To fetch:                          {}".format(len(missing)))

    if args.dry_run:
        for doi in missing[:50]:
            print("  ", doi)
        if len(missing) > 50:
            print("  ... and {} more".format(len(missing) - 50))
        return 0

    if not missing:
        print("Nothing to fetch.")
        return 0

    n_cr = 0
    n_oa = 0
    n_fail = 0
    failures = []
    for i, doi in enumerate(missing, 1):
        time.sleep(DELAY_S)
        data, err = fetch_crossref(doi, args.email)
        if data:
            out = CROSSREF_CACHE / _doi_to_cache_filename(doi)
            out.write_text(json.dumps(data), encoding="utf-8")
            n_cr += 1
            if i % 10 == 0 or i == len(missing):
                print("  [{}/{}] crossref ok: {}".format(i, len(missing), doi))
            continue
        # OpenAlex fallback
        data2, err2 = fetch_openalex(doi, args.email)
        if data2:
            out = CROSSREF_CACHE / _openalex_to_cache_filename(doi)
            out.write_text(json.dumps(data2), encoding="utf-8")
            n_oa += 1
            print("  [{}/{}] openalex ok: {}".format(i, len(missing), doi))
            continue
        n_fail += 1
        failures.append((doi, err, err2))
        print("  [{}/{}] FAIL: {}  (crossref={}, openalex={})".format(
            i, len(missing), doi, err, err2))

    print()
    print("Done. Crossref: {}, OpenAlex fallback: {}, Failed: {}".format(n_cr, n_oa, n_fail))
    if failures:
        print("Failed DOIs:")
        for d, e1, e2 in failures:
            print("  {}  cr={}  oa={}".format(d, e1, e2))
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
