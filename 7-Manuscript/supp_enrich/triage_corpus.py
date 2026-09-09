# -*- coding: utf-8 -*-
# Phase 3 - corpus triage / normalize.
#
# Walks supp_corpus/<stable_name>/** for every included paper, unpacks archives
# (zip / tar.gz / tgz, recursively, depth-limited), classifies every file by type
# and by source stage (deposits / oa / si / _chrome_si), and tags enrichment
# relevance. Output: per-paper inventory + a coverage report that separates the
# enrichment-grade SI (spreadsheets, docs, SI PDFs, data files from si/chrome/
# deposit) from redundant full-text article PDFs and bibliographic XML.
#
# Deterministic, no LLM. Resumable: skips archives already unpacked.
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python triage_corpus.py --corpus supp_corpus --included ../../5-Adjudication/included_step5_v134.csv --out out
#   python triage_corpus.py --selftest

import sys
import os
import csv
import json
import zipfile
import tarfile
import argparse
from collections import Counter, defaultdict

EXT = {
    "pdf": [".pdf"],
    "spreadsheet": [".xlsx", ".xls", ".csv", ".tsv", ".ods"],
    "document": [".docx", ".doc", ".rtf", ".odt", ".tex"],
    "data": [".json", ".xml", ".mat", ".npy", ".npz", ".h5", ".hdf5", ".nii",
             ".pkl", ".parquet", ".dat", ".sav", ".arff"],
    "code": [".py", ".ipynb", ".m", ".r", ".cpp", ".cc", ".c", ".h", ".hpp",
             ".java", ".js", ".sh", ".yaml", ".yml", ".cfg", ".ini", ".toml"],
    "image": [".png", ".jpg", ".jpeg", ".gif", ".tif", ".tiff", ".svg", ".eps", ".bmp"],
    "archive": [".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"],
}
EXT_LOOKUP = {}
for k, v in EXT.items():
    for e in v:
        EXT_LOOKUP[e] = k

ARCHIVE_EXT = (".zip", ".tar.gz", ".tgz", ".tar")
SI_SOURCES = ("chrome_si", "si", "deposit")
SI_TYPES = ("pdf", "spreadsheet", "document", "data")


def ftype(name):
    low = name.lower()
    if low.endswith(".tar.gz"):
        return "archive"
    _, e = os.path.splitext(low)
    return EXT_LOOKUP.get(e, "other")


def source_of(rel_path):
    parts = rel_path.replace("\\", "/").lower().split("/")
    if "_chrome_si" in parts:
        return "chrome_si"
    if "si" in parts:
        return "si"
    if "deposits" in parts:
        return "deposit"
    if "oa" in parts:
        return "oa"
    return "other"


def relevance(source, typ):
    if source in SI_SOURCES and typ in SI_TYPES:
        return "si_enrichment"
    if typ == "code":
        return "code"
    if source == "oa" and typ == "pdf":
        return "full_text_redundant"
    if source == "oa" and typ == "data":
        return "metadata"
    if typ in ("image", "archive"):
        return typ
    return "other"


def is_archive(name):
    low = name.lower()
    return low.endswith(ARCHIVE_EXT)


def unpack_all(paper_dir, max_depth=2):
    """Unpack archives under paper_dir into sibling _unpacked/<name>/ dirs.
    Repeats up to max_depth to catch archives-in-archives. Resumable."""
    for depth in range(max_depth):
        found = False
        for root, dirs, files in os.walk(paper_dir):
            if os.path.basename(root) == "_unpacked_marker":
                continue
            for f in files:
                if not is_archive(f):
                    continue
                src = os.path.join(root, f)
                dest = src + "._unpacked"
                if os.path.isdir(dest):
                    continue
                try:
                    os.makedirs(dest)
                    low = f.lower()
                    if low.endswith(".zip"):
                        with zipfile.ZipFile(src) as z:
                            z.extractall(dest)
                    elif low.endswith((".tar.gz", ".tgz", ".tar")):
                        with tarfile.open(src) as t:
                            _safe_extract(t, dest)
                    found = True
                except Exception as e:
                    with open(dest + ".ERROR", "w") as fh:
                        fh.write("{}: {}".format(type(e).__name__, e))
        if not found:
            break


def _safe_extract(tar, dest):
    base = os.path.abspath(dest)
    for m in tar.getmembers():
        target = os.path.abspath(os.path.join(dest, m.name))
        if not target.startswith(base + os.sep) and target != base:
            continue  # skip path traversal
        tar.extract(m, dest)


def load_included(path):
    out = []
    with open(path, "r") as fh:
        for row in csv.DictReader(fh):
            sn = (row.get("stable_name") or "").strip()
            if sn:
                out.append(sn)
    return out


def inventory_paper(paper_dir):
    files = []
    for root, dirs, fnames in os.walk(paper_dir):
        for f in fnames:
            if f.endswith((".ERROR",)) or f.startswith("._done_"):
                continue
            full = os.path.join(root, f)
            rel = os.path.relpath(full, paper_dir)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            typ = ftype(f)
            src = source_of(rel)
            files.append({"rel_path": rel, "type": typ, "source": src,
                          "relevance": relevance(src, typ), "size": size})
    return files


def run(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    included = load_included(a.included)
    inv_path = os.path.join(a.out, "corpus_inventory.jsonl")
    cov_rows = []
    summary = Counter()
    papers_with_si = []
    papers_with_code = []
    type_by_relevance = defaultdict(Counter)

    with open(inv_path, "w") as inv:
        for sn in included:
            pdir = os.path.join(a.corpus, sn)
            if not os.path.isdir(pdir):
                cov_rows.append({"stable_name": sn, "status": "no_corpus_dir"})
                continue
            unpack_all(pdir)
            files = inventory_paper(pdir)
            rc = Counter(x["relevance"] for x in files)
            tc = Counter("{}:{}".format(x["source"], x["type"]) for x in files)
            for x in files:
                type_by_relevance[x["relevance"]][x["type"]] += 1
            has_si = rc.get("si_enrichment", 0) > 0
            has_code = rc.get("code", 0) > 0
            if has_si:
                papers_with_si.append(sn)
            if has_code:
                papers_with_code.append(sn)
            inv.write(json.dumps({"stable_name": sn, "files": files}, ensure_ascii=False) + "\n")
            cov_rows.append({
                "stable_name": sn, "status": "ok",
                "si_enrichment_files": rc.get("si_enrichment", 0),
                "si_pdf": sum(1 for x in files if x["relevance"] == "si_enrichment" and x["type"] == "pdf"),
                "si_spreadsheet": sum(1 for x in files if x["relevance"] == "si_enrichment" and x["type"] == "spreadsheet"),
                "si_document": sum(1 for x in files if x["relevance"] == "si_enrichment" and x["type"] == "document"),
                "si_data": sum(1 for x in files if x["relevance"] == "si_enrichment" and x["type"] == "data"),
                "code_files": rc.get("code", 0),
                "full_text_pdf": rc.get("full_text_redundant", 0),
                "total_files": len(files),
            })
            summary["papers_scanned"] += 1

    cov_path = os.path.join(a.out, "corpus_coverage.csv")
    fields = ["stable_name", "status", "si_enrichment_files", "si_pdf",
              "si_spreadsheet", "si_document", "si_data", "code_files",
              "full_text_pdf", "total_files"]
    with open(cov_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in cov_rows:
            w.writerow({k: r.get(k, "") for k in fields})

    print("papers scanned: {}".format(summary["papers_scanned"]))
    print("papers with enrichment-grade SI (spreadsheet/doc/SI-pdf/data): {}".format(len(papers_with_si)))
    print("papers with code (repos): {}".format(len(papers_with_code)))
    print("\nenrichment-grade SI files by type:")
    for t, c in type_by_relevance["si_enrichment"].most_common():
        print("  {:5d}  {}".format(c, t))
    print("\nredundant full-text PDFs: {}".format(sum(type_by_relevance["full_text_redundant"].values())))
    print("wrote: {} | {}".format(inv_path, cov_path))
    print("\nPHASE-4 INPUT = the {} papers with enrichment-grade SI; "
          "their SI files are listed per paper in corpus_inventory.jsonl.".format(len(papers_with_si)))


def selftest():
    import tempfile, shutil
    d = tempfile.mkdtemp()
    try:
        sn = "10_test_paper_1"
        pd = os.path.join(d, "corpus", sn)
        os.makedirs(os.path.join(pd, "oa"))
        os.makedirs(os.path.join(pd, "_chrome_si"))
        os.makedirs(os.path.join(pd, "deposits"))
        # an oa full-text pdf (redundant)
        open(os.path.join(pd, "oa", "article.pdf"), "wb").write(b"%PDF-1.7 main")
        # a chrome SI spreadsheet (enrichment)
        open(os.path.join(pd, "_chrome_si", "supp_tables.xlsx"), "wb").write(b"PK\x03\x04xlsx")
        # a deposit zip containing an SI pdf + code
        z = os.path.join(pd, "deposits", "data.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("results_perfold.csv", "fold,acc\n1,0.9\n")
            zf.writestr("train.py", "print(1)\n")
        # included csv
        inc = os.path.join(d, "inc.csv")
        with open(inc, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["doi", "stable_name"])
            w.writerow(["10/test.1", sn])
        out = os.path.join(d, "out")

        class A:
            corpus = os.path.join(d, "corpus")
            included = inc
        A.out = out
        run(A)
        inv = [json.loads(l) for l in open(os.path.join(out, "corpus_inventory.jsonl"))]
        files = inv[0]["files"]
        rels = {x["rel_path"].split(os.sep)[-1]: x["relevance"] for x in files}
        # the zip was unpacked: results_perfold.csv present and tagged si_enrichment
        assert any(f.endswith("results_perfold.csv") for f in rels), rels
        csvf = [v for k, v in rels.items() if k == "results_perfold.csv"][0]
        assert csvf == "si_enrichment", csvf
        assert rels.get("supp_tables.xlsx") == "si_enrichment", rels
        assert rels.get("article.pdf") == "full_text_redundant", rels
        assert rels.get("train.py") == "code", rels
        print("selftest OK: unpack + classify + relevance tagging")
    finally:
        shutil.rmtree(d)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus")
    ap.add_argument("--included")
    ap.add_argument("--out", default="out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.corpus or not a.included:
        ap.error("--corpus and --included are required")
    run(a)


if __name__ == "__main__":
    main()
