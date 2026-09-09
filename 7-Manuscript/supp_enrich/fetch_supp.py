# -*- coding: utf-8 -*-
# Phase 2 - comprehensive supplemental fetcher.
#
# Gathers, per included paper, the FULLEST reachable set of supplemental material:
#   deposits : author code repos (git clone) + data deposits (Zenodo/figshare/OSF
#              APIs; others via generic GET).
#   oa       : OA full text + SI via Unpaywall -> Crossref -> NCBI PMCID ->
#              PMC OA package (tgz, bundles SI) -> EuropePMC supplementaryFiles
#              (zip). Needs NO institutional auth; carries the bulk for OA papers.
#   si       : resolve doi.org -> landing page -> scrape supplementary/SI/media
#              links and download them, using whatever access the egress IP grants
#              + light publisher hints (MDPI /s1, Nature MediaObjects, PeerJ).
#
# Every HTTP response is CLASSIFIED (pdf / zip / data / paywall_html /
# cloudflare_block / dead) so a login wall is never saved as a "file". Resumable:
# per-paper per-stage .done markers + content-hash dedup. Gathers from every
# route (no early-stop) for maximal coverage.
#
# ACCESS NOTE: institutional (paywalled) SI requires the egress IP to be on the
# subscribing network AND the publisher to allow non-browser clients. MedUni
# Vienna's registered block is 131.130.0.0/16; if your egress IP is outside it,
# gated SI will log as paywall_html/forbidden, not download. The OA stage does
# not depend on this. Read fetch_log.csv after run-1 to see what the IP reaches.
#
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
# Dependency: requests (pip install requests --break-system-packages).
#
# Usage:
#   python fetch_supp.py \
#     --included ../../5-Adjudication/included_step5_v134.csv \
#     --fetch-targets out/phase2_fetch_targets.jsonl \
#     --merged out/merged_links.jsonl \
#     --out supp_corpus --email laszlo.papp@meduniwien.ac.at \
#     [--github-token ghp_xxx] [--stages deposits,oa,si] [--delay 1.0]
#   python fetch_supp.py --selftest

import sys
import os
import re
import csv
import json
import time
import hashlib
import argparse
import subprocess

try:
    import requests
    from requests.adapters import HTTPAdapter
    try:
        from urllib3.util.retry import Retry
    except Exception:
        Retry = None
except Exception:
    requests = None

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36 (systematic-review TDM; {email})")

PAYWALL_MARKERS = [
    "access denied", "sign in", "log in to", "institutional login", "subscribe",
    "purchase access", "get access", "buy article", "shibboleth", "ezproxy",
    "please verify you are a human", "checking your browser", "cf-browser-verification",
    "captcha", "403 forbidden", "not authorized",
]
CF_MARKERS = ["cloudflare", "cf-ray", "checking your browser", "cf-browser-verification"]

SI_HINT = re.compile(
    r"supplement|supporting[ _-]?information|\besm\b|electronic[ _-]supplementary|"
    r"media[ _-]?object|datasheet|/s1\b|additional[ _-]file|appendix|annex", re.I)
HREF = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.I)
ATAG = re.compile(r'<a\b[^>]*>(.*?)</a>', re.I | re.S)


# ---------- helpers ----------

def safe_name(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")[:120] or "file"


def sha1_bytes(b):
    return hashlib.sha1(b).hexdigest()


def classify(head, resp):
    """Return a label for a response from its first bytes + headers."""
    code = getattr(resp, "status_code", 0)
    ct = (resp.headers.get("Content-Type", "") if resp is not None else "").lower()
    if head[:5] == b"%PDF-":
        return "pdf"
    if head[:4] == b"PK\x03\x04":
        return "zip"
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    low = head[:4000].decode("utf-8", "replace").lower()
    looks_html = ("<html" in low or "<!doctype html" in low or "text/html" in ct)
    if looks_html:
        if any(m in low for m in CF_MARKERS):
            return "cloudflare_block"
        if any(m in low for m in PAYWALL_MARKERS) or code in (401, 403):
            return "paywall_html"
        return "landing_html"
    if "json" in ct or low.startswith("{") or low.startswith("["):
        return "json"
    if "xml" in ct or low.startswith("<?xml"):
        return "xml"
    if code >= 400:
        return "http_{}".format(code)
    if "csv" in ct or "excel" in ct or "spreadsheet" in ct:
        return "data"
    return "binary"


def make_session(email, token):
    s = requests.Session()
    s.headers.update({"User-Agent": UA.format(email=email),
                      "From": email, "Accept": "*/*"})
    if Retry is not None:
        rk = dict(total=4, backoff_factor=1.5,
                  status_forcelist=[429, 500, 502, 503, 504])
        try:
            r = Retry(allowed_methods=["GET", "HEAD"], **rk)      # urllib3 >= 1.26
        except TypeError:
            try:
                r = Retry(method_whitelist=["GET", "HEAD"], **rk)  # urllib3 < 1.26
            except TypeError:
                r = Retry(**rk)                                    # last resort
        ad = HTTPAdapter(max_retries=r)
        s.mount("https://", ad)
        s.mount("http://", ad)
    s._gh_token = token
    return s


class Ctx(object):
    def __init__(self, session, email, delay, log_writer, manifest_fh):
        self.s = session
        self.email = email
        self.delay = delay
        self.log = log_writer
        self.man = manifest_fh

    def record(self, sn, stage, source, url, result, path="", size=0, sha=""):
        self.log.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), sn, stage, source,
                           url, result, path, size, sha])
        if path:
            self.man.write(json.dumps({"stable_name": sn, "stage": stage,
                                       "source": source, "url": url, "path": path,
                                       "size": size, "sha1": sha}, ensure_ascii=False) + "\n")

    def get(self, url, stream=True, timeout=60):
        time.sleep(self.delay)
        return self.s.get(url, stream=stream, timeout=timeout, allow_redirects=True)


def download(ctx, sn, stage, source, url, dest_dir, seen):
    """GET url, classify, save only real files (not html). Returns label."""
    if not os.path.isdir(dest_dir):
        os.makedirs(dest_dir)
    try:
        resp = ctx.get(url)
    except Exception as e:
        ctx.record(sn, stage, source, url, "error:{}".format(type(e).__name__))
        return "error"
    it = resp.iter_content(65536)
    try:
        first = next(it)
    except StopIteration:
        first = b""
    except Exception:
        first = b""
    label = classify(first, resp)
    if label in ("pdf", "zip", "gzip", "json", "xml", "data", "binary"):
        buf = [first]
        for chunk in it:
            buf.append(chunk)
        body = b"".join(buf)
        sha = sha1_bytes(body)
        if sha in seen:
            ctx.record(sn, stage, source, url, label + ":dup")
            return label + ":dup"
        seen.add(sha)
        ext = {"pdf": ".pdf", "zip": ".zip", "gzip": ".tar.gz", "json": ".json",
               "xml": ".xml", "data": ".dat"}.get(label, "")
        base = safe_name(url.rsplit("/", 1)[-1].split("?")[0]) or (source + ext)
        if ext and not base.lower().endswith(ext):
            base = base + ext
        path = os.path.join(dest_dir, base)
        i = 1
        while os.path.exists(path):
            path = os.path.join(dest_dir, "{}_{}{}".format(os.path.splitext(base)[0], i,
                                                           os.path.splitext(base)[1]))
            i += 1
        with open(path, "wb") as fh:
            fh.write(body)
        ctx.record(sn, stage, source, url, label, path, len(body), sha)
        return label
    # not a file (html/paywall/block/dead)
    resp.close()
    ctx.record(sn, stage, source, url, label)
    return label


def extract_si_links(base_url, html):
    """Return absolute candidate SI links from a landing page."""
    out = []
    for m in ATAG.finditer(html):
        atag = m.group(0)
        text = re.sub(r"<[^>]+>", " ", m.group(1))
        hm = HREF.search(atag)
        if not hm:
            continue
        href = hm.group(1)
        if SI_HINT.search(href) or SI_HINT.search(text):
            out.append(absolutize(base_url, href))
    # also bare hrefs that look like SI files
    for hm in HREF.finditer(html):
        href = hm.group(1)
        if SI_HINT.search(href) and href.lower().split("?")[0].endswith(
                (".pdf", ".zip", ".docx", ".xlsx", ".csv", ".txt")):
            out.append(absolutize(base_url, href))
    seen = set()
    uniq = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def absolutize(base, href):
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    m = re.match(r"(https?://[^/]+)", base or "")
    root = m.group(1) if m else ""
    if href.startswith("/"):
        return root + href
    return (base.rsplit("/", 1)[0] + "/" + href) if base else href


# ---------- stages ----------

def stage_deposits(ctx, sn, doi, targets, dest, seen):
    for t in targets:
        loc = (t.get("locator") or "").strip()
        typ = t.get("type", "")
        if not loc:
            continue
        if re.search(r"github\.com|gitlab\.com|bitbucket\.org", loc, re.I):
            url = loc.split("#")[0].rstrip("/")
            if not url.endswith(".git"):
                url = url + ".git"
            repo = safe_name(loc.rstrip("/").split("/")[-1].replace(".git", ""))
            out = os.path.join(dest, "repo_" + repo)
            if os.path.isdir(out):
                ctx.record(sn, "deposits", "git", url, "exists")
                continue
            try:
                p = subprocess.run(["git", "clone", "--depth", "1", url, out],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=600)
                ok = (p.returncode == 0)
                ctx.record(sn, "deposits", "git", url,
                           "cloned" if ok else "git_fail", out if ok else "")
            except Exception as e:
                ctx.record(sn, "deposits", "git", url, "error:{}".format(type(e).__name__))
            continue
        # data deposits
        m = re.search(r"zenodo\.org/record(?:s)?/(\d+)|zenodo\.(\d+)", loc, re.I)
        if m:
            rid = m.group(1) or m.group(2)
            api = "https://zenodo.org/api/records/{}".format(rid)
            zenodo_fetch(ctx, sn, api, dest, seen)
            continue
        m = re.search(r"figshare", loc, re.I)
        if m:
            figshare_fetch(ctx, sn, loc, dest, seen)
            continue
        m = re.search(r"osf\.io/([a-z0-9]+)", loc, re.I)
        if m:
            osf_fetch(ctx, sn, m.group(1), dest, seen)
            continue
        # generic (IEEE DataPort, Dryad, Mendeley, etc.): GET and classify
        if loc.startswith("http") or "doi.org" in loc:
            url = loc if loc.startswith("http") else "https://doi.org/" + loc.split("doi.org/")[-1]
            download(ctx, sn, "deposits", "generic_deposit", url, dest, seen)


def zenodo_fetch(ctx, sn, api, dest, seen):
    try:
        r = ctx.get(api, stream=False)
        j = r.json()
    except Exception as e:
        ctx.record(sn, "deposits", "zenodo_api", api, "error:{}".format(type(e).__name__))
        return
    for f in j.get("files", []):
        link = (f.get("links") or {}).get("self") or f.get("key")
        if link and link.startswith("http"):
            download(ctx, sn, "deposits", "zenodo", link, dest, seen)


def figshare_fetch(ctx, sn, loc, dest, seen):
    m = re.search(r"articles/[^/]*?/?(\d+)", loc) or re.search(r"(\d{6,})", loc)
    if not m:
        ctx.record(sn, "deposits", "figshare", loc, "no_id")
        return
    api = "https://api.figshare.com/v2/articles/{}".format(m.group(1))
    try:
        r = ctx.get(api, stream=False)
        j = r.json()
    except Exception as e:
        ctx.record(sn, "deposits", "figshare_api", api, "error:{}".format(type(e).__name__))
        return
    for f in j.get("files", []):
        u = f.get("download_url")
        if u:
            download(ctx, sn, "deposits", "figshare", u, dest, seen)


def osf_fetch(ctx, sn, node, dest, seen):
    api = "https://api.osf.io/v2/nodes/{}/files/osfstorage/".format(node)
    try:
        r = ctx.get(api, stream=False)
        j = r.json()
    except Exception as e:
        ctx.record(sn, "deposits", "osf_api", api, "error:{}".format(type(e).__name__))
        return
    for d in j.get("data", []):
        links = d.get("links") or {}
        u = links.get("download")
        if u:
            download(ctx, sn, "deposits", "osf", u, dest, seen)


def stage_oa(ctx, sn, doi, dest, seen):
    if not doi:
        return
    # 1 Unpaywall
    up = "https://api.unpaywall.org/v2/{}?email={}".format(doi, ctx.email)
    try:
        j = ctx.get(up, stream=False).json()
        for loc in (j.get("oa_locations") or []):
            for key in ("url_for_pdf", "url"):
                u = loc.get(key)
                if u:
                    download(ctx, sn, "oa", "unpaywall", u, dest, seen)
    except Exception as e:
        ctx.record(sn, "oa", "unpaywall", up, "error:{}".format(type(e).__name__))
    # 2 Crossref full-text links
    cr = "https://api.crossref.org/works/{}".format(doi)
    try:
        msg = ctx.get(cr, stream=False).json().get("message", {})
        for ln in (msg.get("link") or []):
            u = ln.get("URL")
            if u:
                download(ctx, sn, "oa", "crossref", u, dest, seen)
    except Exception as e:
        ctx.record(sn, "oa", "crossref", cr, "error:{}".format(type(e).__name__))
    # 3 NCBI PMCID
    pmcid = pmid = None
    idc = ("https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids={}&format=json"
           "&tool=qcsysrev&email={}".format(doi, ctx.email))
    try:
        recs = ctx.get(idc, stream=False).json().get("records", [])
        if recs:
            pmcid = recs[0].get("pmcid")
            pmid = recs[0].get("pmid")
    except Exception as e:
        ctx.record(sn, "oa", "ncbi_idconv", idc, "error:{}".format(type(e).__name__))
    # 4 PMC OA package (tgz bundles SI)
    if pmcid:
        oa = "https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={}".format(pmcid)
        try:
            xml = ctx.get(oa, stream=False).text
            m = re.search(r'href="(ftp://[^"]+\.tar\.gz)"', xml) or \
                re.search(r'href="(https?://[^"]+\.tar\.gz)"', xml)
            if m:
                href = m.group(1).replace("ftp://ftp.ncbi.nlm.nih.gov",
                                          "https://ftp.ncbi.nlm.nih.gov")
                download(ctx, sn, "oa", "pmc_oa_package", href, dest, seen)
        except Exception as e:
            ctx.record(sn, "oa", "pmc_oa", oa, "error:{}".format(type(e).__name__))
    # 5 EuropePMC supplementaryFiles (zip)
    src = idv = None
    epmc_s = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:%22{}%22"
              "&resultType=core&format=json".format(doi))
    try:
        res = ctx.get(epmc_s, stream=False).json().get("resultList", {}).get("result", [])
        if res:
            src = res[0].get("source")
            idv = res[0].get("id")
    except Exception as e:
        ctx.record(sn, "oa", "europepmc_search", epmc_s, "error:{}".format(type(e).__name__))
    if src and idv:
        sf = ("https://www.ebi.ac.uk/europepmc/webservices/rest/{}/{}/supplementaryFiles"
              .format(src, idv))
        download(ctx, sn, "oa", "europepmc_suppfiles", sf, dest, seen)


def stage_si(ctx, sn, doi, merged_si_urls, dest, seen):
    # direct SI file URLs the detector already found
    for u in merged_si_urls:
        if u.startswith("http"):
            download(ctx, sn, "si", "detected_si_url", u, dest, seen)
    if not doi:
        return
    # resolve doi.org -> landing, scrape SI links
    land = "https://doi.org/{}".format(doi)
    try:
        r = ctx.get(land, stream=True)
        head = b""
        try:
            head = next(r.iter_content(8000))
        except Exception:
            head = b""
        label = classify(head, r)
        final = r.url
        if label in ("landing_html",):
            html = head.decode("utf-8", "replace")
            try:
                for chunk in r.iter_content(65536):
                    html += chunk.decode("utf-8", "replace")
                    if len(html) > 3000000:
                        break
            except Exception:
                pass
            r.close()
            links = extract_si_links(final, html)
            # publisher hints
            if "mdpi.com" in final and not any("/s1" in l for l in links):
                links.append(final.rstrip("/") + "/s1")
            ctx.record(sn, "si", "landing_scrape", final, "links_{}".format(len(links)))
            for u in links:
                download(ctx, sn, "si", "landing_si", u, dest, seen)
        else:
            r.close()
            ctx.record(sn, "si", "landing", final, label)
    except Exception as e:
        ctx.record(sn, "si", "landing", land, "error:{}".format(type(e).__name__))


# ---------- driver ----------

def load_included(path):
    out = []
    with open(path, "r") as fh:
        for row in csv.DictReader(fh):
            sn = (row.get("stable_name") or "").strip()
            doi = (row.get("doi") or "").strip().lower()
            if sn:
                out.append((sn, doi))
    return out


def load_jsonl(path):
    out = []
    if path and os.path.exists(path):
        with open(path, "r") as fh:
            for line in fh:
                if line.strip():
                    out.append(json.loads(line))
    return out


def run(a):
    if requests is None:
        sys.exit("requests not installed: pip install requests --break-system-packages")
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    included = load_included(a.included)
    targets_by = {}
    for t in load_jsonl(a.fetch_targets):
        targets_by.setdefault(t["stable_name"], []).append(t)
    si_by = {}
    for r in load_jsonl(a.merged):
        urls = []
        for l in (r.get("si_signals") or []):
            loc = l.get("locator", "")
            if isinstance(loc, str) and loc.startswith("http"):
                urls.append(loc)
        if urls:
            si_by[r["stable_name"]] = urls

    stages = [s.strip() for s in a.stages.split(",") if s.strip()]
    session = make_session(a.email, a.github_token)
    logf = open(os.path.join(a.out, "fetch_log.csv"), "a", newline="")
    if os.stat(os.path.join(a.out, "fetch_log.csv")).st_size == 0:
        csv.writer(logf).writerow(["ts", "stable_name", "stage", "source", "url",
                                   "result", "path", "size", "sha1"])
    logw = csv.writer(logf)
    manf = open(os.path.join(a.out, "fetched_manifest.jsonl"), "a")
    ctx = Ctx(session, a.email, a.delay, logw, manf)

    for i, (sn, doi) in enumerate(included, 1):
        pdir = os.path.join(a.out, sn)
        if not os.path.isdir(pdir):
            os.makedirs(pdir)
        seen = set()
        for stage in stages:
            marker = os.path.join(pdir, "._done_{}".format(stage))
            if os.path.exists(marker):
                continue
            ddir = os.path.join(pdir, stage)
            if stage == "deposits":
                stage_deposits(ctx, sn, doi, targets_by.get(sn, []), ddir, seen)
            elif stage == "oa":
                stage_oa(ctx, sn, doi, ddir, seen)
            elif stage == "si":
                stage_si(ctx, sn, doi, si_by.get(sn, []), ddir, seen)
            open(marker, "w").write(time.strftime("%Y-%m-%dT%H:%M:%S"))
        logf.flush()
        manf.flush()
        sys.stderr.write("[{}/{}] {}\n".format(i, len(included), sn))
    logf.close()
    manf.close()
    print("done. logs in {}/fetch_log.csv and fetched_manifest.jsonl".format(a.out))


def selftest():
    # classifier
    class R(object):
        def __init__(self, code, ct):
            self.status_code = code
            self.headers = {"Content-Type": ct}
    assert classify(b"%PDF-1.7 ...", R(200, "application/pdf")) == "pdf"
    assert classify(b"PK\x03\x04rest", R(200, "application/zip")) == "zip"
    assert classify(b"\x1f\x8b\x08", R(200, "application/gzip")) == "gzip"
    assert classify(b'{"a":1}', R(200, "application/json")) == "json"
    assert classify(b"<html><body>Please sign in to access</body></html>",
                    R(200, "text/html")) == "paywall_html"
    assert classify(b"<html>cf-ray checking your browser</html>",
                    R(403, "text/html")) == "cloudflare_block"
    assert classify(b"<html><body>Article landing</body></html>",
                    R(200, "text/html")) == "landing_html"
    # si link extraction + absolutize
    html = ('<a href="/articles/x/s1">Supplementary Material</a>'
            '<a href="https://cdn.pub/esm.pdf">ESM</a>'
            '<a href="/normal">home</a>')
    links = extract_si_links("https://www.mdpi.com/2079/13/4/401", html)
    assert "https://www.mdpi.com/articles/x/s1" in links, links
    assert "https://cdn.pub/esm.pdf" in links, links
    assert all("/normal" not in l for l in links)
    assert absolutize("https://x.com/a/b", "//cdn/y.pdf") == "https://cdn/y.pdf"
    assert absolutize("https://x.com/a/b", "/z") == "https://x.com/z"
    print("selftest OK: classifier + SI extraction + absolutize")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--included")
    ap.add_argument("--fetch-targets", dest="fetch_targets")
    ap.add_argument("--merged")
    ap.add_argument("--out", default="supp_corpus")
    ap.add_argument("--email", default="")
    ap.add_argument("--github-token", dest="github_token", default="")
    ap.add_argument("--stages", default="deposits,oa,si")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.included or not a.email:
        ap.error("--included and --email are required (email for the OA polite pool)")
    run(a)


if __name__ == "__main__":
    main()
