#!/usr/bin/env python3
"""
recover_titles_v2.py

For records in v2_active/merged_dataset.json that have NEITHER a title NOR
an abstract, query Crossref, OpenAlex, and EuropePMC to retrieve the title.
This recovers title metadata that recover_abstracts_v2.py discarded when
the associated abstract failed validation.

For each DOI, queries the three APIs in order, first non-empty title wins.

Output: v2_active/recovered_titles.jsonl
Schema per line:
    {
      "doi": "<lowercase DOI>",
      "title": "<recovered title or null>",
      "source": "crossref|openalex|europepmc|none",
      "attempts": [
        {"source": "...", "got_title": <bool>, "error": "..."}
      ]
    }

Resumable: re-running skips DOIs already in the output file.
"""

import json
import logging
import re
import sys
import time
import urllib.parse
from pathlib import Path

import requests

CONTACT_EMAIL = "laszlo.papp@meduniwien.ac.at"
USER_AGENT = f"IEEE-TRPMS-QC-MedImaging-Review/v2-titles (mailto:{CONTACT_EMAIL})"
HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

CORPUS  = Path("v2_active/merged_dataset.json")
OUTPUT  = Path("v2_active/recovered_titles.jsonl")
LOG     = Path("v2_active/recover_titles.log")


def normalize_ws(s):
    return re.sub(r"\s+", " ", s).strip() if s else ""


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&amp;", "&").replace("&lt;", "<")
           .replace("&gt;", ">").replace("&quot;", '"')
           .replace("&#x2013;", "-").replace("&#x2014;", "-"))
    return normalize_ws(s)


def http_get(url, attempts=3):
    backoff = 2.0
    r = None
    for i in range(attempts):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(backoff); backoff *= 2; continue
            return r
        except requests.exceptions.RequestException:
            if i == attempts - 1:
                raise
            time.sleep(backoff); backoff *= 2
    return r


def fetch_crossref_title(doi):
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='/')}?mailto={CONTACT_EMAIL}"
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, f"crossref_http_{getattr(r,'status_code','noresp')}"
        m = r.json().get("message", {})
        titles = m.get("title") or []
        if titles:
            return strip_html(titles[0]), None
        return None, "crossref_no_title_field"
    except Exception as e:
        return None, f"crossref_exception_{type(e).__name__}"


def fetch_openalex_title(doi):
    url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi, safe='/')}?mailto={CONTACT_EMAIL}"
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, f"openalex_http_{getattr(r,'status_code','noresp')}"
        j = r.json()
        t = j.get("title")
        if t:
            return strip_html(t), None
        return None, "openalex_no_title_field"
    except Exception as e:
        return None, f"openalex_exception_{type(e).__name__}"


def fetch_europepmc_title(doi):
    q = f'DOI:"{doi}"'
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
           f"?query={urllib.parse.quote(q)}&format=json&resultType=core")
    try:
        r = http_get(url)
        if r is None or r.status_code != 200:
            return None, f"epmc_http_{getattr(r,'status_code','noresp')}"
        results = r.json().get("resultList", {}).get("result", [])
        if not results:
            return None, "epmc_no_result"
        t = results[0].get("title")
        if t:
            return strip_html(t), None
        return None, "epmc_no_title_field"
    except Exception as e:
        return None, f"epmc_exception_{type(e).__name__}"


SOURCES = [
    ("crossref",  fetch_crossref_title),
    ("openalex",  fetch_openalex_title),
    ("europepmc", fetch_europepmc_title),
]


def already_processed():
    if not OUTPUT.exists():
        return set()
    done = set()
    with OUTPUT.open() as f:
        for line in f:
            try:
                r = json.loads(line)
                if r.get("doi"):
                    done.add(r["doi"].strip().lower())
            except json.JSONDecodeError:
                continue
    return done


def safe_str(x):
    return (x or "").strip()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG), logging.StreamHandler()],
    )
    log = logging.getLogger("recover_titles")

    if not CORPUS.exists():
        sys.exit(f"Corpus not found: {CORPUS}")

    corpus = json.load(CORPUS.open())
    targets = []
    for r in corpus:
        if not safe_str(r.get("title")) and not safe_str(r.get("abstract")):
            doi = safe_str(r.get("DOI") or r.get("doi")).lower()
            if doi:
                targets.append(doi)
    log.info(f"Empty-text DOIs to recover titles for: {len(targets)}")

    done = already_processed()
    log.info(f"Already attempted (resuming): {len(done)}")

    to_do = [d for d in targets if d not in done]
    log.info(f"To process this run: {len(to_do)}")

    stats = {"got_title": 0, "no_title": 0, "by_source": {"crossref":0,"openalex":0,"europepmc":0,"none":0}}

    with OUTPUT.open("a", encoding="utf-8") as out_f:
        for i, doi in enumerate(to_do, start=1):
            result = {"doi": doi, "title": None, "source": "none", "attempts": []}
            for sname, fn in SOURCES:
                try:
                    title, err = fn(doi)
                except Exception as e:
                    title, err = None, f"{sname}_exception_{type(e).__name__}"
                if title:
                    result["title"] = title
                    result["source"] = sname
                    result["attempts"].append({"source": sname, "got_title": True})
                    stats["got_title"] += 1
                    stats["by_source"][sname] += 1
                    break
                else:
                    result["attempts"].append({"source": sname, "got_title": False, "error": err})
                time.sleep(0.2)

            if not result["title"]:
                stats["no_title"] += 1
                stats["by_source"]["none"] += 1

            out_f.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_f.flush()

            if i % 25 == 0 or i == len(to_do):
                log.info(f"[{i}/{len(to_do)}] got={stats['got_title']} no_title={stats['no_title']} | "
                         f"CR={stats['by_source']['crossref']} OA={stats['by_source']['openalex']} "
                         f"EPMC={stats['by_source']['europepmc']}")

            time.sleep(1.0)

    log.info("Done.")
    log.info(f"Final stats: {json.dumps(stats)}")


if __name__ == "__main__":
    main()
