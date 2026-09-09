#!/usr/bin/env python3
"""enrich_missing_doi_metadata_online_v3_freeplan.py

Online DOI metadata enrichment for rows where FoundInAnyInputFile == "NO".

What changed vs v2 (free plan friendly)
--------------------------------------
- Semantic Scholar is used WITHOUT an API key by default.
- A hard rate-limit is enforced for Semantic Scholar requests: by default
  **>= 1.0 second between HTTP requests** (configurable).
  This keeps the request rate safely below 10 requests/second.

Guarantees (same as before)
---------------------------
- Updates ONLY rows where FoundInAnyInputFile == NO (case-insensitive).
- Preserves all rows and all existing values.
- Fills a field only if it is currently empty.
- Does NOT concatenate multiple values with "|".
- Journal is populated with the *journal / venue / source title* (not publisher).
- If multiple candidate abstracts exist, it keeps the first "good" one by
  priority order; candidates that look garbled are skipped.

Online sources (no scraping)
----------------------------
1) DOI content negotiation (CSL-JSON) via https://doi.org/<doi>
2) Crossref REST API: https://api.crossref.org/works/<doi>
3) OpenAlex API: https://api.openalex.org/works/https://doi.org/<doi>
4) Semantic Scholar Graph API: https://api.semanticscholar.org/graph/v1/paper/DOI:<doi>
5) Unpaywall (optional; requires email) for OA PDF URL

Dependencies
------------
pip install pandas requests

Example
-------
python enrich_missing_doi_metadata_online_v3_freeplan.py \
  --input_csv merged_DOI_metadata_v3.csv \
  --output_csv merged_DOI_metadata_v3_online.csv \
  --mailto you@domain.com \
  --use_unpaywall

To be extra cautious with Semantic Scholar:
  --semanticscholar_min_interval_s 1.0

"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from html import unescape
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote

import pandas as pd
import requests


DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.I)


def canonicalize_doi(raw: Any) -> str:
    """Best-effort canonical DOI string (lowercased, no URL prefix)."""
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    # strip URL prefix
    s = re.sub(r"^\s*(https?://(dx\.)?doi\.org/)\s*", "", s, flags=re.I)
    # strip "doi:" prefix
    s = re.sub(r"^\s*doi\s*:\s*", "", s, flags=re.I)
    # trim surrounding punctuation often present in exports
    s = s.strip().strip(" \t\r\n.,;)")
    s = s.lstrip("(")
    return s.lower()


def first_nonempty(values: Iterable[Optional[str]]) -> str:
    for v in values:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def uniq_preserve_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for it in items:
        s = (it or "").strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def join_list(items: Iterable[str], sep: str = "; ") -> str:
    return sep.join(uniq_preserve_order(items))


def strip_html_tags(text: str) -> str:
    """Remove HTML/JATS tags and normalize whitespace."""
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = unescape(t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    # Remove obvious control chars (keep tab/newline as spaces)
    t = t.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def looks_garbled(text: str, *, min_len: int = 30) -> bool:
    """Heuristic: detect common mojibake/encoding-corruption patterns."""
    if not text:
        return True
    t = text

    # Lots of replacement chars indicates decoding issues
    if t.count("\ufffd") >= 2:
        return True

    # Common mojibake sequences
    bad_markers = [
        "Ã",  # UTF-8 decoded as latin-1
        "â€™",
        "â€“",
        "â€œ",
        "â€\x9d",
    ]
    if any(m in t for m in bad_markers):
        score = sum(t.count(m) for m in bad_markers)
        if score >= 2:
            return True

    # Too many non-printable characters
    printable = sum(1 for ch in t if ch.isprintable())
    if printable / max(1, len(t)) < 0.95:
        return True

    if len(t) < max(1, int(min_len)):
        return True

    return False


def parse_year_from_date_parts(date_parts: Any) -> str:
    """Crossref/CSL style: {"date-parts": [[YYYY, MM, DD]]} or [[YYYY, ...]]."""
    if isinstance(date_parts, dict):
        date_parts = date_parts.get("date-parts")
    if isinstance(date_parts, list) and date_parts:
        first = date_parts[0]
        if isinstance(first, list) and first:
            y = first[0]
            if isinstance(y, int) and 1500 <= y <= 2100:
                return str(y)
            if isinstance(y, str) and y.isdigit():
                return y
    return ""


def openalex_abstract_from_inverted_index(inv: Dict[str, List[int]]) -> str:
    """Reconstruct OpenAlex abstract from abstract_inverted_index."""
    if not isinstance(inv, dict) or not inv:
        return ""
    try:
        max_pos = max((max(pos) for pos in inv.values() if pos), default=-1)
        if max_pos < 0:
            return ""
        words = [""] * (max_pos + 1)
        for word, positions in inv.items():
            if not isinstance(positions, list):
                continue
            for p in positions:
                if isinstance(p, int) and 0 <= p <= max_pos and not words[p]:
                    words[p] = str(word)
        txt = " ".join([w for w in words if w])
        return normalize_text(txt)
    except Exception:
        return ""


@dataclass
class SourceResult:
    ok: bool
    data: Dict[str, Any]
    error: str = ""


class DiskCache:
    """Tiny on-disk cache to avoid re-querying the same DOI repeatedly."""

    def __init__(self, cache_dir: str):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _path(self, source: str, doi: str) -> str:
        safe = re.sub(r"[^a-z0-9_.-]+", "_", doi.lower())
        return os.path.join(self.cache_dir, f"{source}__{safe}.json")

    def get(self, source: str, doi: str) -> Optional[Dict[str, Any]]:
        p = self._path(source, doi)
        if not os.path.exists(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def set(self, source: str, doi: str, obj: Dict[str, Any]) -> None:
        p = self._path(source, doi)
        try:
            with open(p, "w", encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


class RateLimiter:
    """Simple per-process minimum-interval rate limiter."""

    def __init__(self, min_interval_s: float):
        self.min_interval_s = max(0.0, float(min_interval_s))
        self._last_t: float = 0.0

    def wait(self) -> None:
        if self.min_interval_s <= 0:
            return
        now = time.monotonic()
        if self._last_t > 0:
            elapsed = now - self._last_t
            if elapsed < self.min_interval_s:
                time.sleep(self.min_interval_s - elapsed)
        self._last_t = time.monotonic()


def request_json(
    session: requests.Session,
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 25.0,
    max_retries: int = 4,
    sleep_s: float = 0.25,
    rate_limiter: Optional[RateLimiter] = None,
) -> SourceResult:
    """GET JSON with basic retry/backoff for 429/5xx.

    If `rate_limiter` is provided, it enforces a minimum interval between
    *HTTP requests* (including retries) for that call-site.
    """
    last_err = ""
    for attempt in range(max_retries + 1):
        try:
            if rate_limiter is not None:
                rate_limiter.wait()

            r = session.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return SourceResult(True, r.json())

            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                if retry_after and str(retry_after).isdigit():
                    wait = float(retry_after)
                else:
                    wait = sleep_s * (2 ** attempt)
                time.sleep(min(20.0, max(sleep_s, wait)))
                last_err = f"HTTP {r.status_code}"
                continue

            return SourceResult(False, {}, f"HTTP {r.status_code}")

        except Exception as e:
            last_err = str(e)
            time.sleep(sleep_s * (2 ** attempt))
            continue

    return SourceResult(False, {}, last_err or "request failed")


def fetch_doi_csl_json(session: requests.Session, doi: str, cache: DiskCache, sleep_s: float) -> SourceResult:
    cached = cache.get("doi_csl", doi)
    if cached is not None:
        return SourceResult(True, cached)

    url = f"https://doi.org/{quote(doi, safe='/')}"
    headers = {
        "Accept": "application/vnd.citationstyles.csl+json",
        "User-Agent": "doi-metadata-enricher/3.0",
    }
    res = request_json(session, url, headers=headers, sleep_s=sleep_s)
    if res.ok:
        cache.set("doi_csl", doi, res.data)
    return res


def fetch_crossref(session: requests.Session, doi: str, mailto: Optional[str], cache: DiskCache, sleep_s: float) -> SourceResult:
    cached = cache.get("crossref", doi)
    if cached is not None:
        return SourceResult(True, cached)

    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    ua = "doi-metadata-enricher/3.0"
    if mailto:
        ua += f" (mailto:{mailto})"
    headers = {"User-Agent": ua}

    res = request_json(session, url, headers=headers, sleep_s=sleep_s)
    if res.ok:
        cache.set("crossref", doi, res.data)
    return res


def fetch_openalex(session: requests.Session, doi: str, mailto: Optional[str], cache: DiskCache, sleep_s: float) -> SourceResult:
    cached = cache.get("openalex", doi)
    if cached is not None:
        return SourceResult(True, cached)

    ext_id = f"https://doi.org/{doi}"
    url = f"https://api.openalex.org/works/{quote(ext_id, safe='')}"
    params: Dict[str, Any] = {}
    if mailto:
        params["mailto"] = mailto
    headers = {"User-Agent": "doi-metadata-enricher/3.0"}

    res = request_json(session, url, params=params, headers=headers, sleep_s=sleep_s)
    if res.ok:
        cache.set("openalex", doi, res.data)
    return res


def fetch_semanticscholar(
    session: requests.Session,
    doi: str,
    cache: DiskCache,
    sleep_s: float,
    ss_rate_limiter: Optional[RateLimiter],
) -> SourceResult:
    """Semantic Scholar Graph API (no key required here).

    A per-request rate limiter is used to stay within free-plan rate limits.
    """
    cached = cache.get("semanticscholar", doi)
    if cached is not None:
        return SourceResult(True, cached)

    fields = "title,abstract,authors,venue,year,journal,url,externalIds,openAccessPdf"
    url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}"
    params = {"fields": fields}
    headers = {"User-Agent": "doi-metadata-enricher/3.0"}

    res = request_json(
        session,
        url,
        params=params,
        headers=headers,
        sleep_s=sleep_s,
        rate_limiter=ss_rate_limiter,
    )
    if res.ok:
        cache.set("semanticscholar", doi, res.data)
    return res


def fetch_unpaywall(session: requests.Session, doi: str, email: str, cache: DiskCache, sleep_s: float) -> SourceResult:
    cached = cache.get("unpaywall", doi)
    if cached is not None:
        return SourceResult(True, cached)

    url = f"https://api.unpaywall.org/v2/{quote(doi, safe='')}"
    params = {"email": email}
    headers = {"User-Agent": "doi-metadata-enricher/3.0"}

    res = request_json(session, url, params=params, headers=headers, sleep_s=sleep_s)
    if res.ok:
        cache.set("unpaywall", doi, res.data)
    return res


def parse_csl(obj: Dict[str, Any]) -> Dict[str, str]:
    title = normalize_text(str(obj.get("title") or "").strip())

    journal = ""
    ct = obj.get("container-title")
    if isinstance(ct, list) and ct:
        journal = normalize_text(str(ct[0]).strip())
    elif isinstance(ct, str):
        journal = normalize_text(ct.strip())

    year = parse_year_from_date_parts(obj.get("issued"))

    authors_list: List[str] = []
    for a in obj.get("author", []) if isinstance(obj.get("author"), list) else []:
        if not isinstance(a, dict):
            continue
        family = str(a.get("family") or "").strip()
        given = str(a.get("given") or "").strip()
        if family and given:
            authors_list.append(f"{family}, {given}")
        elif family:
            authors_list.append(family)
        elif given:
            authors_list.append(given)
    authors = join_list(authors_list, sep="; ")

    manuscript = normalize_text(str(obj.get("URL") or "").strip())

    abstract = normalize_text(str(obj.get("abstract") or "").strip())

    return {
        "Title": title,
        "Authors": authors,
        "Journal": journal,
        "PublicationYear": year,
        "Abstract": abstract,
        "ManuscriptLink": manuscript,
    }


def parse_crossref(obj: Dict[str, Any]) -> Dict[str, str]:
    msg = obj.get("message", {}) if isinstance(obj, dict) else {}

    title = ""
    t = msg.get("title")
    if isinstance(t, list) and t:
        title = normalize_text(str(t[0]).strip())

    journal = ""
    ct = msg.get("container-title")
    if isinstance(ct, list) and ct:
        journal = normalize_text(str(ct[0]).strip())

    year = ""
    for key in ["published-print", "published-online", "issued", "created"]:
        y = parse_year_from_date_parts(msg.get(key))
        if y:
            year = y
            break

    authors_list: List[str] = []
    for a in msg.get("author", []) if isinstance(msg.get("author"), list) else []:
        if not isinstance(a, dict):
            continue
        family = str(a.get("family") or "").strip()
        given = str(a.get("given") or "").strip()
        if family and given:
            authors_list.append(f"{family}, {given}")
        elif family:
            authors_list.append(family)
        elif given:
            authors_list.append(given)
    authors = join_list(authors_list, sep="; ")

    aff_list: List[str] = []
    for a in msg.get("author", []) if isinstance(msg.get("author"), list) else []:
        if not isinstance(a, dict):
            continue
        affs = a.get("affiliation")
        if isinstance(affs, list):
            for aff in affs:
                if isinstance(aff, dict):
                    nm = str(aff.get("name") or "").strip()
                    if nm:
                        aff_list.append(normalize_text(nm))
    affiliations = join_list(aff_list, sep="; ")

    abstract = ""
    if isinstance(msg.get("abstract"), str):
        abstract = normalize_text(strip_html_tags(msg["abstract"]))

    keywords = ""
    subj = msg.get("subject")
    if isinstance(subj, list) and subj:
        keywords = join_list([normalize_text(str(s)) for s in subj if str(s).strip()], sep="; ")

    manuscript = ""
    links = msg.get("link")
    if isinstance(links, list):
        pdfs: List[str] = []
        others: List[str] = []
        for lk in links:
            if not isinstance(lk, dict):
                continue
            u = str(lk.get("URL") or "").strip()
            if not u:
                continue
            ct2 = str(lk.get("content-type") or "").lower()
            if "pdf" in ct2:
                pdfs.append(u)
            else:
                others.append(u)
        manuscript = first_nonempty(pdfs) or first_nonempty(others)

    if not manuscript:
        manuscript = str(msg.get("URL") or "").strip()

    return {
        "Title": title,
        "Authors": authors,
        "Affiliations": affiliations,
        "Journal": journal,
        "PublicationYear": year,
        "Abstract": abstract,
        "Keywords": keywords,
        "ManuscriptLink": normalize_text(manuscript),
    }


def parse_openalex(obj: Dict[str, Any]) -> Dict[str, str]:
    title = normalize_text(str(obj.get("title") or "").strip())
    year = str(obj.get("publication_year") or "").strip()

    journal = ""
    primary_location = obj.get("primary_location")
    if isinstance(primary_location, dict):
        src = primary_location.get("source")
        if isinstance(src, dict):
            journal = normalize_text(str(src.get("display_name") or "").strip())

    authors_list: List[str] = []
    inst_list: List[str] = []
    authorships = obj.get("authorships")
    if isinstance(authorships, list):
        for au in authorships:
            if not isinstance(au, dict):
                continue
            nm = au.get("author", {}).get("display_name") if isinstance(au.get("author"), dict) else ""
            nm = normalize_text(str(nm or "").strip())
            if nm:
                authors_list.append(nm)
            insts = au.get("institutions")
            if isinstance(insts, list):
                for inst in insts:
                    if isinstance(inst, dict):
                        dn = normalize_text(str(inst.get("display_name") or "").strip())
                        if dn:
                            inst_list.append(dn)

    authors = join_list(authors_list, sep="; ")
    affiliations = join_list(inst_list, sep="; ")

    abstract = ""
    inv = obj.get("abstract_inverted_index")
    if isinstance(inv, dict) and inv:
        abstract = openalex_abstract_from_inverted_index(inv)

    keywords = ""
    concepts = obj.get("concepts")
    if isinstance(concepts, list) and concepts:
        kws: List[str] = []
        for c in concepts[:25]:
            if isinstance(c, dict):
                dn = normalize_text(str(c.get("display_name") or "").strip())
                if dn:
                    kws.append(dn)
        keywords = join_list(kws, sep="; ")

    manuscript = ""
    best_oa = obj.get("best_oa_location")
    if isinstance(best_oa, dict):
        manuscript = str(best_oa.get("pdf_url") or "").strip() or str(best_oa.get("landing_page_url") or "").strip()
    if not manuscript:
        oa_url = obj.get("open_access", {}).get("oa_url") if isinstance(obj.get("open_access"), dict) else ""
        manuscript = str(oa_url or "").strip()
    if not manuscript:
        lp = primary_location.get("landing_page_url") if isinstance(primary_location, dict) else ""
        manuscript = str(lp or "").strip()

    return {
        "Title": title,
        "Authors": authors,
        "Affiliations": affiliations,
        "Journal": journal,
        "PublicationYear": year,
        "Abstract": abstract,
        "Keywords": keywords,
        "ManuscriptLink": normalize_text(manuscript),
    }


def parse_semanticscholar(obj: Dict[str, Any]) -> Dict[str, str]:
    title = normalize_text(str(obj.get("title") or "").strip())
    year = str(obj.get("year") or "").strip()

    journal = ""
    j = obj.get("journal")
    if isinstance(j, dict):
        journal = normalize_text(str(j.get("name") or "").strip())
    if not journal:
        journal = normalize_text(str(obj.get("venue") or "").strip())

    authors_list: List[str] = []
    for a in obj.get("authors", []) if isinstance(obj.get("authors"), list) else []:
        if isinstance(a, dict):
            nm = normalize_text(str(a.get("name") or "").strip())
            if nm:
                authors_list.append(nm)
    authors = join_list(authors_list, sep="; ")

    abstract = normalize_text(str(obj.get("abstract") or "").strip())

    manuscript = ""
    oapdf = obj.get("openAccessPdf")
    if isinstance(oapdf, dict):
        manuscript = str(oapdf.get("url") or "").strip()
    if not manuscript:
        manuscript = str(obj.get("url") or "").strip()

    return {
        "Title": title,
        "Authors": authors,
        "Journal": journal,
        "PublicationYear": year,
        "Abstract": abstract,
        "ManuscriptLink": normalize_text(manuscript),
    }


def parse_unpaywall(obj: Dict[str, Any]) -> Dict[str, str]:
    best_pdf = ""
    best_loc = obj.get("best_oa_location")
    if isinstance(best_loc, dict):
        best_pdf = str(best_loc.get("url_for_pdf") or "").strip()
        if not best_pdf:
            best_pdf = str(best_loc.get("url") or "").strip()
    if not best_pdf:
        locs = obj.get("oa_locations")
        if isinstance(locs, list):
            for loc in locs:
                if not isinstance(loc, dict):
                    continue
                u = str(loc.get("url_for_pdf") or "").strip() or str(loc.get("url") or "").strip()
                if u:
                    best_pdf = u
                    break
    return {"ManuscriptLink": normalize_text(best_pdf)}


def pick_value(
    field: str,
    candidates: List[Tuple[str, str]],
    *,
    skip_garbled: bool = False,
    garbled_min_len: int = 30,
) -> Tuple[str, str]:
    """Pick the first acceptable value in order and return (value, source)."""
    for value, src in candidates:
        v = normalize_text(value)
        if not v:
            continue
        if skip_garbled and looks_garbled(v, min_len=garbled_min_len):
            continue
        return v, src
    return "", ""


def enrich_csv(
    input_csv: str,
    output_csv: str,
    sep: str,
    flag_col: str,
    flag_value: str,
    mailto: Optional[str],
    use_unpaywall: bool,
    sleep_s: float,
    semanticscholar_min_interval_s: float,
    cache_dir: str,
    provenance_json: str,
    max_records: Optional[int],
) -> None:
    df = pd.read_csv(input_csv, sep=sep, dtype=str, keep_default_na=False)

    base_cols = [
        "DOI",
        "Computed_DOI_URL",
        "DOILink",
        "Title",
        "Authors",
        "Affiliations",
        "Journal",
        "PublicationYear",
        "Abstract",
        "Keywords",
        "ManuscriptLink",
        flag_col,
    ]
    for c in base_cols:
        if c not in df.columns:
            df[c] = ""

    mask = df[flag_col].astype(str).str.upper().eq(flag_value.upper())
    idxs = list(df.index[mask])
    if max_records is not None:
        idxs = idxs[: max_records]

    cache = DiskCache(cache_dir)
    provenance: Dict[str, Any] = {}

    ss_limiter = RateLimiter(semanticscholar_min_interval_s)

    with requests.Session() as session:
        session.headers.update({"User-Agent": "doi-metadata-enricher/3.0"})

        for i, idx in enumerate(idxs, start=1):
            doi_raw = df.at[idx, "DOI"]
            doi = canonicalize_doi(doi_raw)
            if not doi or not DOI_PATTERN.search(doi):
                provenance[str(doi_raw)] = {"skipped": True, "reason": "invalid DOI"}
                continue

            doi_url = f"https://doi.org/{doi}"
            if not str(df.at[idx, "Computed_DOI_URL"]).strip():
                df.at[idx, "Computed_DOI_URL"] = doi_url
            if not str(df.at[idx, "DOILink"]).strip():
                df.at[idx, "DOILink"] = doi_url

            prov: Dict[str, Any] = {"doi": doi, "sources": {}, "used": {}}

            # Fetch sources
            csl_res = fetch_doi_csl_json(session, doi, cache, sleep_s)
            cr_res = fetch_crossref(session, doi, mailto, cache, sleep_s)
            oa_res = fetch_openalex(session, doi, mailto, cache, sleep_s)

            # Semantic Scholar: FREE PLAN rate-limited here
            ss_res = fetch_semanticscholar(session, doi, cache, sleep_s, ss_limiter)

            upw_res: Optional[SourceResult] = None
            if use_unpaywall:
                if not mailto:
                    prov["sources"]["unpaywall"] = {"ok": False, "error": "--mailto is required for Unpaywall"}
                else:
                    upw_res = fetch_unpaywall(session, doi, mailto, cache, sleep_s)

            prov["sources"]["doi_csl"] = {"ok": csl_res.ok, "error": csl_res.error}
            prov["sources"]["crossref"] = {"ok": cr_res.ok, "error": cr_res.error}
            prov["sources"]["openalex"] = {"ok": oa_res.ok, "error": oa_res.error}
            prov["sources"]["semanticscholar"] = {"ok": ss_res.ok, "error": ss_res.error}
            if upw_res is not None:
                prov["sources"]["unpaywall"] = {"ok": upw_res.ok, "error": upw_res.error}

            csl = parse_csl(csl_res.data) if csl_res.ok else {}
            cr = parse_crossref(cr_res.data) if cr_res.ok else {}
            oa = parse_openalex(oa_res.data) if oa_res.ok else {}
            ss = parse_semanticscholar(ss_res.data) if ss_res.ok else {}
            upw = parse_unpaywall(upw_res.data) if (upw_res and upw_res.ok) else {}

            def fill_if_empty(col: str, value: str, src: str) -> None:
                cur = str(df.at[idx, col]).strip()
                if cur:
                    return
                if value.strip():
                    df.at[idx, col] = value.strip()
                    prov["used"][col] = src

            # Title: Crossref -> CSL -> SemanticScholar -> OpenAlex
            title_val, title_src = pick_value(
                "Title",
                [
                    (cr.get("Title", ""), "crossref"),
                    (csl.get("Title", ""), "doi_csl"),
                    (ss.get("Title", ""), "semanticscholar"),
                    (oa.get("Title", ""), "openalex"),
                ],
                skip_garbled=True,
                garbled_min_len=10,
            )
            fill_if_empty("Title", title_val, title_src)

            # Authors: Crossref -> CSL -> SemanticScholar -> OpenAlex
            authors_val, authors_src = pick_value(
                "Authors",
                [
                    (cr.get("Authors", ""), "crossref"),
                    (csl.get("Authors", ""), "doi_csl"),
                    (ss.get("Authors", ""), "semanticscholar"),
                    (oa.get("Authors", ""), "openalex"),
                ],
            )
            fill_if_empty("Authors", authors_val, authors_src)

            # Journal: Crossref -> CSL -> SemanticScholar -> OpenAlex
            journal_val, journal_src = pick_value(
                "Journal",
                [
                    (cr.get("Journal", ""), "crossref"),
                    (csl.get("Journal", ""), "doi_csl"),
                    (ss.get("Journal", ""), "semanticscholar"),
                    (oa.get("Journal", ""), "openalex"),
                ],
            )
            fill_if_empty("Journal", journal_val, journal_src)

            # PublicationYear: Crossref -> CSL -> SemanticScholar -> OpenAlex
            year_val, year_src = pick_value(
                "PublicationYear",
                [
                    (cr.get("PublicationYear", ""), "crossref"),
                    (csl.get("PublicationYear", ""), "doi_csl"),
                    (ss.get("PublicationYear", ""), "semanticscholar"),
                    (oa.get("PublicationYear", ""), "openalex"),
                ],
            )
            fill_if_empty("PublicationYear", year_val, year_src)

            # Abstract: SemanticScholar -> Crossref -> OpenAlex -> CSL
            abstract_val, abstract_src = pick_value(
                "Abstract",
                [
                    (ss.get("Abstract", ""), "semanticscholar"),
                    (cr.get("Abstract", ""), "crossref"),
                    (oa.get("Abstract", ""), "openalex"),
                    (csl.get("Abstract", ""), "doi_csl"),
                ],
                skip_garbled=True,
                garbled_min_len=30,
            )
            fill_if_empty("Abstract", abstract_val, abstract_src)

            # Affiliations: Crossref -> OpenAlex
            aff_val, aff_src = pick_value(
                "Affiliations",
                [
                    (cr.get("Affiliations", ""), "crossref"),
                    (oa.get("Affiliations", ""), "openalex"),
                ],
            )
            fill_if_empty("Affiliations", aff_val, aff_src)

            # Keywords: Crossref subjects -> OpenAlex concepts
            kw_val, kw_src = pick_value(
                "Keywords",
                [
                    (cr.get("Keywords", ""), "crossref"),
                    (oa.get("Keywords", ""), "openalex"),
                ],
            )
            fill_if_empty("Keywords", kw_val, kw_src)

            # ManuscriptLink: Unpaywall -> OpenAlex -> SemanticScholar -> Crossref -> CSL -> DOI URL
            manuscript_val, manuscript_src = pick_value(
                "ManuscriptLink",
                [
                    (upw.get("ManuscriptLink", ""), "unpaywall"),
                    (oa.get("ManuscriptLink", ""), "openalex"),
                    (ss.get("ManuscriptLink", ""), "semanticscholar"),
                    (cr.get("ManuscriptLink", ""), "crossref"),
                    (csl.get("ManuscriptLink", ""), "doi_csl"),
                    (doi_url, "computed"),
                ],
            )
            fill_if_empty("ManuscriptLink", manuscript_val, manuscript_src)

            provenance[doi] = prov

            if i % 25 == 0:
                print(f"[{i}/{len(idxs)}] enriched missing DOI rows")

    df.to_csv(output_csv, sep=sep, index=False, quoting=csv.QUOTE_MINIMAL)
    with open(provenance_json, "w", encoding="utf-8") as f:
        json.dump(provenance, f, ensure_ascii=False, indent=2)

    print(f"Wrote: {output_csv}")
    print(f"Wrote provenance: {provenance_json}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_csv", required=True, help="Input CSV (semicolon-separated)")
    ap.add_argument("--output_csv", required=True, help="Output CSV (semicolon-separated)")
    ap.add_argument("--sep", default=";", help="CSV separator (default: ';')")
    ap.add_argument("--flag_col", default="FoundInAnyInputFile", help="Column used to select missing rows")
    ap.add_argument("--flag_value", default="NO", help="Value that indicates missing rows")
    ap.add_argument("--mailto", default=None, help="Contact email (recommended; required for Unpaywall)")
    ap.add_argument("--use_unpaywall", action="store_true", help="Also query Unpaywall for OA PDF URL")
    ap.add_argument(
        "--sleep_s",
        type=float,
        default=0.25,
        help="Base sleep for retries/backoff (seconds). Not the Semantic Scholar limiter.",
    )
    ap.add_argument(
        "--semanticscholar_min_interval_s",
        type=float,
        default=1.0,
        help="Minimum seconds between Semantic Scholar HTTP requests (default: 1.0).",
    )
    ap.add_argument("--cache_dir", default=".doi_meta_cache_v3", help="Cache dir for API responses")
    ap.add_argument("--provenance_json", default="provenance_online_enrichment_v3.json", help="Write provenance JSON")
    ap.add_argument("--max_records", type=int, default=None, help="Only enrich first N missing rows (debug)")
    args = ap.parse_args()

    enrich_csv(
        input_csv=args.input_csv,
        output_csv=args.output_csv,
        sep=args.sep,
        flag_col=args.flag_col,
        flag_value=args.flag_value,
        mailto=args.mailto,
        use_unpaywall=args.use_unpaywall,
        sleep_s=args.sleep_s,
        semanticscholar_min_interval_s=args.semanticscholar_min_interval_s,
        cache_dir=args.cache_dir,
        provenance_json=args.provenance_json,
        max_records=args.max_records,
    )


if __name__ == "__main__":
    main()
