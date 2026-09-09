# -*- coding: utf-8 -*-
# Phase 4.1 - enrichment relevance filter (deterministic, no LLM).
#
# Reads corpus_inventory.jsonl (from triage_corpus.py) and reduces each paper's
# files to the enrichment-eligible set: rendered SI documents, author notebooks,
# metrics-log tables, config/hyperparameter files, and READMEs. Drops the noise
# that inflated the raw counts: array dumps (.pkl/.npy/.mat/.h5), dataset/split
# CSVs, public-benchmark data, app manifests, images, redundant full-text PDFs,
# and bibliographic XML.
#
# Fixes the classifier gap where an author-supplied _chrome_si/*.ipynb was tagged
# `code` and dropped: notebooks are enrichment-eligible regardless of source.
#
# Output: enrich_inputs.jsonl (per paper, the files a subagent should read) +
# filter_report.csv (per-paper kept counts) + a global drop tally.
#
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python phase4_filter.py --inventory out/corpus_inventory.jsonl --out out [--included ../../5-Adjudication/included_step5_v134.csv]
#   python phase4_filter.py --selftest

import sys
import os
import re
import csv
import json
import argparse
from collections import Counter, defaultdict

SI_SOURCES = ("chrome_si", "si", "deposit")

ARRAY_EXT = set([".pkl", ".npy", ".npz", ".mat", ".h5", ".hdf5", ".dat",
                 ".parquet", ".sav", ".pt", ".pth", ".ckpt", ".bin", ".arff"])
CONFIG_EXT = set([".yaml", ".yml", ".cfg", ".ini", ".toml"])
CONFIG_NAME = re.compile(
    r"(config|params|hparams|hyperparam|settings|^args\b|options|opts|"
    r"train_config|model_config|experiment)", re.I)
METRICS_NAME = re.compile(
    r"(training_?log|train_?log|val_?log|log_?history|history|metrics?|"
    r"results?|scores?|performance|summary|eval(uation)?|per[_-]?fold|"
    r"accuracy|dice|auc|iou|confusion)", re.I)
DATASET_NAME = re.compile(
    r"(dataset|train[_-]|val[_-]|valid[_-]|test[_-]|split|gene[_-]?(list|ids)|"
    r"features|cohort|patients?|samples?|_data\b|benchmark|manifest)", re.I)
MANIFEST_NAME = re.compile(r"manifest", re.I)
README_NAME = re.compile(r"^readme", re.I)


def base(p):
    return p.replace("\\", "/").split("/")[-1]


def ext_of(name):
    low = name.lower()
    if low.endswith(".tar.gz"):
        return ".tar.gz"
    return os.path.splitext(low)[1]


def decide(f):
    """Return (keep_bool, reason). reason names the keep-category or drop-cause."""
    name = base(f["rel_path"])
    e = ext_of(name)
    typ = f.get("type", "")
    src = f.get("source", "")

    # KEEP, in priority order
    if e == ".ipynb":
        return True, "notebook"
    if typ in ("pdf", "document") and src in SI_SOURCES:
        return True, "si_document"
    if README_NAME.search(name):
        return True, "readme"
    if e in CONFIG_EXT or (CONFIG_NAME.search(name) and e in (".json", ".py", ".txt", "")):
        # a dataset-ish json named like data is not config
        if not DATASET_NAME.search(name) or CONFIG_NAME.search(name):
            return True, "config"
    if typ == "spreadsheet" and METRICS_NAME.search(name) and not (
            DATASET_NAME.search(name) and not METRICS_NAME.search(name)):
        return True, "metrics_table"

    # DROP, with cause
    if e in ARRAY_EXT:
        return False, "array_dump"
    if MANIFEST_NAME.search(name):
        return False, "manifest"
    if typ == "spreadsheet":
        return False, "dataset_table"
    if typ == "pdf" and src == "oa":
        return False, "full_text_redundant"
    if typ == "data" and src == "oa":
        return False, "metadata"
    if typ == "image":
        return False, "image"
    if typ == "code":
        return False, "bulk_code"
    if typ == "data":
        return False, "data_other"
    if typ == "archive":
        return False, "archive_container"
    return False, "other"


def load_doi_map(path):
    m = {}
    if path and os.path.exists(path):
        for row in csv.DictReader(open(path)):
            sn = (row.get("stable_name") or "").strip()
            if sn:
                m[sn] = (row.get("doi") or "").strip().lower()
    return m


def run(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    doi_map = load_doi_map(a.included)
    out_path = os.path.join(a.out, "enrich_inputs.jsonl")
    rep_path = os.path.join(a.out, "filter_report.csv")

    kept_by_cat = Counter()
    dropped_by_cause = Counter()
    papers_with_input = 0
    report_rows = []

    with open(out_path, "w") as out:
        for line in open(a.inventory):
            if not line.strip():
                continue
            rec = json.loads(line)
            sn = rec["stable_name"]
            inputs = []
            per_cat = Counter()
            for f in rec.get("files", []):
                keep, reason = decide(f)
                if keep:
                    inputs.append({"rel_path": f["rel_path"], "type": f["type"],
                                   "source": f["source"], "keep_reason": reason,
                                   "size": f.get("size", 0)})
                    kept_by_cat[reason] += 1
                    per_cat[reason] += 1
                else:
                    dropped_by_cause[reason] += 1
            if inputs:
                papers_with_input += 1
                out.write(json.dumps({
                    "stable_name": sn, "doi": doi_map.get(sn, ""),
                    "n_inputs": len(inputs), "inputs": inputs}, ensure_ascii=False) + "\n")
            report_rows.append({
                "stable_name": sn, "doi": doi_map.get(sn, ""),
                "n_inputs": len(inputs),
                "si_document": per_cat.get("si_document", 0),
                "notebook": per_cat.get("notebook", 0),
                "metrics_table": per_cat.get("metrics_table", 0),
                "config": per_cat.get("config", 0),
                "readme": per_cat.get("readme", 0),
            })

    with open(rep_path, "w", newline="") as fh:
        cols = ["stable_name", "doi", "n_inputs", "si_document", "notebook",
                "metrics_table", "config", "readme"]
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(report_rows, key=lambda x: -x["n_inputs"]):
            w.writerow(r)

    print("papers with >=1 enrichment input: {}".format(papers_with_input))
    print("kept files by category: {}".format(dict(kept_by_cat)))
    print("dropped files by cause: {}".format(dict(dropped_by_cause)))
    tot_keep = sum(kept_by_cat.values())
    tot_drop = sum(dropped_by_cause.values())
    print("total kept: {} | total dropped: {} | keep rate: {:.1f}%".format(
        tot_keep, tot_drop, 100.0 * tot_keep / max(1, tot_keep + tot_drop)))
    print("wrote: {} | {}".format(out_path, rep_path))


def selftest():
    import tempfile
    inv = [
        {"stable_name": "p1", "files": [
            {"rel_path": "_chrome_si/supp.pdf", "type": "pdf", "source": "chrome_si", "size": 10},
            {"rel_path": "_chrome_si/mmc1.docx", "type": "document", "source": "chrome_si", "size": 10},
            {"rel_path": "_chrome_si/Alz_SA.ipynb", "type": "code", "source": "chrome_si", "size": 10},
            {"rel_path": "oa/article.pdf", "type": "pdf", "source": "oa", "size": 10},
            {"rel_path": "deposits/d.zip._unpacked/results_perfold.csv", "type": "spreadsheet", "source": "deposit", "size": 5},
            {"rel_path": "deposits/d.zip._unpacked/val_control.csv", "type": "spreadsheet", "source": "deposit", "size": 5},
            {"rel_path": "deposits/repo/config.yaml", "type": "code", "source": "deposit", "size": 5},
            {"rel_path": "deposits/repo/train.py", "type": "code", "source": "deposit", "size": 5},
            {"rel_path": "deposits/repo/README.md", "type": "other", "source": "deposit", "size": 5},
            {"rel_path": "deposits/repo/data/x.pkl", "type": "data", "source": "deposit", "size": 5},
            {"rel_path": "deposits/repo/kits.json", "type": "data", "source": "deposit", "size": 5},
        ]},
    ]
    d = tempfile.mkdtemp()
    ip = os.path.join(d, "inv.jsonl")
    with open(ip, "w") as fh:
        for r in inv:
            fh.write(json.dumps(r) + "\n")

    class A:
        inventory = ip
        out = os.path.join(d, "out")
        included = None
    run(A)
    got = {}
    for line in open(os.path.join(A.out, "enrich_inputs.jsonl")):
        rec = json.loads(line)
        for f in rec["inputs"]:
            got[base(f["rel_path"])] = f["keep_reason"]
    assert got.get("supp.pdf") == "si_document", got
    assert got.get("mmc1.docx") == "si_document", got
    assert got.get("Alz_SA.ipynb") == "notebook", got            # the fix
    assert "article.pdf" not in got, "oa full-text must be dropped"
    assert got.get("results_perfold.csv") == "metrics_table", got
    assert "val_control.csv" not in got, "dataset split must be dropped"
    assert got.get("config.yaml") == "config", got
    assert "train.py" not in got, "bulk code must be dropped"
    assert got.get("README.md") == "readme", got
    assert "x.pkl" not in got, "array dump must be dropped"
    assert "kits.json" not in got, "dataset json must be dropped"
    print("selftest OK: keep/drop rules + notebook fix verified")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory")
    ap.add_argument("--out", default="out")
    ap.add_argument("--included", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.inventory:
        ap.error("--inventory is required")
    run(a)


if __name__ == "__main__":
    main()
