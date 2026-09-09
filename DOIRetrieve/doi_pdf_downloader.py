#!/usr/bin/env python3
"""
doi_pdf_downloader.py

Reads an XLSX file with a single column titled "DOI" and tries to download a PDF for each DOI
into a local folder "PDFs" (relative to this script). Writes results back into the XLSX as a
second column: the downloaded filename, or "Undetected" if no PDF could be obtained.

Important note about legality:
- This script ONLY attempts lawful sources (publisher pages, Crossref links, Unpaywall OA links,
  and DOI content negotiation). It intentionally does NOT include Sci-Hub/LibGen/Anna's Archive
  download logic.

Optional integrations:
- pdf2doi (if installed) is used to validate that the downloaded PDF matches the requested DOI.
  pdf2doi usage is documented in its upstream README. https://github.com/MicheleCotrufo/pdf2doi
- JabRef (if installed) is used (headless CLI) to fetch bibliographic metadata (e.g., URLs) that
  can help locate the publisher landing page. JabRef CLI supports web fetchers via --fetch as
  documented here: https://docs.jabref.org/advanced/commandline

Usage:
  python doi_pdf_downloader.py --input mydois.xlsx
  python doi_pdf_downloader.py --make-sample --input sample_dois.xlsx

Environment variables (optional):
  UNPAYWALL_EMAIL   Email required by the Unpaywall API (recommended).
  JABREF_PATH       Full path to the JabRef executable (if not in PATH).

Requires:
  pip install openpyxl requests
Optional:
  pip install pdf2doi
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
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import quote, urljoin, urlparse

import openpyxl
import requests


DOI_COL = "DOI"
RESULT_COL = "PDF"  # second column name to be created/updated
DEFAULT_TIMEOUT = 30


DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


def normalize_doi(doi: str) -> str:
    doi = (doi or "").strip()
    doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
    doi = doi.replace("https://dx.doi.org/", "").replace("http://dx.doi.org/", "")
    return doi.strip()


def safe_filename_from_doi(doi: str) -> str:
    # Keep it filesystem-friendly and stable across OSes
    doi = normalize_doi(doi)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", doi)
    # avoid very long filenames
    if len(name) > 160:
        h = hashlib.sha256(doi.encode("utf-8")).hexdigest()[:16]
        name = name[:140] + "_" + h
    return name + ".pdf"


def is_pdf_bytes(b: bytes) -> bool:
    return b.startswith(b"%PDF-")


def is_pdf_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(5)
        return is_pdf_bytes(head)
    except Exception:
        return False


@dataclass
class Candidate:
    url: str
    source: str


class LinkExtractor(HTMLParser):
    """Extract candidate PDF links from HTML in a dependency-free way."""
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links: list[str] = []
        self.meta_pdf: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "meta":
            name = (attrs.get("name") or "").lower()
            if name in {"citation_pdf_url", "dc.identifier", "dc.identifier.doi"}:
                content = attrs.get("content")
                if content:
                    if name == "citation_pdf_url":
                        self.meta_pdf.append(urljoin(self.base_url, content))
                    else:
                        self.links.append(content)
        if tag.lower() == "a":
            href = attrs.get("href")
            if href:
                self.links.append(urljoin(self.base_url, href))


def request_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        # A decent UA helps with some publisher frontends.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) doi_pdf_downloader/1.0",
        "Accept": "*/*",
    })
    return s


def _download_url_to_file(sess: requests.Session, url: str, out_path: Path, timeout: int = DEFAULT_TIMEOUT) -> bool:
    """
    Downloads url into out_path. Returns True iff it looks like a PDF.
    Does not overwrite existing file unless it's invalid.
    """
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    if tmp_path.exists():
        try:
            tmp_path.unlink()
        except Exception:
            pass

    try:
        with sess.get(url, stream=True, timeout=timeout, allow_redirects=True) as r:
            r.raise_for_status()
            ctype = (r.headers.get("Content-Type") or "").lower()
            # We still accept octet-stream if it starts with %PDF.
            with tmp_path.open("wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)

        if not tmp_path.exists() or tmp_path.stat().st_size < 512:
            tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            return False

        # Quick signature check
        with tmp_path.open("rb") as f:
            head = f.read(8)

        if not is_pdf_bytes(head) and "pdf" not in ctype:
            tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
            return False

        tmp_path.replace(out_path)
        return is_pdf_file(out_path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)  # type: ignore[arg-type]
        except Exception:
            pass
        return False


def _try_doi_content_negotiation(sess: requests.Session, doi: str) -> Optional[Candidate]:
    """
    Some publishers support content negotiation to PDF directly.
    """
    doi = normalize_doi(doi)
    url = f"https://doi.org/{quote(doi)}"
    # Try a direct PDF Accept first.
    try:
        r = sess.get(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, headers={"Accept": "application/pdf"})
        ctype = (r.headers.get("Content-Type") or "").lower()
        if r.status_code == 200 and ("pdf" in ctype or is_pdf_bytes(r.content[:8])):
            return Candidate(url=r.url, source="doi:application/pdf")
    except Exception:
        pass
    return None


def _try_crossref_pdf_links(sess: requests.Session, doi: str) -> list[Candidate]:
    """
    Crossref sometimes exposes full-text links (may be paywalled).
    """
    doi = normalize_doi(doi)
    api = f"https://api.crossref.org/works/{quote(doi)}"
    out: list[Candidate] = []
    try:
        r = sess.get(api, timeout=DEFAULT_TIMEOUT, headers={"Accept": "application/json"})
        r.raise_for_status()
        data = r.json()
        msg = data.get("message") or {}
        for link in msg.get("link") or []:
            # link: {"URL": "...", "content-type": "application/pdf", ...}
            ct = (link.get("content-type") or "").lower()
            u = link.get("URL")
            if u and ("pdf" in ct or u.lower().endswith(".pdf")):
                out.append(Candidate(url=u, source="crossref:link"))
    except Exception:
        pass
    return out


def _try_unpaywall(sess: requests.Session, doi: str, email: str) -> list[Candidate]:
    """
    Uses Unpaywall to find OA PDF URLs.
    """
    doi = normalize_doi(doi)
    out: list[Candidate] = []
    if not email:
        return out
    api = f"https://api.unpaywall.org/v2/{quote(doi)}?email={quote(email)}"
    try:
        r = sess.get(api, timeout=DEFAULT_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        def add_loc(loc, label: str):
            if not isinstance(loc, dict):
                return
            pdf_url = loc.get("url_for_pdf")
            if pdf_url:
                out.append(Candidate(url=pdf_url, source=f"unpaywall:{label}:url_for_pdf"))
            # sometimes url is a landing page; keep it as weaker candidate
            landing = loc.get("url")
            if landing:
                out.append(Candidate(url=landing, source=f"unpaywall:{label}:landing"))

        add_loc(data.get("best_oa_location"), "best")
        for loc in data.get("oa_locations") or []:
            add_loc(loc, "oa_locations")
    except Exception:
        pass
    return out


def _resolve_landing_page(sess: requests.Session, doi: str) -> Optional[str]:
    """
    Resolve DOI to landing page (HTML).
    """
    doi = normalize_doi(doi)
    url = f"https://doi.org/{quote(doi)}"
    try:
        r = sess.get(url, allow_redirects=True, timeout=DEFAULT_TIMEOUT, headers={"Accept": "text/html,application/xhtml+xml"})
        r.raise_for_status()
        # Some servers might give PDF directly; treat that elsewhere.
        return r.url
    except Exception:
        return None


def _extract_pdf_candidates_from_html(html: str, base_url: str) -> list[Candidate]:
    parser = LinkExtractor(base_url=base_url)
    try:
        parser.feed(html)
    except Exception:
        pass

    cands: list[Candidate] = []
    # Meta-provided PDF URLs are usually best
    for u in parser.meta_pdf:
        cands.append(Candidate(url=u, source="html:meta:citation_pdf_url"))

    # Heuristic: pick links that look like they point to PDFs
    for u in parser.links:
        ul = (u or "").lower()
        if not u:
            continue
        if "pdf" in ul and (ul.endswith(".pdf") or "download" in ul or "content/pdf" in ul or "articlepdf" in ul):
            cands.append(Candidate(url=u, source="html:link:pdf-ish"))
    return dedupe_candidates(cands)


def dedupe_candidates(cands: Iterable[Candidate]) -> list[Candidate]:
    seen = set()
    out = []
    for c in cands:
        u = c.url.strip()
        if not u:
            continue
        # normalize URL fragments
        u = u.split("#", 1)[0]
        if u in seen:
            continue
        seen.add(u)
        out.append(Candidate(url=u, source=c.source))
    return out


def _try_html_scrape_for_pdf(sess: requests.Session, landing_url: str) -> list[Candidate]:
    out: list[Candidate] = []
    try:
        r = sess.get(landing_url, timeout=DEFAULT_TIMEOUT, headers={"Accept": "text/html,application/xhtml+xml"})
        r.raise_for_status()
        html = r.text
        out.extend(_extract_pdf_candidates_from_html(html, base_url=r.url))
    except Exception:
        pass
    return out


def _jabref_executable() -> Optional[str]:
    # Allow explicit override
    if os.environ.get("JABREF_PATH"):
        p = Path(os.environ["JABREF_PATH"]).expanduser()
        if p.exists():
            return str(p)
    return shutil.which("jabref") or shutil.which("JabRef")


def _try_jabref_metadata(sess: requests.Session, doi: str) -> list[Candidate]:
    """
    Uses JabRef CLI (--fetch) to pull a BibTeX entry (e.g., via CrossRef) and extract URL-ish fields.
    This does NOT download PDFs by itself; it just yields extra URLs to try.

    Requires JabRef installed and accessible (JABREF_PATH or in PATH).
    """
    exe = _jabref_executable()
    if not exe:
        return []

    tmp_dir = Path(".jabref_tmp")
    tmp_dir.mkdir(exist_ok=True)
    bib_path = tmp_dir / "tmp.bib"

    # Try a few likely fetcher names. (JabRef docs: --fetch=FetcherName:QueryString)
    # If this fails for all, we just return nothing.
    fetchers = ["CrossRef", "Crossref", "DOI"]
    urls: list[Candidate] = []

    for fetcher in fetchers:
        try:
            if bib_path.exists():
                bib_path.unlink()
            cmd = [exe, "--nogui", f"--fetch={fetcher}:{normalize_doi(doi)}", "--output", str(bib_path)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            if proc.returncode != 0 or not bib_path.exists() or bib_path.stat().st_size == 0:
                continue

            bib_text = bib_path.read_text(encoding="utf-8", errors="ignore")

            # Extract url = {...} fields
            for m in re.finditer(r"\burl\s*=\s*[{\"\']([^}\"\']+)[}\"\']", bib_text, flags=re.IGNORECASE):
                u = m.group(1).strip()
                if u.startswith("http"):
                    urls.append(Candidate(url=u, source=f"jabref:{fetcher}:url"))

            # Extract doi = {...} field in case it differs (rare)
            for m in re.finditer(r"\bdoi\s*=\s*[{\"\']([^}\"\']+)[}\"\']", bib_text, flags=re.IGNORECASE):
                # A DOI isn't a URL; ignore.

                pass

            if urls:
                break
        except Exception:
            continue

    return dedupe_candidates(urls)


def _pdf2doi_extract(pdf_path: Path) -> Optional[str]:
    """
    If pdf2doi is installed, try to extract DOI from a downloaded pdf.
    Returns normalized DOI if found, else None.
    """
    try:
        import pdf2doi  # type: ignore
    except Exception:
        return None

    try:
        # Be conservative: avoid modifying PDFs and avoid extra web validation.
        if hasattr(pdf2doi, "config"):
            try:
                pdf2doi.config.set("verbose", False)
                pdf2doi.config.set("store", False)          # don't write metadata
                pdf2doi.config.set("webvalidation", False)  # don't query dx.doi.org
            except Exception:
                pass

        # pdf2doi.pdf2doi is the main entry point according to upstream docs.
        result = pdf2doi.pdf2doi(str(pdf_path))  # type: ignore[attr-defined]
        if isinstance(result, dict):
            ident = result.get("identifier")
            if isinstance(ident, str) and DOI_PATTERN.search(ident):
                return normalize_doi(DOI_PATTERN.search(ident).group(0))  # type: ignore[union-attr]
    except Exception:
        return None

    return None


def try_download_pdf_for_doi(doi: str, pdf_dir: Path, sess: requests.Session) -> Optional[Path]:
    """
    Attempts several lawful strategies; returns saved PDF path or None.
    """
    doi = normalize_doi(doi)
    if not doi:
        return None

    pdf_dir.mkdir(parents=True, exist_ok=True)
    out_path = pdf_dir / safe_filename_from_doi(doi)

    # If already exists and passes signature check, keep it
    if out_path.exists() and is_pdf_file(out_path):
        return out_path

    candidates: list[Candidate] = []

    # 1) Unpaywall (best when available)
    candidates.extend(_try_unpaywall(sess, doi, email=os.environ.get("UNPAYWALL_EMAIL", "")))

    # 2) Crossref full-text links
    candidates.extend(_try_crossref_pdf_links(sess, doi))

    # 3) Direct DOI content negotiation to PDF (rare but fast)
    cand = _try_doi_content_negotiation(sess, doi)
    if cand:
        candidates.append(cand)

    # 4) JabRef metadata as helper (if installed)
    candidates.extend(_try_jabref_metadata(sess, doi))

    # 5) Scrape DOI landing page for PDF-ish links
    landing = _resolve_landing_page(sess, doi)
    if landing:
        # if landing itself is a PDF, treat it as a candidate
        candidates.append(Candidate(url=landing, source="doi:landing"))
        candidates.extend(_try_html_scrape_for_pdf(sess, landing))

    candidates = dedupe_candidates(candidates)

    # Prefer likely direct PDFs first
    def score(c: Candidate) -> int:
        ul = c.url.lower()
        s = 0
        if ul.endswith(".pdf"):
            s += 20
        if "url_for_pdf" in c.source:
            s += 15
        if "citation_pdf_url" in c.source:
            s += 10
        if "crossref" in c.source:
            s += 5
        if "doi:application/pdf" in c.source:
            s += 8
        return -s  # for ascending sort

    candidates.sort(key=score)

    for c in candidates:
        ok = _download_url_to_file(sess, c.url, out_path)
        if not ok:
            continue

        # Validate (best-effort) that the PDF belongs to this DOI
        extracted = _pdf2doi_extract(out_path)
        if extracted is not None and normalize_doi(extracted).lower() != doi.lower():
            # Not a match: discard and keep trying.
            try:
                out_path.unlink()
            except Exception:
                pass
            continue

        return out_path

    return None


def update_xlsx(input_path: Path, pdf_dir: Path) -> None:
    wb = openpyxl.load_workbook(input_path)
    ws = wb.active

    # Identify DOI column index by header.
    header_row = 1
    headers = {}
    for col in range(1, ws.max_column + 1):
        val = ws.cell(row=header_row, column=col).value
        if isinstance(val, str):
            headers[val.strip()] = col

    if DOI_COL not in headers:
        raise ValueError(f'Input XLSX must have a single column titled "{DOI_COL}". Found headers: {list(headers.keys())}')

    doi_col_idx = headers[DOI_COL]

    # Ensure result column exists as second column. If not present, create it.
    if RESULT_COL in headers:
        res_col_idx = headers[RESULT_COL]
    else:
        res_col_idx = ws.max_column + 1
        ws.cell(row=header_row, column=res_col_idx).value = RESULT_COL

    sess = request_session()

    # Iterate rows
    for row in range(2, ws.max_row + 1):
        doi_cell = ws.cell(row=row, column=doi_col_idx)
        doi_raw = doi_cell.value
        doi = normalize_doi(str(doi_raw)) if doi_raw is not None else ""

        if not doi:
            continue

        current = ws.cell(row=row, column=res_col_idx).value
        if isinstance(current, str) and current.strip() and current.strip().lower() != "undetected":
            # if it points to an existing file, skip
            maybe = pdf_dir / current.strip()
            if maybe.exists() and is_pdf_file(maybe):
                continue

        print(f"[{row-1}/{ws.max_row-1}] DOI: {doi}", flush=True)

        pdf_path = try_download_pdf_for_doi(doi, pdf_dir=pdf_dir, sess=sess)
        if pdf_path is None:
            ws.cell(row=row, column=res_col_idx).value = "Undetected"
            print("  -> Undetected")
        else:
            ws.cell(row=row, column=res_col_idx).value = pdf_path.name
            print(f"  -> {pdf_path.name}")

        # Gentle pacing (avoid hammering services)
        time.sleep(0.3)

    wb.save(input_path)


def make_sample_xlsx(path: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DOIs"
    ws.cell(row=1, column=1).value = DOI_COL
    ws.cell(row=2, column=1).value = "10.1016/j.mtbio.2025.101763"
    ws.cell(row=3, column=1).value = "10.1002/adhm.202401358"
    wb.save(path)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Download PDFs for DOIs listed in an XLSX file.")
    p.add_argument("--input", required=True, help="Path to input XLSX (must have column 'DOI').")
    p.add_argument("--pdf-dir", default=None, help='PDF output folder. Default: "./PDFs" next to this script.')
    p.add_argument("--make-sample", action="store_true", help="Create a sample XLSX at --input before running.")
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
    update_xlsx(input_path, pdf_dir=pdf_dir)

    print(f"Done. Updated XLSX: {input_path}")
    print(f"PDF folder: {pdf_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
