#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doi_pdf_downloader_v3.py  (Python 3.8+)

Goal
-----
Given an XLSX with a single column titled "DOI", try to download a lawful PDF for each DOI
into ./PDFs (relative to this script). Update the XLSX by adding (or overwriting) a second
column titled "PDF" that contains either the downloaded filename or the string "Undetected".

What "lawful" means here
------------------------
This script only attempts:
- Publisher-hosted PDFs that are openly accessible (open access / author-archived / PMC, etc.)
- Open-access discovery services (Unpaywall/OpenAlex/Semantic Scholar/Europe PMC/Crossref)

It does NOT use Sci-Hub/LibGen or any piracy source. (PyPaperBot DOI mode uses Sci-Hub, so it is
intentionally NOT used for downloading.)

Why v3 (fix for ScienceDirect/Elsevier OA)
-----------------------------------------
ScienceDirect sometimes serves an intermediate "Preparing your download" HTML page for /pdfft
requests, which breaks naïve "must start with %PDF" checks.

v3 adds two robust Elsevier OA strategies:
1) If the DOI resolves to a ScienceDirect PII (e.g., S2590006425003230), try Elsevier CDN:
       https://ars.els-cdn.com/content/article/1-s2.0-<PII>-main.pdf
   This is commonly the direct PDF for Elsevier open-access content.
2) If any request returns HTML, v3 scans that HTML for embedded PDF URLs (reader.elsevier.com,
   pdf.sciencedirectassets.com, ars.els-cdn.com, *.pdf links) and retries those.

Dependencies
------------
Required:
    pip install openpyxl requests
Optional validation:
    pip install pypdf
    pip install pdf2doi    # optional; may require upgrading pdfminer.six

Recommended:
    Provide an email for Unpaywall:
        export UNPAYWALL_EMAIL="you@domain.com"
    or pass --unpaywall-email

Usage
-----
    python doi_pdf_downloader_v3.py --input my_dois.xlsx --unpaywall-email you@domain.com --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import quote, urljoin, urlparse

# ---- imports with friendly error messages ----
try:
    import openpyxl  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    print("ERROR: openpyxl is not installed. Run: pip install openpyxl", file=sys.stderr)
    raise

try:
    import requests  # type: ignore
except ModuleNotFoundError:  # pragma: no cover
    print("ERROR: requests is not installed. Run: pip install requests", file=sys.stderr)
    raise

DOI_COL = "DOI"
RESULT_COL = "PDF"

DEFAULT_TIMEOUT = 45
DEFAULT_SLEEP_S = 0.25
MAX_MB = 250

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
SCIDIRECT_PII_RE = re.compile(r"/pii/([^/?#]+)", re.IGNORECASE)

# PDF-ish URL heuristics (captures common publisher PDF endpoints even without .pdf extension)
PDF_URL_HINT_RE = re.compile(
    r"https?://[^\s\"'<>]+",
    flags=re.IGNORECASE,
)


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = re.sub(r"^\s*(https?://)?(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)
    return doi.strip()


def safe_filename_from_doi(doi: str) -> str:
    doi = normalize_doi(doi)
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", doi)
    if len(base) > 160:
        h = hashlib.sha256(doi.encode("utf-8")).hexdigest()[:16]
        base = base[:140] + "_" + h
    return base + ".pdf"


def is_pdf_bytes(b: bytes) -> bool:
    return b.startswith(b"%PDF-")


def is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return is_pdf_bytes(f.read(8))
    except Exception:
        return False


def request_session() -> requests.Session:
    s = requests.Session()
    # Browser-like headers reduce blocks; keep minimal to avoid breaking some servers.
    s.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "*/*",
        }
    )
    return s


def merged_headers(base: Dict[str, str], extra: Optional[Dict[str, str]]) -> Dict[str, str]:
    out = dict(base)
    if extra:
        out.update(extra)
    return out


@dataclass(frozen=True)
class Candidate:
    url: str
    source: str
    referer: str = ""


def dedupe_candidates(cands: Iterable[Candidate]) -> List[Candidate]:
    seen: Set[str] = set()
    out: List[Candidate] = []
    for c in cands:
        u = (c.url or "").strip()
        if not u:
            continue
        u = u.split("#", 1)[0]
        if u in seen:
            continue
        seen.add(u)
        out.append(Candidate(url=u, source=c.source, referer=c.referer))
    return out


def candidate_priority(c: Candidate) -> int:
    """
    Lower score is tried earlier.
    """
    u = c.url.lower()
    s = 100

    # direct PDFs first
    if u.endswith(".pdf"):
        s -= 30
    if "ars.els-cdn.com" in u:
        s -= 40
    if "reader.elsevier.com" in u:
        s -= 20
    if "pdf.sciencedirectassets.com" in u:
        s -= 20
    if "url_for_pdf" in c.source:
        s -= 25
    if "citation_pdf_url" in c.source:
        s -= 25
    if "openalex" in c.source:
        s -= 10
    if "semanticscholar" in c.source:
        s -= 10
    if "europepmc" in c.source or "pmc" in c.source:
        s -= 10
    if "crossref" in c.source:
        s -= 5
    if "sciencedirect" in c.source:
        s -= 5
    if "wiley" in c.source:
        s -= 3

    return s


def _get_json(sess: requests.Session, url: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[dict]:
    try:
        r = sess.get(url, timeout=timeout, headers={"Accept": "application/json"})
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


# -----------------------------
# OA discovery APIs
# -----------------------------
def _try_unpaywall(sess: requests.Session, doi: str, email: str) -> List[Candidate]:
    doi = normalize_doi(doi)
    if not email:
        return []
    api = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(email)}"
    data = _get_json(sess, api)
    if not isinstance(data, dict):
        return []

    out: List[Candidate] = []

    def add_loc(loc: object, label: str) -> None:
        if not isinstance(loc, dict):
            return
        pdf_url = loc.get("url_for_pdf")
        if isinstance(pdf_url, str) and pdf_url.startswith("http"):
            out.append(Candidate(url=pdf_url, source=f"unpaywall:{label}:url_for_pdf"))
        landing = loc.get("url")
        if isinstance(landing, str) and landing.startswith("http"):
            out.append(Candidate(url=landing, source=f"unpaywall:{label}:landing"))

    add_loc(data.get("best_oa_location"), "best")
    for loc in data.get("oa_locations") or []:
        add_loc(loc, "oa_locations")

    return dedupe_candidates(out)


def _try_openalex(sess: requests.Session, doi: str, mailto: str = "") -> List[Candidate]:
    doi = normalize_doi(doi)
    api = f"https://api.openalex.org/works/https://doi.org/{quote(doi)}"
    if mailto:
        api += f"?mailto={quote(mailto)}"
    data = _get_json(sess, api)
    if not isinstance(data, dict):
        return []
    out: List[Candidate] = []

    primary = data.get("primary_location") or {}
    if isinstance(primary, dict):
        pdf_url = primary.get("pdf_url")
        if isinstance(pdf_url, str) and pdf_url.startswith("http"):
            out.append(Candidate(url=pdf_url, source="openalex:primary_location:pdf_url"))
        landing = primary.get("landing_page_url")
        if isinstance(landing, str) and landing.startswith("http"):
            out.append(Candidate(url=landing, source="openalex:primary_location:landing_page_url"))

    oa = data.get("open_access") or {}
    if isinstance(oa, dict):
        oa_url = oa.get("oa_url")
        if isinstance(oa_url, str) and oa_url.startswith("http"):
            out.append(Candidate(url=oa_url, source="openalex:open_access:oa_url"))

    return dedupe_candidates(out)


def _try_semantic_scholar(sess: requests.Session, doi: str) -> List[Candidate]:
    doi = normalize_doi(doi)
    api = (
        "https://api.semanticscholar.org/graph/v1/paper/"
        + quote(f"DOI:{doi}")
        + "?fields=url,openAccessPdf,isOpenAccess"
    )
    data = _get_json(sess, api)
    if not isinstance(data, dict):
        return []
    out: List[Candidate] = []

    oapdf = data.get("openAccessPdf")
    if isinstance(oapdf, dict):
        u = oapdf.get("url")
        if isinstance(u, str) and u.startswith("http"):
            out.append(Candidate(url=u, source="semanticscholar:openAccessPdf:url"))
    landing = data.get("url")
    if isinstance(landing, str) and landing.startswith("http"):
        out.append(Candidate(url=landing, source="semanticscholar:url"))

    return dedupe_candidates(out)


def _try_europe_pmc(sess: requests.Session, doi: str) -> List[Candidate]:
    doi = normalize_doi(doi)
    api = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{quote(doi)}&format=json&pageSize=1"
    data = _get_json(sess, api)
    if not isinstance(data, dict):
        return []
    out: List[Candidate] = []

    result_list = data.get("resultList") or {}
    results = []
    if isinstance(result_list, dict):
        results = result_list.get("result") or []
    if not isinstance(results, list) or not results:
        return []

    first = results[0]
    if not isinstance(first, dict):
        return []

    pmcid = first.get("pmcid")
    if isinstance(pmcid, str) and pmcid.startswith("PMC"):
        out.append(Candidate(url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/", source="europepmc:pmcid:pmc-pdf"))
        out.append(Candidate(url=f"https://pmc.ncbi.nlm.nih.gov/articles/{pmcid}/pdf/{pmcid}.pdf", source="europepmc:pmcid:pmc-pdf-direct"))

    ft = first.get("fullTextUrlList") or {}
    if isinstance(ft, dict):
        ft_urls = ft.get("fullTextUrl") or []
        if isinstance(ft_urls, list):
            for item in ft_urls:
                if not isinstance(item, dict):
                    continue
                u = item.get("url")
                style = (item.get("documentStyle") or "").lower()
                if isinstance(u, str) and u.startswith("http"):
                    if "pdf" in style or u.lower().endswith(".pdf"):
                        out.append(Candidate(url=u, source="europepmc:fullTextUrlList:pdf"))
                    else:
                        out.append(Candidate(url=u, source="europepmc:fullTextUrlList:landing"))

    return dedupe_candidates(out)


def _try_crossref(sess: requests.Session, doi: str) -> List[Candidate]:
    doi = normalize_doi(doi)
    api = f"https://api.crossref.org/works/{quote(doi)}"
    data = _get_json(sess, api)
    if not isinstance(data, dict):
        return []
    msg = data.get("message") or {}
    if not isinstance(msg, dict):
        return []

    out: List[Candidate] = []
    for link in msg.get("link") or []:
        if not isinstance(link, dict):
            continue
        ct = (link.get("content-type") or "").lower()
        u = link.get("URL")
        if isinstance(u, str) and u.startswith("http"):
            if "pdf" in ct or u.lower().endswith(".pdf"):
                out.append(Candidate(url=u, source="crossref:link:pdf"))
            else:
                out.append(Candidate(url=u, source="crossref:link:other"))

    landing = msg.get("URL")
    if isinstance(landing, str) and landing.startswith("http"):
        out.append(Candidate(url=landing, source="crossref:URL"))

    return dedupe_candidates(out)


# -----------------------------
# DOI resolution / content negotiation
# -----------------------------
def resolve_doi(sess: requests.Session, doi: str) -> Tuple[Optional[str], bool]:
    doi = normalize_doi(doi)
    if not doi:
        return None, False
    url = f"https://doi.org/{quote(doi)}"
    try:
        with sess.get(
            url,
            allow_redirects=True,
            timeout=DEFAULT_TIMEOUT,
            headers={"Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.8"},
            stream=True,
        ) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            head = r.raw.read(8) if r.raw else b""
            is_pdf = ("pdf" in ctype) or is_pdf_bytes(head)
            return r.url, is_pdf
    except Exception:
        return None, False


def doi_accept_pdf_candidate(doi: str) -> Candidate:
    doi = normalize_doi(doi)
    return Candidate(url=f"https://doi.org/{quote(doi)}", source="doi:accept:application/pdf")


# -----------------------------
# Publisher heuristics
# -----------------------------
def sciencedirect_candidates(doi: str, landing_url: str) -> List[Candidate]:
    """
    If landing_url is on ScienceDirect and contains a PII, add:
    - Elsevier CDN direct PDF pattern (often works for OA)
    - ScienceDirect /pdfft and /pdf endpoints, with referer set.
    """
    out: List[Candidate] = []
    if not landing_url:
        return out

    host = urlparse(landing_url).netloc.lower()
    if "sciencedirect.com" not in host:
        return out

    m = SCIDIRECT_PII_RE.search(landing_url)
    if not m:
        return out
    pii = m.group(1)

    # 1) Elsevier CDN (common direct OA PDF)
    out.append(Candidate(
        url=f"https://ars.els-cdn.com/content/article/1-s2.0-{pii}-main.pdf",
        source="elsevier:ars.els-cdn:1-s2.0-pii-main",
        referer=landing_url,
    ))

    # 2) ScienceDirect endpoints (may redirect)
    base = f"https://www.sciencedirect.com/science/article/pii/{pii}"
    out.append(Candidate(url=f"{base}/pdfft?download=true&isDTMRedir=true", source="sciencedirect:pdfft", referer=landing_url))
    out.append(Candidate(url=f"{base}/pdf?download=true&isDTMRedir=true", source="sciencedirect:pdf", referer=landing_url))
    out.append(Candidate(url=base, source="sciencedirect:landing", referer=landing_url))

    return out


def wiley_candidates(doi: str, landing_url: Optional[str]) -> List[Candidate]:
    """
    Wiley OA sometimes works via these endpoints; often 403 in automated contexts.
    """
    doi = normalize_doi(doi)
    out: List[Candidate] = []
    if not doi:
        return out

    # Prefer onlinelibrary host (advanced.* is frequently blocked)
    out.append(Candidate(url=f"https://onlinelibrary.wiley.com/doi/pdf/{quote(doi)}", source="wiley:doi/pdf"))
    out.append(Candidate(url=f"https://onlinelibrary.wiley.com/doi/epdf/{quote(doi)}", source="wiley:doi/epdf"))
    out.append(Candidate(url=f"https://onlinelibrary.wiley.com/doi/pdfdirect/{quote(doi)}", source="wiley:doi/pdfdirect"))
    if landing_url:
        out.append(Candidate(url=landing_url, source="wiley:landing", referer=landing_url))
    return out


# -----------------------------
# HTML scraping for embedded PDF links
# -----------------------------
def looks_like_pdf_url(u: str) -> bool:
    ul = u.lower()
    if ul.endswith(".pdf"):
        return True
    # common "pdf without .pdf"
    if "reader.elsevier.com/reader/" in ul:
        return True
    if "pdf.sciencedirectassets.com" in ul:
        return True
    if "ars.els-cdn.com" in ul and "content/article" in ul:
        return True
    # general heuristic
    if "/pdf" in ul or "download" in ul or "epdf" in ul:
        return True
    return False


def extract_pdf_like_urls(text: str, base_url: str) -> List[str]:
    """
    Extract potential PDF URLs from HTML/JS text.
    """
    out: List[str] = []
    for m in PDF_URL_HINT_RE.finditer(text):
        u = m.group(0).strip().strip(").,;\"'")
        if not u:
            continue
        # Unescape common JSON escapes
        u = u.replace("\\/", "/")
        u = u.replace("&amp;", "&")

        if u.startswith("//"):
            u = "https:" + u
        if u.startswith("/"):
            u = urljoin(base_url, u)

        if u.startswith("http") and looks_like_pdf_url(u):
            out.append(u)
    return out


def try_html_scrape(sess: requests.Session, url: str) -> List[Candidate]:
    try:
        r = sess.get(url, timeout=DEFAULT_TIMEOUT, headers={"Accept": "text/html,application/xhtml+xml"})
        r.raise_for_status()
        urls = extract_pdf_like_urls(r.text, base_url=r.url)

        # meta citation_pdf_url also appears in HTML; quick regex for it.
        for m in re.finditer(r'citation_pdf_url["\']\s*content=["\']([^"\']+)["\']', r.text, flags=re.IGNORECASE):
            urls.append(urljoin(r.url, m.group(1)))

        out = [Candidate(url=u, source="html:scrape", referer=r.url) for u in urls]
        return dedupe_candidates(out)
    except Exception:
        return []


# -----------------------------
# JabRef integration (optional: metadata helper)
# -----------------------------
def jabref_executable() -> Optional[str]:
    if os.environ.get("JABREF_PATH"):
        p = Path(os.environ["JABREF_PATH"]).expanduser()
        if p.exists():
            return str(p)
    return shutil.which("jabref") or shutil.which("JabRef")


def try_jabref_urls(doi: str) -> List[Candidate]:
    exe = jabref_executable()
    if not exe:
        return []
    doi = normalize_doi(doi)
    tmp_dir = Path(".jabref_tmp")
    tmp_dir.mkdir(exist_ok=True)
    out_bib = tmp_dir / "tmp.bib"

    fetchers = ["CrossRef", "Crossref", "DOI", "Medline"]
    for fetcher in fetchers:
        try:
            if out_bib.exists():
                out_bib.unlink()
            cmd = [exe, "--nogui", f"--fetch={fetcher}:{doi}", "-o", f"{out_bib},bibtex"]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0 or (not out_bib.exists()) or out_bib.stat().st_size == 0:
                continue
            bib = out_bib.read_text(encoding="utf-8", errors="ignore")
            urls: List[Candidate] = []
            for m in re.finditer(r"\burl\s*=\s*[{\"\']([^}\"\']+)[}\"\']", bib, flags=re.IGNORECASE):
                u = m.group(1).strip()
                if u.startswith("http"):
                    urls.append(Candidate(url=u, source=f"jabref:{fetcher}:url"))
            if urls:
                return dedupe_candidates(urls)
        except Exception:
            continue
    return []


# -----------------------------
# Download logic
# -----------------------------
@dataclass
class DownloadAttempt:
    ok: bool
    followups: List[str]
    status_code: Optional[int] = None
    content_type: str = ""
    final_url: str = ""


def attempt_download(
    sess: requests.Session,
    url: str,
    out_path: Path,
    referer: str = "",
    timeout: int = DEFAULT_TIMEOUT,
    verbose: bool = False,
) -> DownloadAttempt:
    """
    Try to download URL as PDF. If the response is HTML, extract follow-up PDF-like URLs.
    """
    tmp = out_path.with_suffix(out_path.suffix + ".part")
    if tmp.exists():
        try:
            tmp.unlink()
        except Exception:
            pass

    # Decide Accept header
    accept_pdf = "application/pdf,*/*;q=0.8"
    accept_html = "text/html,application/xhtml+xml,*/*;q=0.8"

    headers_extra: Dict[str, str] = {}
    if referer:
        headers_extra["Referer"] = referer

    # Heuristic: if URL looks like a PDF endpoint, prefer PDF Accept.
    if looks_like_pdf_url(url) or url.lower().endswith(".pdf"):
        headers_extra["Accept"] = accept_pdf
    else:
        headers_extra["Accept"] = accept_html

    try:
        with sess.get(url, stream=True, timeout=timeout, allow_redirects=True, headers=headers_extra) as r:
            status = r.status_code
            ctype = (r.headers.get("Content-Type") or "").lower()
            final_url = r.url

            # Peek first bytes
            head = b""
            try:
                head = r.raw.read(8) if r.raw else b""
            except Exception:
                head = b""

            # If PDF, stream-save.
            if status == 200 and (is_pdf_bytes(head) or "application/pdf" in ctype):
                with tmp.open("wb") as f:
                    f.write(head)
                    for chunk in r.iter_content(chunk_size=1024 * 128):
                        if chunk:
                            f.write(chunk)
                            if f.tell() > MAX_MB * 1024 * 1024:
                                raise RuntimeError("PDF too large")
                tmp.replace(out_path)
                return DownloadAttempt(ok=is_pdf_file(out_path), followups=[], status_code=status, content_type=ctype, final_url=final_url)

            # Not a PDF: read some body and try to extract PDF-like URLs.
            body = b""
            try:
                # Read up to ~2 MB for link extraction.
                body = head + r.raw.read(2 * 1024 * 1024)
            except Exception:
                body = b""

            followups: List[str] = []
            if body:
                try:
                    text = body.decode("utf-8", errors="ignore")
                    followups = extract_pdf_like_urls(text, base_url=final_url)
                except Exception:
                    followups = []

            if verbose:
                print(f"    download failed as PDF (status={status}, ctype={ctype}, final={final_url})", flush=True)
                if followups:
                    print(f"    extracted {len(followups)} follow-up URL(s) from HTML", flush=True)

            return DownloadAttempt(ok=False, followups=followups, status_code=status, content_type=ctype, final_url=final_url)

    except Exception as e:
        if verbose:
            print(f"    exception while downloading: {e}", flush=True)
        return DownloadAttempt(ok=False, followups=[], status_code=None, content_type="", final_url="")


def validate_pdf_matches_doi(pdf_path: Path, doi: str, verbose: bool = False) -> Optional[bool]:
    """
    Best-effort validation:
    - If pdf2doi is installed and extracts a DOI, verify it matches.
    - Else if pypdf is installed, scan the first pages for the DOI.
    Returns:
        True  => matches
        False => definitely mismatch
        None  => unknown (no tool / not decisive)
    """
    doi_norm = normalize_doi(doi).lower()

    # 1) pdf2doi
    try:
        import pdf2doi  # type: ignore
        try:
            if hasattr(pdf2doi, "config"):
                try:
                    pdf2doi.config.set("verbose", False)
                    pdf2doi.config.set("store", False)
                    pdf2doi.config.set("webvalidation", False)
                except Exception:
                    pass
            res = pdf2doi.pdf2doi(str(pdf_path))  # type: ignore[attr-defined]
            if isinstance(res, dict):
                ident = res.get("identifier")
                if isinstance(ident, str):
                    m = DOI_PATTERN.search(ident)
                    if m:
                        extracted = normalize_doi(m.group(0)).lower()
                        if extracted == doi_norm:
                            return True
                        return False
        except Exception:
            pass
    except Exception:
        pass

    # 2) pypdf
    try:
        from pypdf import PdfReader  # type: ignore
        reader = PdfReader(str(pdf_path))
        text = ""
        for page in reader.pages[:2]:
            try:
                text += "\n" + (page.extract_text() or "")
            except Exception:
                continue
        if text.strip():
            found = {normalize_doi(m.group(0)).lower() for m in DOI_PATTERN.finditer(text)}
            if not found:
                return None
            if doi_norm in found:
                return True
            return False
    except Exception:
        return None

    return None


def collect_candidates(
    sess: requests.Session,
    doi: str,
    unpaywall_email: str = "",
    verbose: bool = False,
) -> List[Candidate]:
    doi = normalize_doi(doi)
    out: List[Candidate] = []

    landing_url, landing_is_pdf = resolve_doi(sess, doi)
    if landing_url and landing_is_pdf:
        out.append(Candidate(url=landing_url, source="doi:resolved:pdf"))

    # OA discovery
    if unpaywall_email:
        out.extend(_try_unpaywall(sess, doi, unpaywall_email))
    out.extend(_try_openalex(sess, doi, mailto=unpaywall_email))
    out.extend(_try_semantic_scholar(sess, doi))
    out.extend(_try_europe_pmc(sess, doi))
    out.extend(_try_crossref(sess, doi))

    # DOI content negotiation attempt
    out.append(doi_accept_pdf_candidate(doi))

    # JabRef URLs (metadata helper)
    out.extend(try_jabref_urls(doi))

    # Publisher heuristics
    if landing_url:
        out.extend(sciencedirect_candidates(doi, landing_url))
        out.extend(wiley_candidates(doi, landing_url))

    # HTML scrape landing (can surface embedded direct PDFs)
    if landing_url:
        out.append(Candidate(url=landing_url, source="doi:resolved:landing"))
        out.extend(try_html_scrape(sess, landing_url))

    out = dedupe_candidates(out)
    out.sort(key=candidate_priority)

    if verbose:
        print("  Candidates (top 25):", flush=True)
        for c in out[:25]:
            ref = f" (ref={c.referer})" if c.referer else ""
            print(f"    - {c.source}: {c.url}{ref}", flush=True)

    return out


def download_pdf_for_doi(
    sess: requests.Session,
    doi: str,
    pdf_dir: Path,
    unpaywall_email: str = "",
    validate: bool = True,
    verbose: bool = False,
) -> Optional[Path]:
    doi = normalize_doi(doi)
    if not doi:
        return None

    pdf_dir.mkdir(parents=True, exist_ok=True)
    out_path = pdf_dir / safe_filename_from_doi(doi)

    if out_path.exists() and is_pdf_file(out_path):
        return out_path

    candidates = collect_candidates(sess, doi, unpaywall_email=unpaywall_email, verbose=verbose)

    # We'll use a queue, and also allow "follow-up" URLs extracted from HTML responses.
    queue: List[Candidate] = candidates[:]
    seen_urls: Set[str] = set()

    while queue:
        cand = queue.pop(0)
        if cand.url in seen_urls:
            continue
        seen_urls.add(cand.url)

        if verbose:
            print(f"  Trying: {cand.source}: {cand.url}", flush=True)

        res = attempt_download(sess, cand.url, out_path, referer=cand.referer, verbose=verbose)
        if res.ok:
            if not validate:
                return out_path

            verdict = validate_pdf_matches_doi(out_path, doi, verbose=verbose)
            if verdict is False:
                if verbose:
                    print("  Validation mismatch; deleting and continuing...", flush=True)
                try:
                    out_path.unlink()
                except Exception:
                    pass
                continue
            return out_path

        # If not ok, but extracted follow-up urls, add them with the current final_url as referer.
        if res.followups:
            for u in res.followups:
                if u not in seen_urls:
                    queue.append(Candidate(url=u, source=f"followup:{cand.source}", referer=res.final_url or cand.url))

        # Small politeness delay
        time.sleep(0.05)

    return None


# -----------------------------
# XLSX processing
# -----------------------------
def update_xlsx(
    input_path: Path,
    pdf_dir: Path,
    unpaywall_email: str = "",
    validate: bool = True,
    sleep_s: float = DEFAULT_SLEEP_S,
    verbose: bool = False,
) -> None:
    wb = openpyxl.load_workbook(str(input_path))
    ws = wb.active

    # Header lookup
    headers: Dict[str, int] = {}
    for col in range(1, ws.max_column + 1):
        v = ws.cell(row=1, column=col).value
        if isinstance(v, str):
            headers[v.strip()] = col

    if DOI_COL not in headers:
        raise ValueError(f'Input XLSX must have a column titled "{DOI_COL}". Found: {list(headers.keys())}')

    doi_col = headers[DOI_COL]
    if RESULT_COL in headers:
        res_col = headers[RESULT_COL]
    else:
        res_col = ws.max_column + 1
        ws.cell(row=1, column=res_col).value = RESULT_COL

    sess = request_session()
    total = max(0, ws.max_row - 1)

    for idx, row in enumerate(range(2, ws.max_row + 1), start=1):
        raw = ws.cell(row=row, column=doi_col).value
        doi = normalize_doi(str(raw)) if raw is not None else ""
        if not doi:
            continue

        print(f"[{idx}/{total}] DOI: {doi}", flush=True)

        pdf_path = download_pdf_for_doi(
            sess=sess,
            doi=doi,
            pdf_dir=pdf_dir,
            unpaywall_email=unpaywall_email,
            validate=validate,
            verbose=verbose,
        )

        if pdf_path is None:
            ws.cell(row=row, column=res_col).value = "Undetected"
            print("  -> Undetected", flush=True)
        else:
            ws.cell(row=row, column=res_col).value = pdf_path.name
            print(f"  -> {pdf_path.name}", flush=True)

        wb.save(str(input_path))
        time.sleep(max(0.0, sleep_s))


def make_sample_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DOIs"
    ws.cell(row=1, column=1).value = DOI_COL
    ws.cell(row=2, column=1).value = "10.1016/j.mtbio.2025.101763"
    ws.cell(row=3, column=1).value = "10.1002/adhm.202401358"
    wb.save(str(path))


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download open-access PDFs for DOIs in an XLSX file.")
    p.add_argument("--input", required=True, help='Path to input XLSX (must have column "DOI").')
    p.add_argument("--pdf-dir", default=None, help='PDF output folder. Default: "./PDFs" next to this script.')
    p.add_argument("--unpaywall-email", default=os.environ.get("UNPAYWALL_EMAIL", ""), help="Email for Unpaywall API (recommended).")
    p.add_argument("--no-validate", action="store_true", help="Disable PDF↔DOI validation.")
    p.add_argument("--sleep", type=float, default=DEFAULT_SLEEP_S, help="Sleep between DOIs (seconds).")
    p.add_argument("--make-sample", action="store_true", help="Create a sample XLSX at --input before running.")
    p.add_argument("--verbose", action="store_true", help="Print candidates and download diagnostics.")
    args = p.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    script_dir = Path(__file__).resolve().parent
    pdf_dir = Path(args.pdf_dir).expanduser().resolve() if args.pdf_dir else (script_dir / "PDFs")

    if args.make_sample:
        input_path.parent.mkdir(parents=True, exist_ok=True)
        make_sample_xlsx(input_path)

    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 2

    pdf_dir.mkdir(parents=True, exist_ok=True)

    update_xlsx(
        input_path=input_path,
        pdf_dir=pdf_dir,
        unpaywall_email=args.unpaywall_email,
        validate=not args.no_validate,
        sleep_s=args.sleep,
        verbose=args.verbose,
    )

    print(f"Done. Updated XLSX: {input_path}")
    print(f"PDF folder: {pdf_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
