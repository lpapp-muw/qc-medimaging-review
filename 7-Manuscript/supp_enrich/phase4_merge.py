# -*- coding: utf-8 -*-
# Phase 4.4 merge - collapse enrichment verdicts into an adjudication set.
#
# Reads out/enrich_tasks/*.verdicts.json (+ the sibling *.task.json for each
# field's current extracted value), keeps only the ACTIONABLE verdicts
# (fills_blank, appends_extra, contradicts, figure_only), dedups repeats across a
# paper's chunks, attaches pre-screen flags (numeric range gates, toy-dataset
# controls, unverified figure reads), and writes:
#   adjudication.jsonl / .csv : one row per actionable verdict, with extraction
#                               value, proposed SI value, quote, source, flag.
#   verify_tasks/<sn>.task.json : per paper, its actionable verdicts + the SI
#                               text/image paths, for the verify-verdict pass to
#                               reopen and accept/reject.
# confirms, confirms_absence, not_found are summarized, not expanded (no table
# change). Deterministic, no LLM. Python 3.8 strict.
#
# Usage:
#   python phase4_merge.py --tasks out/enrich_tasks --norm supp_norm --out out
#   python phase4_merge.py --selftest

import sys
import os
import re
import csv
import json
import argparse
from collections import Counter, defaultdict

ACTIONABLE = ("fills_blank", "appends_extra", "contradicts", "figure_only")
RANGE = {"qubit_count": (1, 1000), "circuit_depth": (1, 100000),
         "shot_count": (1, 10 ** 9), "gate_count": (1, 10 ** 7)}
TOY = set(["mnist", "fashion-mnist", "fashionmnist", "fashion_mnist", "cifar-10",
           "cifar10", "cifar-100", "cifar100", "kmnist", "emnist", "qmnist",
           "digits", "usps"])


def norm(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, sort_keys=True, ensure_ascii=False)
    return str(v).strip()


def first_int(s):
    m = re.search(r"\d[\d,]*", norm(s))
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def flag_of(field, verdict, value, figure_only):
    if figure_only:
        return "review:figure_only_unverified"
    if verdict == "fills_blank" and field in RANGE:
        n = first_int(value)
        lo, hi = RANGE[field]
        if n is None:
            return "review:unparseable_number"
        if n < lo or n > hi:
            return "hold:out_of_range({} not in {}-{})".format(n, lo, hi)
    if field in ("dataset_name", "dataset_type") and norm(value).lower() in TOY:
        return "hold:suspected_control_dataset"
    if verdict == "contradicts":
        return "review:extraction_vs_si_disagreement"
    return "review"


def load_targets_map(task_path):
    """field -> current_value from a chunk task file (targets identical per paper)."""
    m = {}
    try:
        t = json.load(open(task_path))
    except Exception:
        return m
    for tg in t.get("targets", []):
        m[tg.get("field")] = tg.get("current_value", "")
    return m


def si_refs(norm_dir, sn):
    """Return (text_files, image_files) absolute-ish paths for a paper's SI."""
    idx_path = os.path.join(norm_dir, sn, "index.json")
    tfiles, ifiles = [], []
    if os.path.exists(idx_path):
        idx = json.load(open(idx_path))
        for e in idx.get("entries", []):
            if e.get("text_file"):
                tfiles.append(os.path.join(norm_dir, sn, "text", e["text_file"]))
            for im in e.get("image_files", []):
                ifiles.append(os.path.join(norm_dir, sn, "images", im))
    return tfiles, ifiles


def run(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    vt_dir = os.path.join(a.out, "verify_tasks")
    if not os.path.isdir(vt_dir):
        os.makedirs(vt_dir)

    reply_files = [f for f in os.listdir(a.tasks) if f.endswith(".verdicts.json")]
    tgt_cache = {}
    # dedup key -> record; occurrences counted
    records = {}
    summary = Counter()
    per_paper_all = Counter()

    for rf in sorted(reply_files):
        sn = rf.split("__")[0]
        try:
            rep = json.load(open(os.path.join(a.tasks, rf)))
        except Exception:
            summary["bad_json_reply"] += 1
            continue
        task_path = os.path.join(a.tasks, rf.replace(".verdicts.json", ".task.json"))
        if sn not in tgt_cache:
            tgt_cache[sn] = load_targets_map(task_path)
        tmap = tgt_cache[sn]
        for v in rep.get("verdicts", []):
            vt = v.get("verdict")
            per_paper_all[vt] += 1
            summary[vt] += 1
            actionable = vt in ("fills_blank", "appends_extra", "contradicts") or v.get("_figure_only")
            vkind = "figure_only" if (v.get("_figure_only") and vt not in ACTIONABLE[:3]) else vt
            if not actionable:
                continue
            field = v.get("field")
            val = v.get("value")
            key = (sn, field, vt, norm(val), bool(v.get("_figure_only")))
            if key in records:
                records[key]["occurrences"] += 1
                continue
            records[key] = {
                "stable_name": sn, "doi": rep.get("doi", ""),
                "field": field, "verdict": vt,
                "figure_only": bool(v.get("_figure_only")),
                "extraction_value": tmap.get(field, ""),
                "si_value": val,
                "quote": v.get("quote"), "supp_source": v.get("supp_source"),
                "note": v.get("note", ""),
                "flag": flag_of(field, vt, val, bool(v.get("_figure_only"))),
                "occurrences": 1,
            }

    recs = list(records.values())
    # adjudication.jsonl
    adj_jsonl = os.path.join(a.out, "adjudication.jsonl")
    with open(adj_jsonl, "w") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    # adjudication.csv (human view)
    adj_csv = os.path.join(a.out, "adjudication.csv")
    cols = ["stable_name", "field", "verdict", "figure_only", "flag",
            "extraction_value", "si_value", "quote", "supp_source", "occurrences", "note"]
    with open(adj_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(recs, key=lambda x: (x["flag"], x["stable_name"], x["field"])):
            row = dict(r)
            row["si_value"] = norm(row["si_value"])[:200]
            row["extraction_value"] = norm(row["extraction_value"])[:200]
            row["quote"] = norm(row["quote"])[:200]
            w.writerow({k: row.get(k, "") for k in cols})
    # per-paper verify tasks
    by_paper = defaultdict(list)
    for r in recs:
        by_paper[r["stable_name"]].append(r)
    for sn, rs in by_paper.items():
        tfiles, ifiles = si_refs(a.norm, sn)
        task = {"stable_name": sn, "doi": rs[0]["doi"],
                "records": [{"field": r["field"], "verdict": r["verdict"],
                             "figure_only": r["figure_only"],
                             "extraction_value": r["extraction_value"],
                             "si_value": r["si_value"], "quote": r["quote"],
                             "supp_source": r["supp_source"], "flag": r["flag"]}
                            for r in rs],
                "si_text_files": tfiles, "si_image_paths": ifiles,
                "reply_path": os.path.join(vt_dir, "{}.verify.json".format(sn))}
        json.dump(task, open(os.path.join(vt_dir, "{}.task.json".format(sn)), "w"), ensure_ascii=False)

    print("actionable verdicts (deduped): {}".format(len(recs)))
    print("by verdict: {}".format(dict(Counter(r["verdict"] for r in recs))))
    print("by flag: {}".format(dict(Counter(r["flag"].split("(")[0] for r in recs))))
    print("papers with actionable verdicts: {}".format(len(by_paper)))
    print("full verdict tally (all reply files): {}".format(dict(summary)))
    print("wrote: {} | {} | {}/*.task.json".format(adj_jsonl, adj_csv, vt_dir))


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    tasks = os.path.join(d, "enrich_tasks")
    norm_d = os.path.join(d, "supp_norm")
    os.makedirs(tasks)
    os.makedirs(os.path.join(norm_d, "p1", "text"))
    os.makedirs(os.path.join(norm_d, "p1", "images"))
    open(os.path.join(norm_d, "p1", "text", "si.txt"), "w").write("n_qubits shown")
    open(os.path.join(norm_d, "p1", "images", "Fig1.pdf_p1.png"), "wb").write(b"x")
    json.dump({"stable_name": "p1", "doi": "10/x", "entries": [
        {"source": "si.pdf", "keep_reason": "si_document", "text_file": "si.txt", "image_files": ["Fig1.pdf_p1.png"]}]},
        open(os.path.join(norm_d, "p1", "index.json"), "w"))
    targets = [
        {"field": "qubit_count", "current_value": ""},
        {"field": "circuit_depth", "current_value": "4"},
        {"field": "dataset_name", "current_value": "OASIS-2"},
        {"field": "code_release", "current_value": "FALSE"},
    ]
    # chunk 0: a valid fill, an out-of-range fill, a toy append, a contradict, a figure_only
    v0 = {"stable_name": "p1", "doi": "10/x", "verdicts": [
        {"field": "shot_count", "verdict": "fills_blank", "value": "2048 shots", "quote": "2048 shots", "supp_source": "config.yaml"},
        {"field": "qubit_count", "verdict": "fills_blank", "value": "10,000", "quote": "10000", "supp_source": "nb"},
        {"field": "dataset_name", "verdict": "appends_extra", "value": "MNIST", "quote": "MNIST", "supp_source": "nb"},
        {"field": "code_release", "verdict": "contradicts", "value": True, "quote": "github.com/x", "supp_source": "README", "note": "extraction=false, repo present"},
        {"field": "circuit_depth", "verdict": "fills_blank", "value": "depth 5", "quote": None, "supp_source": "Fig1 p1", "_figure_only": True},
        {"field": "x", "verdict": "confirms", "value": "y", "quote": "y", "supp_source": "z"},
        {"field": "x2", "verdict": "not_found", "value": None, "quote": None, "supp_source": None},
    ]}
    # chunk 1: duplicate MNIST append (must dedup)
    v1 = {"stable_name": "p1", "doi": "10/x", "verdicts": [
        {"field": "dataset_name", "verdict": "appends_extra", "value": "MNIST", "quote": "MNIST", "supp_source": "nb2"}]}
    for ci, v in ((0, v0), (1, v1)):
        json.dump({"stable_name": "p1", "doi": "10/x", "targets": targets},
                  open(os.path.join(tasks, "p1__c{}.task.json".format(ci)), "w"))
        json.dump(v, open(os.path.join(tasks, "p1__c{}.verdicts.json".format(ci)), "w"))

    class A:
        pass
    A.tasks = tasks
    A.norm = norm_d
    A.out = os.path.join(d, "out")
    run(A)
    recs = [json.loads(l) for l in open(os.path.join(A.out, "adjudication.jsonl"))]
    by = {(r["field"], r["verdict"]): r for r in recs}
    # 5 actionable, deduped (MNIST once with occurrences=2)
    assert len(recs) == 5, ("expected 5 deduped actionable", len(recs))
    mnist = by[("dataset_name", "appends_extra")]
    assert mnist["occurrences"] == 2, ("MNIST dedup count", mnist["occurrences"])
    assert mnist["flag"] == "hold:suspected_control_dataset", mnist["flag"]
    assert by[("qubit_count", "fills_blank")]["flag"].startswith("hold:out_of_range"), by[("qubit_count", "fills_blank")]["flag"]
    assert by[("shot_count", "fills_blank")]["flag"] == "review", by[("shot_count", "fills_blank")]["flag"]
    assert by[("circuit_depth", "fills_blank")]["flag"] == "review:figure_only_unverified"
    assert by[("code_release", "contradicts")]["extraction_value"] == "FALSE", "extraction value joined"
    # verify task exists with SI refs
    vt = json.load(open(os.path.join(A.out, "verify_tasks", "p1.task.json")))
    assert len(vt["records"]) == 5 and vt["si_text_files"] and vt["si_image_paths"], "verify task built with SI refs"
    print("selftest OK: dedup, range/toy/figure flags, extraction-value join, verify tasks")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks")
    ap.add_argument("--norm")
    ap.add_argument("--out", default="out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.tasks or not a.norm:
        ap.error("--tasks and --norm are required")
    run(a)


if __name__ == "__main__":
    main()
