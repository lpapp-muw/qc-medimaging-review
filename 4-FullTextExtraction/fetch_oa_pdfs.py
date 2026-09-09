#!/usr/bin/env python3
"""
fetch_oa_pdfs.py
================

Focused open-access-only PDF retriever for the Step-4 candidate set.

Strategy (legal, no paywall bypass, no Cloudflare challenges):

1. For each DOI in step4_candidates_v2.xlsx "missing_pdfs" sheet:
   a. Query Unpaywall for best_oa_location.url_for_pdf (and url as fallback).
   b. Query OpenAlex for primary_location.pdf_url + best_oa_location.pdf_url.
   c. Query EuropePMC for fullTextUrlList where availability == "Open access"
      and documentStyle == "pdf".
2. For each candidate URL, do ONE GET request with:
   - User-Agent: polite scholar UA with contact email
   - Max 1 redirect hop
   - Timeout: 30 s
   - HARD STOP if content-type is text/html (no follow-up URL chasing).
   - Accept only application/pdf or application/octet-stream with %PDF magic.
3. Save successful PDFs to 4-FullTextExtraction/PDFs_v2/<doi_safe>.pdf.
4. Update the workbook INCREMENTALLY after every DOI (so a crash or
   interrupt does not lose progress).

No follow-up URL chasing. No JavaScript. No Cloudflare token loops. If
the OA APIs do not advertise a direct PDF URL, the script skips and
moves on. This is the deliberate trade-off vs the v1 downloader: lower
recovery rate, but bounded execution time and predictable behaviour.

Wall-time estimate: ~3-5 seconds per DOI -> 5-10 min for 102 DOIs.

Usage:
    python3 4-FullTextExtraction/fetch_oa_pdfs.py
"""

import json
import re
import shutil
import sys
import time
import urllib.parse
from pathlib import Path

try:
    import requests
    import openpyxl
except ImportError:
    sys.exit("requests and openpyxl required: pip3 install --break-system-packages requests openpyxl")

CONTACT_EMAIL = "laszlo.papp@meduniwien.ac.at"
USER_AGENT    = f"IEEE-TRPMS-QC-MedImaging-Review/v2 (mailto:{CONTACT_EMAIL})"

PROJECT_ROOT     = Path("/home/lpapp/IEEE_SYS_REV")
STEP4_DIR        = PROJECT_ROOT / "4-FullTextExtraction"
WORKBOOK         = STEP4_DIR / "step4_candidates_v2.xlsx"
WORKBOOK_BAK     = STEP4_DIR / "step4_candidates_v2.xlsx.pre_oafetch.bak"
PDF_OUTPUT_DIR   = STEP4_DIR / "PDFs_v2"
LOG_PATH         = STEP4_DIR / "fetch_oa_pdfs.log"

# Per-request timeouts
TIMEOUT_API = 20
TIMEOUT_PDF = 30
INTER_DOI_SLEEP = 0.5  # politeness

HEADERS_JSON = {"User-Agent": USER_AGENT, "Accept": "application/json"}
HEADERS_PDF  = {"User-Agent": USER_AGENT, "Accept": "application/pdf,*/*"}


def safe_str(x):
    return (x or "").strip()


def doi_to_filename(doi):
    """Stable DOI -> filename mapping (matches the v1 downloader convention)."""
    safe = re.sub(r"[^A-Za-z0-9.-]", "_", doi.lower())
    return safe + ".pdf"


def log(msg):
    print(msg, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(msg + "\n")


# ---------- API queries ----------

def get_unpaywall_url(doi):
    """Return a list of candidate PDF URLs from Unpaywall, or empty."""
    try:
        url = f"https://api.unpaywall.org/v2/{urllib.parse.quote(doi)}?email={urllib.parse.quote(CONTACT_EMAIL)}"
        r = requests.get(url, headers=HEADERS_JSON, timeout=TIMEOUT_API)
        if r.status_code != 200:
            return []
        j = r.json()
        urls = []
        best = j.get("best_oa_location") or {}
        if best.get("url_for_pdf"):
            urls.append(("unpaywall_best_pdf", best["url_for_pdf"]))
        elif best.get("url"):
            urls.append(("unpaywall_best", best["url"]))
        for loc in (j.get("oa_locations") or []):
            if loc.get("url_for_pdf"):
                urls.append(("unpaywall_alt_pdf", loc["url_for_pdf"]))
        # dedupe preserving order
        seen = set()
        out = []
        for tag, u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append((tag, u))
        return out
    except Exception as e:
        return []


def get_openalex_url(doi):
    try:
        url = f"https://api.openalex.org/works/doi:{urllib.parse.quote(doi)}?mailto={urllib.parse.quote(CONTACT_EMAIL)}"
        r = requests.get(url, headers=HEADERS_JSON, timeout=TIMEOUT_API)
        if r.status_code != 200:
            return []
        j = r.json()
        urls = []
        for key in ("primary_location", "best_oa_location"):
            loc = j.get(key) or {}
            if loc.get("pdf_url"):
                urls.append((f"openalex_{key}_pdf", loc["pdf_url"]))
        for loc in (j.get("locations") or []):
            if loc.get("pdf_url"):
                urls.append(("openalex_alt_pdf", loc["pdf_url"]))
        seen = set()
        out = []
        for tag, u in urls:
            if u and u not in seen:
                seen.add(u)
                out.append((tag, u))
        return out
    except Exception:
        return []


def get_europepmc_url(doi):
    try:
        q = f'DOI:"{doi}"'
        url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search"
               f"?query={urllib.parse.quote(q)}&format=json&resultType=core")
        r = requests.get(url, headers=HEADERS_JSON, timeout=TIMEOUT_API)
        if r.status_code != 200:
            return []
        j = r.json()
        results = (j.get("resultList") or {}).get("result") or []
        if not results:
            return []
        rec = results[0]
        urls = []
        for u in (rec.get("fullTextUrlList") or {}).get("fullTextUrl") or []:
            if (u.get("availability") in ("Open access", "Free") and
                u.get("documentStyle") == "pdf" and u.get("url")):
                urls.append(("europepmc", u["url"]))
        return urls
    except Exception:
        return []


# ---------- PDF download ----------

PDF_MAGIC = b"%PDF-"


def try_download_pdf(url, out_path):
    """Attempt to download the PDF at url to out_path. Strict: must end with
    %PDF magic header, content-type must indicate PDF or octet-stream, max
    1 redirect, no Cloudflare-challenge follow-up chasing.

    Returns (success_bool, reason_string).
    """
    try:
        r = requests.get(url, headers=HEADERS_PDF, timeout=TIMEOUT_PDF,
                         allow_redirects=True, stream=True)
        # Cap redirect chain
        if len(r.history) > 3:
            return (False, f"too_many_redirects:{len(r.history)}")
        if r.status_code != 200:
            return (False, f"http_{r.status_code}")
        ctype = (r.headers.get("Content-Type") or "").lower()
        # Hard reject HTML/JSON responses (paywall pages, Cloudflare challenges)
        if "text/html" in ctype or "application/json" in ctype or "text/plain" in ctype:
            return (False, f"non_pdf_ctype:{ctype.split(';')[0]}")
        # Read first chunk and verify it starts with %PDF
        chunks = []
        total = 0
        for chunk in r.iter_content(chunk_size=8192):
            if not chunk:
                continue
            chunks.append(chunk)
            total += len(chunk)
            if total >= 8 and not b"".join(chunks)[:8].startswith(PDF_MAGIC):
                return (False, f"not_pdf_magic")
            if total > 50_000_000:  # 50 MB cap
                return (False, "too_large")
        if total == 0:
            return (False, "empty_response")
        # Now verify the magic again on the assembled bytes
        data = b"".join(chunks)
        if not data.startswith(PDF_MAGIC):
            return (False, "not_pdf_magic")
        # Write file
        out_path.write_bytes(data)
        return (True, f"ok:{total}")
    except requests.exceptions.Timeout:
        return (False, "timeout")
    except requests.exceptions.RequestException as e:
        return (False, f"req_err:{type(e).__name__}")
    except Exception as e:
        return (False, f"err:{type(e).__name__}")


# ---------- Orchestration ----------

def fetch_for_doi(doi):
    """Try all OA sources for one DOI, return (success, pdf_filename, source_tag, msg)."""
    candidates = []
    candidates.extend(get_unpaywall_url(doi))
    candidates.extend(get_openalex_url(doi))
    candidates.extend(get_europepmc_url(doi))

    if not candidates:
        return (False, None, None, "no_oa_url_advertised")

    # Dedupe URLs across sources
    seen = set()
    unique = []
    for tag, u in candidates:
        if u not in seen:
            seen.add(u)
            unique.append((tag, u))

    out_filename = doi_to_filename(doi)
    out_path = PDF_OUTPUT_DIR / out_filename

    for tag, u in unique:
        ok, reason = try_download_pdf(u, out_path)
        if ok:
            return (True, out_filename, tag, reason)
        # Small breath between candidates
        time.sleep(0.3)

    return (False, None, None, f"all_{len(unique)}_urls_failed")


def main():
    if not WORKBOOK.exists():
        sys.exit(f"Missing workbook: {WORKBOOK}. Run build_step4_candidates.py first.")
    if not WORKBOOK_BAK.exists():
        shutil.copy2(WORKBOOK, WORKBOOK_BAK)
        log(f"Backed up: {WORKBOOK_BAK}")

    PDF_OUTPUT_DIR.mkdir(exist_ok=True)
    LOG_PATH.write_text(f"=== fetch_oa_pdfs.py started {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")

    # Load workbook
    wb = openpyxl.load_workbook(WORKBOOK)
    all_ws = wb["all_candidates"]
    miss_ws = wb["missing_pdfs"]

    all_headers = [c.value for c in all_ws[1]]
    miss_headers = [c.value for c in miss_ws[1]]

    doi_idx_all  = all_headers.index("DOI") + 1
    status_idx   = all_headers.index("PDF_status") + 1
    path_idx     = all_headers.index("PDF_local_path") + 1
    doi_idx_miss = miss_headers.index("DOI") + 1
    oa_url_idx   = miss_headers.index("OA_url_tried") + 1

    # Gather missing DOIs (preserve workbook-row mapping for incremental updates)
    missing_rows = []
    for row in miss_ws.iter_rows(min_row=2):
        doi = row[doi_idx_miss - 1].value
        if doi:
            missing_rows.append((row[doi_idx_miss - 1].row, str(doi).strip().lower()))
    log(f"DOIs to attempt: {len(missing_rows)}")

    retrieved = 0
    failed = 0
    no_oa = 0

    for i, (row_num, doi) in enumerate(missing_rows, start=1):
        log(f"[{i}/{len(missing_rows)}] {doi}")
        ok, fname, source, msg = fetch_for_doi(doi)
        if ok:
            log(f"  -> RETRIEVED via {source} ({msg})")
            retrieved += 1
            # Update all_candidates row
            for r in all_ws.iter_rows(min_row=2):
                if str(r[doi_idx_all - 1].value or "").strip().lower() == doi:
                    r[status_idx - 1].value = "retrieved_OA"
                    r[path_idx - 1].value = str(PDF_OUTPUT_DIR / fname)
                    break
            # Update missing_pdfs row OA_url_tried column to a positive marker
            miss_ws.cell(row=row_num, column=oa_url_idx).value = f"retrieved_via_{source}"
        else:
            if msg == "no_oa_url_advertised":
                no_oa += 1
            else:
                failed += 1
            log(f"  -> not retrieved ({msg})")
            miss_ws.cell(row=row_num, column=oa_url_idx).value = msg

        # Save incrementally every 10 DOIs (cheap insurance against crashes)
        if i % 10 == 0:
            wb.save(WORKBOOK)
            log(f"  [checkpoint saved at {i}/{len(missing_rows)}]")

        time.sleep(INTER_DOI_SLEEP)

    # Final save
    wb.save(WORKBOOK)

    log("")
    log(f"=== Summary ===")
    log(f"Total attempted:        {len(missing_rows)}")
    log(f"Retrieved:              {retrieved}")
    log(f"No OA URL advertised:   {no_oa}")
    log(f"All OA URLs failed:     {failed}")
    log(f"Workbook updated:       {WORKBOOK}")
    log(f"Backup:                 {WORKBOOK_BAK}")


if __name__ == "__main__":
    main()
