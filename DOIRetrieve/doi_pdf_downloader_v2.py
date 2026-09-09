#!/usr/bin/env python3
"""
doi_pdf_downloader_v2.py

Reads an XLSX with a single column "DOI", tries to legally retrieve PDFs for each DOI, and writes the result
into the 2nd column "PDF" (downloaded filename) or "Undetected".

Legal retrieval strategies implemented (in order):
  1) DOI landing-page resolution (doi.org -> publisher page) and direct-PDF detection
  2) Publisher-page HTML parsing for citation/meta PDF URLs (e.g., citation_pdf_url)
  3) Publisher-specific heuristics (ScienceDirect/Elsevier, Wiley, ACS, Springer, IEEE basic patterns)
  4) Open-access aggregators/APIs: Unpaywall (optional email), OpenAlex, Semantic Scholar, Crossref

Optional:
  - pdf2doi: verify downloaded PDF appears to contain the DOI (best-effort, not a hard failure)
  - JabRef: fetch BibTeX/metadata (best-effort; not used as a downloader)

Usage:
  python doi_pdf_downloader_v2.py --input sample.xlsx

Recommended (improves OA hit rate for Unpaywall):
  export UNPAYWALL_EMAIL="you@domain.com"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, unquote

# -----------------------------
# Dependency bootstrap
# -----------------------------

def _pip_install(packages: Sequence[str]) -> bool:
    try:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade"] + list(packages)
        print(f"[deps] Installing: {' '.join(packages)}")
        subprocess.check_call(cmd)
        return True
    except Exception as e:
        print(f"[deps] pip install failed: {e}")
        return False

def ensure_import(module_name: str, pip_name: Optional[str] = None, auto_install: bool = False) -> bool:
    try:
        __import__(module_name)
        return True
    except ImportError:
        if not auto_install:
            return False
        pip_pkg = pip_name or module_name
        return _pip_install([pip_pkg]) and ensure_import(module_name, pip_name, auto_install=False)

# -----------------------------
# Helpers
# -----------------------------

DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)

def normalize_doi(raw: str) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    s = s.replace("https://doi.org/", "").replace("http://doi.org/", "")
    s = s.replace("https://dx.doi.org/", "").replace("http://dx.doi.org/", "")
    s = s.strip()
    m = DOI_RE.search(s)
    return m.group(1).lower() if m else s.lower()

def safe_filename_from_doi(doi: str) -> str:
    # DOI -> filename-safe string
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", doi)
    safe = safe.strip("_")
    if not safe:
        safe = "unknown"
    return safe + ".pdf"

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)

def is_probably_pdf_bytes(head: bytes) -> bool:
    return head.startswith(b"%PDF")

def choose_filename_from_headers(url: str, headers: Dict[str, str], fallback: str) -> str:
    cd = headers.get("content-disposition") or headers.get("Content-Disposition") or ""
    # Try Content-Disposition: attachment; filename="...pdf"
    m = re.search(r'filename\*=UTF-8\'\'([^;]+)', cd, flags=re.IGNORECASE)
    if m:
        name = unquote(m.group(1)).strip().strip('"').strip("'")
        if name.lower().endswith(".pdf"):
            return name
    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip()
        if name.lower().endswith(".pdf"):
            return name
    m = re.search(r"filename\s*=\s*([^;]+)", cd, flags=re.IGNORECASE)
    if m:
        name = m.group(1).strip().strip('"').strip("'")
        if name.lower().endswith(".pdf"):
            return name

    # fallback: URL path
    try:
        path = urlparse(url).path
        base = os.path.basename(path)
        if base.lower().endswith(".pdf") and len(base) >= 5:
            return base
    except Exception:
        pass

    return fallback

def dedupe_keep_order(urls: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for u in urls:
        if not u:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

# -----------------------------
# Networking / Retrieval
# -----------------------------

@dataclass
class DownloadResult:
    ok: bool
    filename: Optional[str] = None
    url: Optional[str] = None
    reason: Optional[str] = None

class PDFDownloader:
    def __init__(
        self,
        out_dir: str,
        unpaywall_email: Optional[str] = None,
        timeout: Tuple[int, int] = (15, 45),
        max_bytes: Optional[int] = None,
        verify_with_pdf2doi: bool = False,
        verbose: bool = True,
    ):
        self.out_dir = out_dir
        self.unpaywall_email = unpaywall_email
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.verify_with_pdf2doi = verify_with_pdf2doi
        self.verbose = verbose

        # requests will be imported after ensure_import
        import requests  # type: ignore
        self.requests = requests
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) doi-pdf-downloader/2.0",
            "Accept": "application/pdf,application/xhtml+xml,text/html;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.8",
        })

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def _get_json(self, url: str) -> Optional[dict]:
        try:
            r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            if r.status_code != 200:
                return None
            return r.json()
        except Exception:
            return None

    def _download_pdf_url(self, doi: str, url: str, preferred_name: str) -> DownloadResult:
        """
        Try to download a PDF from url, saving under out_dir with a stable filename.
        """
        ensure_dir(self.out_dir)
        tmp_path = None
        try:
            with self.session.get(url, stream=True, allow_redirects=True, timeout=self.timeout) as r:
                if r.status_code != 200:
                    return DownloadResult(False, reason=f"HTTP {r.status_code}")

                # peek first chunk for PDF magic
                it = r.iter_content(chunk_size=4096)
                first = next(it, b"")
                if not first:
                    return DownloadResult(False, reason="empty response")

                ct = (r.headers.get("Content-Type") or "").lower()
                # Some servers send octet-stream. Use magic bytes as truth.
                if ("application/pdf" not in ct) and (not is_probably_pdf_bytes(first)):
                    return DownloadResult(False, reason=f"not a PDF (content-type={ct or 'unknown'})")

                filename = choose_filename_from_headers(url, {k.lower(): v for k, v in r.headers.items()}, preferred_name)
                # sanitize filename
                filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("_")
                if not filename.lower().endswith(".pdf"):
                    filename += ".pdf"

                final_path = os.path.join(self.out_dir, filename)
                # If exists, avoid overwriting; add suffix.
                if os.path.exists(final_path):
                    base, ext = os.path.splitext(filename)
                    i = 2
                    while True:
                        alt = f"{base}__{i}{ext}"
                        alt_path = os.path.join(self.out_dir, alt)
                        if not os.path.exists(alt_path):
                            filename = alt
                            final_path = alt_path
                            break
                        i += 1

                tmp_path = final_path + ".part"
                bytes_written = 0
                with open(tmp_path, "wb") as f:
                    f.write(first)
                    bytes_written += len(first)
                    for chunk in it:
                        if not chunk:
                            continue
                        f.write(chunk)
                        bytes_written += len(chunk)
                        if self.max_bytes and bytes_written > self.max_bytes:
                            raise RuntimeError(f"download exceeds max_bytes={self.max_bytes}")

                # quick validate header
                with open(tmp_path, "rb") as f:
                    head = f.read(4)
                if head != b"%PDF":
                    os.remove(tmp_path)
                    return DownloadResult(False, reason="downloaded file is not PDF (magic mismatch)")

                os.replace(tmp_path, final_path)

                # optional verification with pdf2doi (best-effort, not fatal)
                if self.verify_with_pdf2doi:
                    self._verify_pdf_matches_doi(final_path, doi)

                return DownloadResult(True, filename=filename, url=r.url, reason="downloaded")
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass
            return DownloadResult(False, reason=str(e))

    def _verify_pdf_matches_doi(self, pdf_path: str, expected_doi: str) -> None:
        try:
            import pdf2doi  # type: ignore
        except Exception:
            self.log("  [verify] pdf2doi not installed; skipping verification")
            return

        try:
            # suppress heavy web search; we only want in-file DOI/metadata if possible
            try:
                pdf2doi.config.set("verbose", False)
                pdf2doi.config.set("websearch", False)
                pdf2doi.config.set("webvalidation", False)
            except Exception:
                pass

            res = pdf2doi.pdf2doi(pdf_path)  # returns dict for single file
            if isinstance(res, list) and res:
                res = res[0]
            found = None
            if isinstance(res, dict):
                found = res.get("identifier")
            if found:
                found_norm = normalize_doi(str(found))
                if found_norm != normalize_doi(expected_doi):
                    self.log(f"  [verify] WARNING: pdf2doi found DOI {found_norm}, expected {expected_doi}")
        except Exception as e:
            self.log(f"  [verify] pdf2doi verification failed: {e}")

    # -----------------------------
    # Candidate URL sources
    # -----------------------------

    def _candidates_from_unpaywall(self, doi: str) -> List[str]:
        if not self.unpaywall_email:
            return []
        # Unpaywall requires an email parameter
        url = f"https://api.unpaywall.org/v2/{doi}?email={self.unpaywall_email}"
        data = self._get_json(url)
        if not data:
            return []
        urls = []
        best = data.get("best_oa_location") or {}
        if isinstance(best, dict):
            if best.get("url_for_pdf"):
                urls.append(best["url_for_pdf"])
            if best.get("url"):
                urls.append(best["url"])
        for loc in (data.get("oa_locations") or []):
            if not isinstance(loc, dict):
                continue
            if loc.get("url_for_pdf"):
                urls.append(loc["url_for_pdf"])
            if loc.get("url"):
                urls.append(loc["url"])
        return urls

    def _candidates_from_openalex(self, doi: str) -> List[str]:
        url = f"https://api.openalex.org/works/https://doi.org/{doi}"
        data = self._get_json(url)
        if not data:
            return []
        urls = []
        pl = data.get("primary_location") or {}
        if isinstance(pl, dict):
            if pl.get("pdf_url"):
                urls.append(pl["pdf_url"])
            if pl.get("landing_page_url"):
                urls.append(pl["landing_page_url"])
        for loc in (data.get("locations") or []):
            if not isinstance(loc, dict):
                continue
            if loc.get("pdf_url"):
                urls.append(loc["pdf_url"])
            if loc.get("landing_page_url"):
                urls.append(loc["landing_page_url"])
        return urls

    def _candidates_from_semanticscholar(self, doi: str) -> List[str]:
        # Graph API (open access PDF if available)
        url = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}?fields=openAccessPdf,url,isOpenAccess"
        data = self._get_json(url)
        if not data:
            return []
        urls = []
        oap = data.get("openAccessPdf") or {}
        if isinstance(oap, dict) and oap.get("url"):
            urls.append(oap["url"])
        if data.get("url"):
            urls.append(data["url"])
        return urls

    def _candidates_from_crossref(self, doi: str) -> List[str]:
        url = f"https://api.crossref.org/works/{doi}"
        data = self._get_json(url)
        if not data:
            return []
        msg = data.get("message") or {}
        urls = []
        for link in (msg.get("link") or []):
            if not isinstance(link, dict):
                continue
            ct = (link.get("content-type") or "").lower()
            if "pdf" in ct or ct == "application/pdf":
                u = link.get("URL") or link.get("url")
                if u:
                    urls.append(u)
        # Sometimes full-text is in "resource"
        res = msg.get("resource") or {}
        if isinstance(res, dict) and res.get("primary", {}).get("URL"):
            urls.append(res["primary"]["URL"])
        return urls

    def _resolve_doi(self, doi: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Resolve DOI via doi.org.
        Returns: (final_url, content_type, text_if_html_or_none)
        """
        url = f"https://doi.org/{doi}"
        try:
            r = self.session.get(url, allow_redirects=True, timeout=self.timeout)
            ct = (r.headers.get("Content-Type") or "").lower()
            if "application/pdf" in ct:
                return (r.url, ct, None)
            # Some servers send application/octet-stream for PDF
            if r.content[:4] == b"%PDF":
                return (r.url, "application/pdf", None)
            # Else assume HTML/text
            try:
                text = r.text
            except Exception:
                text = None
            return (r.url, ct, text)
        except Exception:
            return (None, None, None)

    def _extract_pdf_urls_from_html(self, html: str, base_url: str) -> List[str]:
        """
        Lightweight extraction without requiring BeautifulSoup.
        """
        urls: List[str] = []

        # 1) meta citation_pdf_url
        for m in re.finditer(r'citation_pdf_url[^>]*content=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            urls.append(m.group(1))

        # 2) generic .pdf URLs
        for m in re.finditer(r'https?://[^\s"<>]+?\.pdf(?:\?[^\s"<>]+)?', html, flags=re.IGNORECASE):
            urls.append(m.group(0))

        # 3) href="...pdf..." (relative or absolute)
        for m in re.finditer(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
            href = m.group(1)
            if not href:
                continue
            h = href.strip()
            if any(tok in h.lower() for tok in [".pdf", "pdfft", "pdfdirect", "/pdf", "/epdf", "downloadpdf"]):
                urls.append(urljoin(base_url, h))

        # normalize
        cleaned = []
        for u in urls:
            if not u:
                continue
            if u.startswith("//"):
                u = "https:" + u
            cleaned.append(u)

        return dedupe_keep_order(cleaned)

    # -----------------------------
    # Publisher-specific heuristics
    # -----------------------------

    def _publisher_specific_candidates(self, landing_url: str, doi: str) -> List[str]:
        urls: List[str] = []
        if not landing_url:
            return urls

        lu = landing_url

        # ScienceDirect / Elsevier
        m = re.search(r"sciencedirect\.com/science/article/pii/([A-Z0-9]+)", lu, flags=re.IGNORECASE)
        if m:
            pii = m.group(1)
            base = f"https://www.sciencedirect.com/science/article/pii/{pii}"
            urls.extend([
                f"{base}/pdfft?isDTMRedir=true&download=true",
                f"{base}/pdfft?isDTMRedir=true",
                f"{base}/pdfft",
                f"{base}/pdf",
                f"https://ars.els-cdn.com/content/image/1-s2.0-{pii}-main.pdf",
            ])

        # Wiley Online Library patterns (may be paywalled, but works for OA)
        if "onlinelibrary.wiley.com/doi/" in lu:
            # normalize landing page: .../doi/10.xxxx/yyyy -> pdf at .../doi/epdf/10.xxxx/yyyy
            try:
                parsed = urlparse(lu)
                # Extract DOI path part after /doi/
                path = parsed.path
                idx = path.lower().find("/doi/")
                if idx >= 0:
                    after = path[idx + len("/doi/") :].strip("/")
                    # Some paths like /doi/10.1002/... or /doi/abs/10.1002/...
                    after = after.replace("abs/", "").replace("full/", "")
                    doi_in_path = after
                    urls.extend([
                        f"https://onlinelibrary.wiley.com/doi/epdf/{doi_in_path}",
                        f"https://onlinelibrary.wiley.com/doi/pdfdirect/{doi_in_path}",
                        f"https://onlinelibrary.wiley.com/doi/pdf/{doi_in_path}",
                    ])
            except Exception:
                pass

        # ACS
        if "pubs.acs.org/doi/" in lu:
            try:
                parsed = urlparse(lu)
                # /doi/abs/10.1021/xxx or /doi/10.1021/xxx
                path = parsed.path
                doi_part = path.split("/doi/")[-1].strip("/")
                doi_part = doi_part.replace("abs/", "").replace("full/", "")
                urls.extend([
                    f"https://pubs.acs.org/doi/pdf/{doi_part}",
                    f"https://pubs.acs.org/doi/pdfplus/{doi_part}",
                ])
            except Exception:
                pass

        # Springer / Nature
        if "link.springer.com/article/" in lu:
            # Springer often: /content/pdf/<article-id>.pdf
            # Usually embedded as citation_pdf_url, so let HTML parse handle it.
            pass

        # IEEE Xplore (very inconsistent; OA sometimes exists)
        if "ieeexplore.ieee.org/document/" in lu:
            # IEEE "stamp" PDFs require arnumber
            m2 = re.search(r"/document/(\d+)", lu)
            if m2:
                arn = m2.group(1)
                urls.append(f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arn}")

        return dedupe_keep_order(urls)

    # -----------------------------
    # Main retrieval pipeline
    # -----------------------------

    def retrieve_pdf_for_doi(self, doi: str) -> DownloadResult:
        doi = normalize_doi(doi)
        if not doi:
            return DownloadResult(False, reason="empty DOI")

        preferred_name = safe_filename_from_doi(doi)

        # 0) If already downloaded, return existing file
        existing_path = os.path.join(self.out_dir, preferred_name)
        if os.path.exists(existing_path) and os.path.getsize(existing_path) > 1024:
            return DownloadResult(True, filename=preferred_name, url=None, reason="already exists")

        # 1) OA APIs
        candidates: List[str] = []
        candidates.extend(self._candidates_from_unpaywall(doi))
        candidates.extend(self._candidates_from_openalex(doi))
        candidates.extend(self._candidates_from_semanticscholar(doi))
        candidates.extend(self._candidates_from_crossref(doi))
        candidates = dedupe_keep_order(candidates)

        for u in candidates:
            self.log(f"  [try] {u}")
            dr = self._download_pdf_url(doi, u, preferred_name)
            if dr.ok:
                return dr

        # 2) Resolve DOI -> landing page; sometimes direct PDF
        landing_url, content_type, html = self._resolve_doi(doi)
        if landing_url and content_type and "application/pdf" in content_type:
            self.log(f"  [try] DOI resolved to PDF: {landing_url}")
            return self._download_pdf_url(doi, landing_url, preferred_name)

        # 3) Publisher heuristics from landing URL
        if landing_url:
            heur = self._publisher_specific_candidates(landing_url, doi)
            for u in heur:
                self.log(f"  [try] {u}")
                dr = self._download_pdf_url(doi, u, preferred_name)
                if dr.ok:
                    return dr

        # 4) Parse landing HTML for PDF links
        if html and landing_url:
            extracted = self._extract_pdf_urls_from_html(html, landing_url)
            # additional: if we didn't have html (some servers block) try fetching landing URL as HTML explicitly
            for u in extracted:
                self.log(f"  [try] {u}")
                dr = self._download_pdf_url(doi, u, preferred_name)
                if dr.ok:
                    return dr

        # 5) As last attempt: fetch landing_url again and parse (sometimes r.text earlier failed)
        if landing_url and not html:
            try:
                r = self.session.get(landing_url, timeout=self.timeout, allow_redirects=True)
                if r.status_code == 200 and r.text:
                    extracted = self._extract_pdf_urls_from_html(r.text, r.url)
                    for u in extracted:
                        self.log(f"  [try] {u}")
                        dr = self._download_pdf_url(doi, u, preferred_name)
                        if dr.ok:
                            return dr
            except Exception:
                pass

        return DownloadResult(False, reason="Undetected")

# -----------------------------
# JabRef helper (optional)
# -----------------------------

def jabref_fetch_bibtex(doi: str, jabref_path: str = "jabref") -> Optional[str]:
    """
    Best-effort: call JabRef CLI to fetch a BibTeX entry. Returns BibTeX text or None.
    Note: This does NOT download PDFs. It may help you get stable metadata.
    """
    doi = normalize_doi(doi)
    if not doi:
        return None
    if shutil.which(jabref_path) is None:
        return None
    try:
        # JabRef can fetch with: --fetch=FetcherName:QueryString
        # DOI fetcher is typically available in JabRef.
        cmd = [jabref_path, f"--fetch=DOI:{doi}", "--nogui"]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=60)
        txt = out.decode("utf-8", errors="replace")
        if "@" in txt:
            return txt
        return None
    except Exception:
        return None

# -----------------------------
# XLSX processing
# -----------------------------

def process_xlsx(
    input_path: str,
    output_path: str,
    downloader: PDFDownloader,
    doi_header: str = "DOI",
    out_header: str = "PDF",
) -> None:
    import openpyxl  # type: ignore

    wb = openpyxl.load_workbook(input_path)
    ws = wb.active

    # Identify DOI column (header row 1)
    header_row = 1
    max_col = ws.max_column
    doi_col = None
    out_col = None

    for c in range(1, max_col + 1):
        v = ws.cell(row=header_row, column=c).value
        if v is None:
            continue
        if str(v).strip().lower() == doi_header.lower():
            doi_col = c
        if str(v).strip().lower() == out_header.lower():
            out_col = c

    if doi_col is None:
        raise RuntimeError(f'No column titled "{doi_header}" found in row 1.')

    if out_col is None:
        out_col = max_col + 1
        ws.cell(row=header_row, column=out_col).value = out_header

    # Iterate rows
    n_rows = ws.max_row
    total = 0
    for r in range(2, n_rows + 1):
        doi_raw = ws.cell(row=r, column=doi_col).value
        doi = normalize_doi("" if doi_raw is None else str(doi_raw))
        if not doi:
            continue
        total += 1

    idx = 0
    for r in range(2, n_rows + 1):
        doi_raw = ws.cell(row=r, column=doi_col).value
        doi = normalize_doi("" if doi_raw is None else str(doi_raw))
        if not doi:
            continue
        idx += 1

        # If already has a filename and it exists, skip unless user wants force (not implemented)
        current = ws.cell(row=r, column=out_col).value
        if current and str(current).strip() and str(current).strip().lower() != "undetected":
            existing = os.path.join(downloader.out_dir, str(current).strip())
            if os.path.exists(existing):
                downloader.log(f"[{idx}/{total}] DOI: {doi}\n  -> already: {current}")
                continue

        downloader.log(f"[{idx}/{total}] DOI: {doi}")
        dr = downloader.retrieve_pdf_for_doi(doi)
        if dr.ok and dr.filename:
            ws.cell(row=r, column=out_col).value = dr.filename
            downloader.log(f"  -> {dr.filename}")
        else:
            ws.cell(row=r, column=out_col).value = "Undetected"
            downloader.log(f"  -> Undetected")

        # Save periodically (crash-safe)
        if idx % 5 == 0:
            wb.save(output_path)

    wb.save(output_path)

# -----------------------------
# Main
# -----------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Input XLSX path with column header 'DOI'")
    ap.add_argument("--output", default=None, help="Output XLSX path (default: overwrite input)")
    ap.add_argument("--pdf-dir", default=None, help="PDF output directory (default: ./PDFs next to this script)")
    ap.add_argument("--unpaywall-email", default=None, help="Email for Unpaywall API (or set env UNPAYWALL_EMAIL)")
    ap.add_argument("--auto-install", action="store_true", help="Auto-install missing python deps (openpyxl, requests)")
    ap.add_argument("--verify-with-pdf2doi", action="store_true", help="If pdf2doi is installed, verify downloaded PDFs (best-effort)")
    ap.add_argument("--quiet", action="store_true", help="Reduce console output")
    args = ap.parse_args()

    input_path = args.input
    output_path = args.output or args.input

    # Determine PDFs folder relative to this script unless user specified
    if args.pdf_dir:
        pdf_dir = args.pdf_dir
    else:
        pdf_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "PDFs")

    # Ensure minimal deps (requests, openpyxl)
    if not ensure_import("requests", "requests", auto_install=args.auto_install):
        print("Missing dependency: requests")
        print("Install: python -m pip install requests")
        return 2
    if not ensure_import("openpyxl", "openpyxl", auto_install=args.auto_install):
        print("Missing dependency: openpyxl")
        print("Install: python -m pip install openpyxl")
        return 2

    unpaywall_email = args.unpaywall_email or os.environ.get("UNPAYWALL_EMAIL")
    if unpaywall_email:
        unpaywall_email = unpaywall_email.strip()

    downloader = PDFDownloader(
        out_dir=pdf_dir,
        unpaywall_email=unpaywall_email,
        verify_with_pdf2doi=args.verify_with_pdf2doi,
        verbose=not args.quiet,
    )

    # Quick note if Unpaywall not configured
    if not unpaywall_email and not args.quiet:
        print("[info] UNPAYWALL_EMAIL not set; Unpaywall step will be skipped (other legal sources still used).")

    ensure_dir(pdf_dir)
    process_xlsx(input_path, output_path, downloader)
    print(f"Done. PDFs in: {pdf_dir}")
    print(f"Updated XLSX: {output_path}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
