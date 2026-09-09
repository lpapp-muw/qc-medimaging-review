#!/usr/bin/env python3
"""
recover_abstracts_v2.py

Retrieve abstracts for a flat list of DOIs by querying, in order:
  1. Crossref          https://api.crossref.org/works/{doi}
  2. OpenAlex          https://api.openalex.org/works/doi:{doi}     (abstract_inverted_index)
  3. EuropePMC         https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}

First validated abstract wins (short-circuit).

Validation rules (same as Step-3 recover_abstracts.py):
  - >= 250 characters after whitespace normalization
  - >= 30 words
  - not a known placeholder ("no abstract available", etc.)
  - not substring-contained in or containing the title (both directions)
  - token Jaccard with title < 0.7

Inputs:
  doi_list.txt          one DOI per line

Outputs:
  recovered_abstracts_v2.jsonl      append-only, one JSON record per DOI processed
  recover_v2.log                    human-readable progress log

Resumability:
  on restart, skip any DOI already present in recovered_abstracts_v2.jsonl.

API etiquette:
  - Polite-pool user-agent with contact email
  - 1 second sleep between calls (configurable)
  - Retries on 429 / 5xx with exponential backoff up to 3 attempts per source
"""

import argparse
import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

CONTACT_EMAIL = "laszlo.papp@meduniwien.ac.at"
USER_AGENT = f"IEEE-TRPMS-QC-MedImaging-Review/v2 (mailto:{CONTACT_EMAIL})"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

PLACEHOLDERS = [
    "no abstract available",
    "abstract unavailable",
    "abstract not available",
    "no abstract is available",
    "abstract: no abstract",
    "the abstract is not available",
]

MIN_CHARS = 250
MIN_WORDS = 30
JACCARD_MAX = 0.7


def normalize_ws(s):
    return re.sub(r"\s+", " ", s).strip() if s else ""


def strip_html(s):
    """Remove common HTML tags returned by Crossref / OpenAlex abstracts."""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#x2013;", "-").replace("&#x2014;", "-")
    return normalize_ws(s)


def token_set(s):
    return set(re.findall(r"\w+", s.lower()))


def jaccard(a, b):
    sa, sb = token_set(a), token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def validate_abstract(abstract, title):
    if not abstract:
        return False, "empty"
    abs_norm = normalize_ws(abstract)
    if len(abs_norm) < MIN_CHARS:
        return False, f"too_short_chars_{len(abs_norm)}"
    if len(abs_norm.split()) < MIN_WORDS:
        return False, f"too_few_words_{len(abs_norm.split())}"
    abs_lower = abs_norm.lower()
    for ph in PLACEHOLDERS:
        if ph in abs_lower:
            return False, "placeholder_text"
    if title:
        title_norm = normalize_ws(title)
        if title_norm and title_norm.lower() in abs_lower:
            return False, "title_substring_of_abstract"
        if abs_lower in title_norm.lower():
            return False, "abstract_substring_of_title"
        j = jaccard(abs_norm, title_norm)
        if j >= JACCARD_MAX:
            return False, f"jaccard_too_high_{j:.2f}"
    return True, "ok"


def http_get(url, attempts=3):
    """GET with simple retry on 429/5xx."""
    backoff = 2.0
    r = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            return r
        except requests.exceptions.RequestException as e:
            if i == attempts - 1:
                raise
            time.sleep(backoff)
            backoff *= 2
    return r


def fetch_crossref(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}?mailto={CONTACT_EMAIL}"
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, None, f"crossref_http_{getattr(r, 'status_code', 'noresp')}"
        m = r.json().get("message", {})
        raw_abs = m.get("abstract")
        title_list = m.get("title") or []
        title = title_list[0] if title_list else ""
        if raw_abs:
            return strip_html(raw_abs), title, None
        return None, title, "crossref_no_abstract_field"
    except Exception as e:
        return None, None, f"crossref_exception_{type(e).__name__}"


def fetch_openalex(doi):
    url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}?mailto={CONTACT_EMAIL}"
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, None, f"openalex_http_{getattr(r, 'status_code', 'noresp')}"
        j = r.json()
        title = j.get("title") or ""
        idx = j.get("abstract_inverted_index")
        if not idx:
            return None, title, "openalex_no_abstract_index"
        positions = {}
        for word, plist in idx.items():
            for p in plist:
                positions[p] = word
        if not positions:
            return None, title, "openalex_empty_index"
        text = " ".join(positions[p] for p in sorted(positions))
        return strip_html(text), title, None
    except Exception as e:
        return None, None, f"openalex_exception_{type(e).__name__}"


def fetch_europepmc(doi):
    q = f'DOI:"{doi}"'
    url = (
        "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        f"?query={urllib.parse.quote(q)}&format=json&resultType=core"
    )
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, None, f"epmc_http_{getattr(r, 'status_code', 'noresp')}"
        results = r.json().get("resultList", {}).get("result", [])
        if not results:
            return None, None, "epmc_no_result"
        item = results[0]
        title = item.get("title") or ""
        abstract = item.get("abstractText")
        if not abstract:
            return None, title, "epmc_no_abstract_field"
        return strip_html(abstract), title, None
    except Exception as e:
        return None, None, f"epmc_exception_{type(e).__name__}"


SOURCES = [
    ("crossref", fetch_crossref),
    ("openalex", fetch_openalex),
    ("europepmc", fetch_europepmc),
]


def already_processed(out_path):
    if not out_path.exists():
        return set()
    done = set()
    with out_path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                if rec.get("doi"):
                    done.add(rec["doi"].strip().lower())
            except json.JSONDecodeError:
                continue
    return done


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--doi-list", default="dois_to_fetch.txt")
    ap.add_argument("--out", default="recovered_abstracts_v2.jsonl")
    ap.add_argument("--log", default="recover_v2.log")
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()

    doi_path = Path(args.doi_list)
    out_path = Path(args.out)
    log_path = Path(args.log)

    if not doi_path.exists():
        sys.exit(f"DOI list file not found: {doi_path}")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path), logging.StreamHandler()],
    )
    log = logging.getLogger("recover_v2")

    dois_all = []
    with doi_path.open("r", encoding="utf-8") as f:
        for line in f:
            d = line.strip()
            if d and not d.startswith("#"):
                dois_all.append(d.lower())
    log.info(f"Loaded {len(dois_all)} DOIs from {doi_path}")

    done = already_processed(out_path)
    log.info(f"Already processed (resuming): {len(done)}")

    dois_to_do = [d for d in dois_all if d not in done]
    if args.limit > 0:
        dois_to_do = dois_to_do[: args.limit]
    log.info(f"To process this run: {len(dois_to_do)}")

    stats = {"validated": 0, "no_abstract": 0,
             "by_source": {"crossref": 0, "openalex": 0, "europepmc": 0}}

    with out_path.open("a", encoding="utf-8") as out_f:
        for i, doi in enumerate(dois_to_do, start=1):
            result = {
                "doi": doi,
                "source": None,
                "abstract": None,
                "title": None,
                "validation_status": None,
                "attempts": [],
            }
            for source_name, fn in SOURCES:
                try:
                    abstract, title, err = fn(doi)
                except Exception as e:
                    abstract, title, err = None, None, f"{source_name}_exception_{type(e).__name__}"
                if abstract:
                    ok, vstatus = validate_abstract(abstract, title)
                    result["attempts"].append({
                        "source": source_name, "got_text": True,
                        "chars": len(normalize_ws(abstract)),
                        "validation": vstatus,
                    })
                    if ok:
                        result["source"] = source_name
                        result["abstract"] = normalize_ws(abstract)
                        result["title"] = title
                        result["validation_status"] = vstatus
                        stats["validated"] += 1
                        stats["by_source"][source_name] += 1
                        break
                else:
                    result["attempts"].append({"source": source_name, "got_text": False, "error": err})
                time.sleep(0.2)

            if not result["source"]:
                result["validation_status"] = "all_sources_failed"
                stats["no_abstract"] += 1

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()

            if i % 25 == 0 or i == len(dois_to_do):
                log.info(
                    f"[{i}/{len(dois_to_do)}] validated={stats['validated']} "
                    f"no_abstract={stats['no_abstract']} | "
                    f"CR={stats['by_source']['crossref']} "
                    f"OA={stats['by_source']['openalex']} "
                    f"EPMC={stats['by_source']['europepmc']}"
                )

            time.sleep(args.sleep)

    log.info("Done.")
    log.info(f"Final stats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
