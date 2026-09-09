# -*- coding: utf-8 -*-
# Phase 1 merge - union the deterministic harvest (Pass-C code/data links) with
# the subagent detection (SI/appendix + any links Pass C missed), dedupe by
# normalised locator, recover author-deposit links wrongly rejected on a quote
# mismatch, and tier every included paper for Phase 2 fetch.
#
# Tiers:
#   T1_author_deposit  - github/zenodo/figshare/IEEE-DataPort/etc. Clean fetch.
#   T2_publisher_si    - SI exists (ESM / Fig SX / Table SX / article DOI / a
#                        release flag) but retrieval is publisher-specific.
#   T3_public_or_other - only public dataset portals or unresolved URLs. No fetch.
#   T4_none            - no external material found by either source.
#
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python merge_links.py <harvested_links.jsonl> <supp_links.jsonl> <supp_links__rejected_quotes.csv> <out_dir>

import sys
import os
import re
import csv
import json

RE_AUTHOR = re.compile(
    r"github\.com|gitlab\.com|bitbucket\.org|codeocean|zenodo|figshare|dryad|"
    r"osf\.io|mendeley|dataverse|10\.21227|drive\.google|dropbox|onedrive|1drv\.ms", re.I)
RE_PUBLIC = re.compile(
    r"kaggle\.com|grand-challenge|physionet|openneuro|isic-archive|med\.upenn|"
    r"ircad|adcis|wiki\.cancerimagingarchive|cancerimagingarchive", re.I)
RE_URL = re.compile(r"https?://", re.I)
RE_DOI = re.compile(r"\b10\.\d{4,9}/|doi\.org/", re.I)
RE_SIREF = re.compile(
    r"supplement|\besm\b|electronic supplementary|supporting information|appendix|"
    r"fig\.?\s*s\d|figure\s*s\d|table\s*s\d|eqs?\.?\s*e\d|fig\.?\s*e\d", re.I)

# author-deposit links wrongly rejected on quote grounding; recover and flag
RECOVER = {
    "10_3389_fonc_2025_1553539": [
        {"type": "code_repository", "locator": "https://github.com/rachelglenn/qcuts3D"}],
    "10_1109_tce_2024_3351649": [
        {"type": "data_doi", "locator": "https://dx.doi.org/10.21227/5cr5-0204"}],
}


def nloc(s):
    s = str(s if s is not None else "").strip().rstrip(").,;]")
    low = re.sub(r"^https?://", "", s.lower())
    low = re.sub(r"^www\.", "", low)
    low = low.rstrip("/")
    low = low.replace("dx.doi.org/", "doi.org/")
    return low


def norm_doi(s):
    return str(s or "").strip().lower()


def classify_link(locator, ltype, paper_doi):
    """Return one of: author_deposit, public_portal, article_doi, si_reference,
    other_url, other."""
    loc = str(locator or "")
    if RE_AUTHOR.search(loc):
        return "author_deposit"
    if RE_PUBLIC.search(loc):
        return "public_portal"
    pd = norm_doi(paper_doi)
    if pd and pd in nloc(loc):
        return "article_doi"
    if RE_SIREF.search(loc) or ltype == "supplementary_material":
        return "si_reference"
    if RE_URL.search(loc) or RE_DOI.search(loc):
        return "other_url"
    return "other"


def load_jsonl(path):
    out = []
    with open(path, "r") as fh:
        for line in fh:
            if line.strip():
                out.append(json.loads(line))
    return out


def main():
    if len(sys.argv) != 5:
        sys.stderr.write("usage: merge_links.py <harvested.jsonl> <supp_links.jsonl> <rejected.csv> <out_dir>\n")
        sys.exit(2)
    harv_p, det_p, rej_p, out_dir = sys.argv[1:5]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    harv = load_jsonl(harv_p)
    det = load_jsonl(det_p)

    papers = {}  # stable_name -> record

    def ensure(sn, doi):
        if sn not in papers:
            papers[sn] = {"stable_name": sn, "doi": norm_doi(doi),
                          "links": {}, "release_flags": {}}
        elif doi and not papers[sn]["doi"]:
            papers[sn]["doi"] = norm_doi(doi)
        return papers[sn]

    def add_link(rec, ltype, locator, quote, source):
        key = nloc(locator)
        if not key:
            return
        if key in rec["links"]:
            if source not in rec["links"][key]["source"]:
                rec["links"][key]["source"].append(source)
            return
        rec["links"][key] = {"type": ltype, "locator": str(locator), "quote": quote,
                             "source": [source]}

    for r in harv:
        rec = ensure(r["stable_name"], r.get("doi", ""))
        rec["release_flags"] = r.get("release_flags", {})
        for l in r.get("links", []):
            add_link(rec, l["type"], l["locator"], l.get("quote", ""), "harvest")
    for r in det:
        rec = ensure(r["stable_name"], r.get("doi", ""))
        for l in r.get("links", []):
            add_link(rec, l["type"], l["locator"], l.get("quote", ""), "detection")
    for sn, links in RECOVER.items():
        if sn in papers:
            for l in links:
                add_link(papers[sn], l["type"], l["locator"], "", "detection_recovered")

    # classify + tier each paper
    tier_counts = {"T1_author_deposit": 0, "T2_publisher_si": 0,
                   "T3_public_or_other": 0, "T4_none": 0}
    out_recs = []
    fetch_targets = []
    for sn in sorted(papers):
        rec = papers[sn]
        pd = rec["doi"]
        author = []
        si = []
        public_other = []
        for key, l in rec["links"].items():
            cls = classify_link(l["locator"], l["type"], pd)
            entry = {"type": l["type"], "locator": l["locator"], "quote": l["quote"],
                     "source": l["source"], "class": cls}
            if cls == "author_deposit":
                author.append(entry)
            elif cls in ("si_reference", "article_doi"):
                si.append(entry)
            else:
                public_other.append(entry)
        flags = rec.get("release_flags", {})
        release_claim = bool(flags.get("code_release") or flags.get("data_release"))
        if author:
            tier = "T1_author_deposit"
        elif si or release_claim:
            tier = "T2_publisher_si"
        elif public_other:
            tier = "T3_public_or_other"
        else:
            tier = "T4_none"
        tier_counts[tier] += 1
        out_recs.append({
            "stable_name": sn, "doi": pd, "tier": tier,
            "has_author_deposit": bool(author), "has_si": bool(si) or release_claim,
            "author_deposit_links": author, "si_signals": si,
            "public_or_other": public_other, "release_flags": flags,
        })
        for a in author:
            fetch_targets.append({
                "stable_name": sn, "doi": pd, "type": a["type"],
                "locator": a["locator"], "source": a["source"],
                "needs_quote_verify": "detection_recovered" in a["source"],
            })

    merged_path = os.path.join(out_dir, "merged_links.jsonl")
    with open(merged_path, "w") as fh:
        for rec in out_recs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    ft_path = os.path.join(out_dir, "phase2_fetch_targets.jsonl")
    with open(ft_path, "w") as fh:
        for t in fetch_targets:
            fh.write(json.dumps(t, ensure_ascii=False) + "\n")

    # summary
    sum_path = os.path.join(out_dir, "merge_summary.csv")
    fetch_papers = sorted(set(t["stable_name"] for t in fetch_targets))
    si_papers = [r["stable_name"] for r in out_recs if r["tier"] == "T2_publisher_si"]
    with open(sum_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "value"])
        w.writerow(["papers_merged", len(out_recs)])
        for t in ["T1_author_deposit", "T2_publisher_si", "T3_public_or_other", "T4_none"]:
            w.writerow([t, tier_counts[t]])
        w.writerow(["papers_with_author_deposit_fetch_target", len(fetch_papers)])
        w.writerow(["author_deposit_fetch_links_total", len(fetch_targets)])
        w.writerow(["recovered_links (flagged needs_quote_verify)",
                    sum(1 for t in fetch_targets if t["needs_quote_verify"])])

    print("papers merged: {}".format(len(out_recs)))
    print("tiers: {}".format(tier_counts))
    print("papers with an author-deposit fetch target (T1): {}".format(len(fetch_papers)))
    print("author-deposit fetch links: {} (recovered+flagged: {})".format(
        len(fetch_targets), sum(1 for t in fetch_targets if t["needs_quote_verify"])))
    print("T2 publisher-SI papers (SI exists, publisher-specific retrieval): {}".format(len(si_papers)))
    print("wrote: {} | {} | {}".format(merged_path, ft_path, sum_path))


if __name__ == "__main__":
    main()
