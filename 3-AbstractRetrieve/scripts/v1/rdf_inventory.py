"""
rdf_inventory.py
Parse all Zotero RDF exports under --rdf-root, build a DOI -> PDF path index,
intersect with the title-only records in merged_dataset.json, and report
how many of the title-only records have a PDF available locally.

Output: rdf_pdf_index.csv with columns:
    record_id, doi, rdf_file, item_id, pdf_relpath, pdf_abs_path,
    pdf_exists, pdf_size_bytes

This is a discovery / accounting step. It does NOT extract any text yet.

Usage:
    python rdf_inventory.py \\
        --rdf-root /home/lpapp/IEEE_SYS_REV/PDFs \\
        --corpus merged_dataset.json \\
        --output rdf_pdf_index.csv

Optional:
    --all-records      Include ALL corpus records, not just title-only
                       (useful for sanity checks and for the later step where
                       you may want to verify PDFs exist for already-screened
                       records too)
"""

import argparse
import csv
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

NAMESPACES = {
    "rdf":     "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "z":       "http://www.zotero.org/namespaces/export#",
    "dcterms": "http://purl.org/dc/terms/",
    "bib":     "http://purl.org/net/biblio#",
    "foaf":    "http://xmlns.com/foaf/0.1/",
    "link":    "http://purl.org/rss/1.0/modules/link/",
    "dc":      "http://purl.org/dc/elements/1.1/",
    "prism":   "http://prismstandard.org/namespaces/1.2/basic/",
}

def Q(prefix, local):
    return f"{{{NAMESPACES[prefix]}}}{local}"


def normalize_doi(s):
    if not s:
        return ""
    s = s.strip()
    # Strip common prefixes / URLs
    s = re.sub(r"^(https?://(dx\.)?doi\.org/)", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.lower().strip()


def extract_doi_from_text(s):
    if not s:
        return ""
    m = re.search(r"10\.\d{4,9}/[^\s\"<>]+", s)
    return m.group(0).rstrip(".,;)]") if m else ""


def parse_rdf(rdf_path):
    """Yield (item_id, doi, title, pdf_relpath) per article-with-attachment.

    Linkage:
        <bib:Article>
            <link:link rdf:resource="#item_15518"/>
            <bib:identifier> ... DOI ... </bib:identifier>
            ...
        </bib:Article>
        <z:Attachment rdf:about="#item_15518">
            <z:path rdf:resource="files/15518/...pdf"/>
        </z:Attachment>
    """
    tree = ET.parse(rdf_path)
    root = tree.getroot()

    # Build map of item_id -> pdf_relpath from attachments
    attachments = {}
    for att in root.findall(Q("z", "Attachment")):
        about = att.get(Q("rdf", "about"), "")
        if not about.startswith("#item_"):
            continue
        item_id = about[len("#item_"):]
        path_el = att.find(Q("z", "path"))
        if path_el is None:
            continue
        relpath = path_el.get(Q("rdf", "resource"), "")
        if not relpath.lower().endswith(".pdf"):
            continue
        attachments[item_id] = relpath

    # Iterate Articles + similar bibliographic items
    article_tags = [
        Q("bib", "Article"),
        Q("bib", "JournalArticle"),
        Q("bib", "Document"),
        Q("bib", "ConferencePaper"),
        Q("bib", "BookSection"),
        Q("bib", "Book"),
        Q("bib", "Thesis"),
        Q("bib", "Report"),
        Q("bib", "Manuscript"),
        Q("bib", "Memo"),
    ]
    article_tag_set = set(article_tags)

    for child in root:
        if child.tag not in article_tag_set:
            continue

        # Title
        title_el = child.find(Q("dc", "title"))
        title = (title_el.text or "").strip() if title_el is not None else ""

        # Linked item id
        link_el = child.find(Q("link", "link"))
        item_id = ""
        if link_el is not None:
            res = link_el.get(Q("rdf", "resource"), "")
            if res.startswith("#item_"):
                item_id = res[len("#item_"):]

        # DOI: look in bib:identifier with rdf:datatype hints, or any text content
        doi = ""
        # 1) <prism:doi>
        prism_doi = child.find(Q("prism", "doi"))
        if prism_doi is not None and prism_doi.text:
            doi = normalize_doi(prism_doi.text)
        # 2) <dc:identifier> children
        if not doi:
            for ident in child.findall(Q("dc", "identifier")):
                if ident.text and "10." in ident.text:
                    cand = extract_doi_from_text(ident.text)
                    if cand:
                        doi = normalize_doi(cand)
                        break
                # Some encodings nest <dcterms:URI><rdf:value>...
                for sub in ident.iter():
                    if sub.text and "10." in sub.text:
                        cand = extract_doi_from_text(sub.text)
                        if cand:
                            doi = normalize_doi(cand)
                            break
                if doi:
                    break
        # 3) The article's about=URL may itself contain a DOI
        if not doi:
            about = child.get(Q("rdf", "about"), "")
            cand = extract_doi_from_text(about)
            if cand:
                doi = normalize_doi(cand)

        pdf_relpath = attachments.get(item_id, "")
        yield item_id, doi, title, pdf_relpath


def find_rdf_files(root_dir):
    """Recursively find *.rdf under root_dir."""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            if fn.lower().endswith(".rdf"):
                found.append(os.path.join(dirpath, fn))
    return sorted(found)


def load_corpus(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--rdf-root", required=True)
    p.add_argument("--corpus", default="merged_dataset.json")
    p.add_argument("--output", default="rdf_pdf_index.csv")
    p.add_argument("--all-records", action="store_true",
                   help="Include all corpus records, not just title-only")
    args = p.parse_args()

    rdf_root = os.path.abspath(args.rdf_root)
    if not os.path.isdir(rdf_root):
        sys.exit(f"Not a directory: {rdf_root}")

    rdf_files = find_rdf_files(rdf_root)
    if not rdf_files:
        sys.exit(f"No .rdf files found under {rdf_root}")
    print(f"Found {len(rdf_files)} RDF files:", file=sys.stderr)
    for f in rdf_files:
        print(f"  {f}", file=sys.stderr)

    # Build DOI -> entry and title -> entry indexes
    doi_index = {}      # doi -> entry dict
    title_index = {}    # normalized title -> entry dict (fallback for missing DOI)
    no_pdf_entries = []  # entries with DOI but no attached PDF
    n_no_doi = 0

    def norm_title(s):
        return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()

    for rdf_path in rdf_files:
        n_in_file = 0
        try:
            for item_id, doi, title, pdf_relpath in parse_rdf(rdf_path):
                n_in_file += 1
                rdf_dir = os.path.dirname(rdf_path)
                pdf_abs = os.path.join(rdf_dir, pdf_relpath) if pdf_relpath else ""
                if not pdf_relpath:
                    if doi:
                        no_pdf_entries.append({
                            "rdf_file": rdf_path, "item_id": item_id,
                            "doi": doi, "title": title,
                        })
                    continue
                entry = {
                    "rdf_file": rdf_path, "item_id": item_id,
                    "title": title, "pdf_relpath": pdf_relpath,
                    "pdf_abs_path": pdf_abs,
                }
                if doi:
                    doi_index.setdefault(doi, entry)
                else:
                    n_no_doi += 1
                # Always index by title too (fallback when corpus and RDF disagree on DOI normalization)
                nt = norm_title(title)
                if len(nt) >= 30:    # avoid trivial title collisions
                    title_index.setdefault(nt, entry)
        except ET.ParseError as e:
            print(f"  WARN parse error in {rdf_path}: {e}", file=sys.stderr)
        print(f"  Parsed {rdf_path}: {n_in_file} bibliographic items", file=sys.stderr)

    print(f"\nIndex sizes:", file=sys.stderr)
    print(f"  DOI -> entry:                 {len(doi_index)}", file=sys.stderr)
    print(f"  Title -> entry (fallback):    {len(title_index)}", file=sys.stderr)
    print(f"  Entries with no parsable DOI: {n_no_doi}", file=sys.stderr)
    print(f"  Entries with DOI but no PDF:  {len(no_pdf_entries)}", file=sys.stderr)

    # Load corpus, select target set
    corpus = load_corpus(args.corpus)
    if args.all_records:
        targets = corpus
        target_label = "ALL records"
    else:
        targets = [r for r in corpus if not (r.get("abstract") or "").strip()]
        target_label = "TITLE-ONLY records"

    print(f"\nTarget set: {target_label}  (n={len(targets)})\n", file=sys.stderr)

    # Match by DOI, then by title as fallback
    rows = []
    matched_by_doi = 0
    matched_by_title = 0
    pdf_present_count = 0
    pdf_total_bytes = 0
    no_match = 0

    for r in targets:
        rid = r.get("id", "")
        rec_doi = normalize_doi(r.get("DOI", "") or "")
        rec_title = r.get("title", "") or ""
        nt = norm_title(rec_title)

        entry = None
        how = ""
        if rec_doi and rec_doi in doi_index:
            entry = doi_index[rec_doi]
            how = "doi"
            matched_by_doi += 1
        elif len(nt) >= 30 and nt in title_index:
            entry = title_index[nt]
            how = "title"
            matched_by_title += 1
        else:
            no_match += 1
            rows.append({
                "record_id": rid, "doi": rec_doi, "rdf_file": "", "item_id": "",
                "pdf_relpath": "", "pdf_abs_path": "",
                "pdf_exists": False, "pdf_size_bytes": 0,
                "match_status": "no_match", "matched_by": "",
            })
            continue

        exists = os.path.isfile(entry["pdf_abs_path"]) if entry["pdf_abs_path"] else False
        size = os.path.getsize(entry["pdf_abs_path"]) if exists else 0
        if exists:
            pdf_present_count += 1
            pdf_total_bytes += size
        rows.append({
            "record_id": rid, "doi": rec_doi,
            "rdf_file": entry["rdf_file"], "item_id": entry["item_id"],
            "pdf_relpath": entry["pdf_relpath"],
            "pdf_abs_path": entry["pdf_abs_path"],
            "pdf_exists": exists, "pdf_size_bytes": size,
            "match_status": "matched" if exists else "matched_pdf_missing",
            "matched_by": how,
        })

    # Write CSV
    with open(args.output, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "record_id", "doi", "rdf_file", "item_id",
            "pdf_relpath", "pdf_abs_path",
            "pdf_exists", "pdf_size_bytes", "match_status", "matched_by",
        ])
        w.writeheader()
        for row in rows:
            w.writerow(row)

    # Summary
    print("=" * 60)
    print(f"Target set: {target_label}  (n={len(targets)})")
    print("=" * 60)
    print(f"  Matched by DOI:                     {matched_by_doi}")
    print(f"  Matched by title (fallback):        {matched_by_title}")
    print(f"  No match in RDF (DOI or title):     {no_match}")
    print(f"  Matched & PDF present on disk:      {pdf_present_count}")
    print(f"  Matched but PDF missing on disk:    {(matched_by_doi+matched_by_title) - pdf_present_count}")
    print(f"  Total PDF size:                     {pdf_total_bytes / 1e6:.1f} MB")
    statuses = Counter(r["match_status"] for r in rows)
    print(f"\nStatus distribution: {dict(statuses)}")
    print(f"\nIndex written to {args.output}")


if __name__ == "__main__":
    main()
