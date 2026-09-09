# -*- coding: utf-8 -*-
# Phase 1 deterministic harvest - reuse the code/data links Pass C already
# extracted and grounded, instead of re-detecting them with the subagent.
#
# Reads the AI extraction workbook (restricted to the included synthesis set via
# the included CSV) and emits, per paper, the code_url / data_identifier links
# with their existing Pass-C grounding quotes, each type-triaged and flagged for
# whether Phase 2 should fetch it. Papers that claim a release but have no
# extracted URL are flagged as gap candidates for the detection subagent.
#
# No LLM. No PDF read. Raw extraction values are never mutated.
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python harvest_known_links.py <extractions_ai*.xlsx> <included_step5_v134.csv> <out_dir>

import sys
import os
import re
import csv
import json

from openpyxl import load_workbook

ABSENCE = set(["", "not_reported", "none", "none_reported", "not_applicable",
               "null", "n/a", "na", "-"])

RE_URL = re.compile(r"https?://", re.I)
RE_DOI = re.compile(r"\b10\.\d{4,9}/|doi\.org/", re.I)
RE_REPO = re.compile(r"github\.com|gitlab\.com|bitbucket\.org|codeocean", re.I)
RE_AUTHOR_DATA = re.compile(
    r"zenodo|figshare|dryad|osf\.io|mendeley|dataverse|"
    r"drive\.google|dropbox|onedrive|1drv\.ms", re.I)


def norm(v):
    return "" if v is None else str(v).strip()


def is_absence(v):
    return norm(v).lower() in ABSENCE


def has_locator(s):
    return bool(RE_URL.search(s) or RE_DOI.search(s))


def triage(field, value):
    """Return (type, fetch_target, note).
    fetch_target is True ONLY for author-deposited code/data (the
    enrichment-relevant supplements). Public dataset portals and bare dataset
    names are NOT fetch targets: re-downloading the public data the paper used
    adds nothing to the extraction."""
    s = value
    if RE_REPO.search(s):
        return ("code_repository", True, "")
    if RE_AUTHOR_DATA.search(s):
        t = "data_doi" if (RE_DOI.search(s) and not RE_URL.search(s)) else "data_repository"
        return (t, True, "")
    if field == "code_url":
        # the "code is available at" field: a URL/DOI here is almost always
        # author code even if the host is unrecognised -> fetch.
        if RE_URL.search(s) or RE_DOI.search(s):
            return ("code_repository", True, "")
        return ("code_repository", False, "locator_unresolved (no URL/DOI in cell)")
    # field == data_identifier
    if RE_DOI.search(s) and not RE_URL.search(s):
        return ("data_doi", True, "")
    if RE_URL.search(s):
        return ("external_data_url", False,
                "review: URL but not a known author-deposit host (likely public dataset portal)")
    return ("named_dataset_ref", False, "public/benchmark dataset reference, not a fetch target")


def main():
    if len(sys.argv) != 4:
        sys.stderr.write("usage: harvest_known_links.py <xlsx> <included_csv> <out_dir>\n")
        sys.exit(2)
    xlsx, inc_csv, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    inc = set()
    with open(inc_csv, "r") as fh:
        for row in csv.DictReader(fh):
            sn = norm(row.get("stable_name"))
            if sn:
                inc.add(sn)

    wb = load_workbook(xlsx, data_only=False)
    ws = wb["extractions"]
    names = [norm(ws.cell(row=2, column=c).value) for c in range(1, ws.max_column + 1)]

    def col(name):
        return names.index(name) + 1

    c_doi = col("doi")
    c_sn = col("stable_name")
    LINK_FIELDS = [("code_url", "code_url_quote"), ("data_identifier", "data_identifier_quote")]
    FLAG_FIELDS = ["code_release", "data_release", "weights_release"]
    field_col = {}
    for vf, qf in LINK_FIELDS:
        field_col[vf] = col(vf)
        field_col[qf] = col(qf)
    for ff in FLAG_FIELDS:
        field_col[ff] = col(ff)

    out_path = os.path.join(out_dir, "harvested_links.jsonl")
    recs = []
    type_tally = {}
    n_links = 0
    n_fetch = 0
    gap_papers = []  # release claimed but no extracted URL

    for r in range(3, ws.max_row + 1):
        sn = norm(ws.cell(row=r, column=c_sn).value)
        if sn not in inc:
            continue
        doi = norm(ws.cell(row=r, column=c_doi).value).lower()
        links = []
        for vf, qf in LINK_FIELDS:
            val = norm(ws.cell(row=r, column=field_col[vf]).value)
            if is_absence(val):
                continue
            quote = norm(ws.cell(row=r, column=field_col[qf]).value)
            typ, fetch, note = triage(vf, val)
            links.append({
                "type": typ,
                "locator": val,
                "quote": quote,
                "source": "pass_c_extraction",
                "field": vf,
                "fetch_target": bool(fetch),
                "note": note,
            })
            type_tally[typ] = type_tally.get(typ, 0) + 1
            n_links += 1
            if fetch:
                n_fetch += 1
        flags = {}
        for ff in FLAG_FIELDS:
            flags[ff] = norm(ws.cell(row=r, column=field_col[ff]).value).upper() == "TRUE"
        # gap candidate: claims a release but no extracted URL for that modality
        claims = flags["code_release"] or flags["data_release"]
        has_url_link = any(l["fetch_target"] for l in links)
        if claims and not has_url_link:
            gap_papers.append(sn)
        if links or claims:
            recs.append({
                "stable_name": sn, "doi": doi, "links": links,
                "release_flags": flags, "n_links": len(links),
                "release_claimed_no_url": bool(claims and not has_url_link),
            })

    with open(out_path, "w") as fh:
        for rec in recs:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # summary csv
    sum_path = os.path.join(out_dir, "harvested_links_summary.csv")
    with open(sum_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["metric", "value"])
        w.writerow(["included_papers", len(inc)])
        w.writerow(["papers_with_harvested_link_or_release", len(recs)])
        w.writerow(["papers_with_at_least_one_fetchable_link",
                    sum(1 for x in recs if any(l["fetch_target"] for l in x["links"]))])
        w.writerow(["total_harvested_links", n_links])
        w.writerow(["fetchable_links", n_fetch])
        w.writerow(["non_fetch_links (named_dataset_ref / unresolved)", n_links - n_fetch])
        w.writerow(["papers_release_claimed_no_url (gap for subagent)", len(gap_papers)])
        for t in sorted(type_tally):
            w.writerow(["type::" + t, type_tally[t]])

    gap_path = os.path.join(out_dir, "harvest_gap_release_no_url.txt")
    with open(gap_path, "w") as fh:
        fh.write("\n".join(sorted(gap_papers)) + ("\n" if gap_papers else ""))

    print("included papers: {}".format(len(inc)))
    print("papers with harvested link or release flag: {}".format(len(recs)))
    print("papers with >=1 fetchable link: {}".format(
        sum(1 for x in recs if any(l["fetch_target"] for l in x["links"]))))
    print("total harvested links: {} | fetchable: {} | non-fetch: {}".format(
        n_links, n_fetch, n_links - n_fetch))
    print("type tally: {}".format(type_tally))
    print("release-claimed-no-url (subagent gap targets): {}".format(len(gap_papers)))
    print("wrote: {} | {} | {}".format(out_path, sum_path, gap_path))


if __name__ == "__main__":
    main()
