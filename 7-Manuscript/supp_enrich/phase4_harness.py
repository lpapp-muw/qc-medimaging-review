# -*- coding: utf-8 -*-
# Phase 4.3 harness - no-loss chunker for the enrich-from-supp pass.
#
# Per paper: dedup byte-identical files (identical is not information), then pack
# the normalized supplement into bounded chunks so nothing is dropped:
#   - SI documents first (text + rasterized page images), then metrics tables,
#     configs, READMEs, then notebooks.
#   - Each chunk holds <= img_chunk images AND <= text_chunk characters.
#   - A single source larger than a chunk is split across chunks (text sliced,
#     images sliced); every character and every image lands in exactly one chunk.
#   - Every chunk carries the paper's FULL target-cell list.
# A downstream merge (phase4_merge.py) recombines per-chunk verdicts, so chunking
# causes no information loss.
#
# --papers runs a subset first (e.g. the 67-notebook paper alone); --exclude runs
# the rest afterward. status reports pending chunks for the orchestrator gate.
#
# Deterministic, no LLM. Python 3.8 strict: .format() only, no f-strings.
#
# Usage:
#   python phase4_harness.py build --norm supp_norm --targets out/targets.jsonl --out out/enrich_tasks \
#       [--papers 10_1371_journal_pone_0331870] [--exclude a,b] [--img-chunk 18 --text-chunk 120000]
#   python phase4_harness.py status --tasks out/enrich_tasks [--list 10]
#   python phase4_harness.py --selftest

import sys
import os
import re
import csv
import json
import hashlib
import argparse

PRIORITY = {"si_document": 0, "metrics_table": 1, "config": 2, "readme": 3, "notebook": 4}


def sha1_file(path):
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for b in iter(lambda: fh.read(65536), b""):
                h.update(b)
    except OSError:
        return None
    return h.hexdigest()


def load_targets(path):
    """Group targets.jsonl by stable_name -> [ {field, arity, state, current_value, allowed_verdicts} ]."""
    by = {}
    for line in open(path):
        if not line.strip():
            continue
        r = json.loads(line)
        by.setdefault(r["stable_name"], []).append({
            "field": r["field"], "arity": r["arity"], "state": r["state"],
            "current_value": r.get("current_value", ""),
            "allowed_verdicts": r.get("allowed_verdicts", []),
        })
    return by


def build_units(norm_dir, sn):
    """Return deduped units for a paper: list of {source, keep_reason, text, text_len, images}."""
    pdir = os.path.join(norm_dir, sn)
    idx_path = os.path.join(pdir, "index.json")
    if not os.path.exists(idx_path):
        return []
    idx = json.load(open(idx_path))
    entries = sorted(idx.get("entries", []),
                     key=lambda e: PRIORITY.get(e.get("keep_reason"), 9))
    seen_text = set()
    seen_img = set()
    units = []
    for e in entries:
        src = os.path.basename(e.get("source", ""))
        text = ""
        tlen = 0
        tf = e.get("text_file")
        if tf:
            tp = os.path.join(pdir, "text", tf)
            th = sha1_file(tp)
            if th and th not in seen_text:
                seen_text.add(th)
                try:
                    text = open(tp, "r", errors="replace").read()
                except OSError:
                    text = ""
                tlen = len(text)
        imgs = []
        for im in e.get("image_files", []):
            ip = os.path.join(pdir, "images", im)
            ih = sha1_file(ip)
            if ih and ih not in seen_img:
                seen_img.add(ih)
                imgs.append(ip)
        if text or imgs:
            units.append({"source": src, "keep_reason": e.get("keep_reason"),
                          "text": text, "text_len": tlen, "images": imgs})
    return units


def pack(units, img_chunk, text_chunk, overlap=2000):
    """Pack units into chunks bounded by img_chunk images and text_chunk chars.
    Oversize single units are split with `overlap` so no value straddles a
    boundary unseen. Returns list of {text_parts, images}."""
    chunks = []
    cur = {"text_parts": [], "text_len": 0, "images": []}

    def flush():
        if cur["text_parts"] or cur["images"]:
            chunks.append({"text_parts": cur["text_parts"][:], "images": cur["images"][:]})
        cur["text_parts"] = []
        cur["text_len"] = 0
        cur["images"] = []

    for u in units:
        header = "===== FILE: {} =====\n".format(u["source"])
        body = header + (u["text"] or "")
        n_img = len(u["images"])
        oversize = (len(body) > text_chunk) or (n_img > img_chunk)
        if oversize:
            flush()
            # split text into slices
            step = max(1, text_chunk - min(overlap, text_chunk // 2))
            tslices = [body[i:i + text_chunk] for i in range(0, max(len(body), 1), step)] or [body]
            islices = [u["images"][i:i + img_chunk] for i in range(0, n_img, img_chunk)] or [[]]
            m = max(len(tslices), len(islices))
            for k in range(m):
                tp = [tslices[k]] if k < len(tslices) else []
                im = islices[k] if k < len(islices) else []
                chunks.append({"text_parts": tp, "images": im})
            continue
        if (cur["text_len"] + len(body) > text_chunk) or (len(cur["images"]) + n_img > img_chunk):
            flush()
        cur["text_parts"].append(body)
        cur["text_len"] += len(body)
        cur["images"].extend(u["images"])
    flush()
    return chunks


def cmd_build(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    tgt = load_targets(a.targets)
    only = set(s.strip() for s in a.papers.split(",")) if a.papers else None
    excl = set(s.strip() for s in a.exclude.split(",")) if a.exclude else set()
    papers = [d for d in sorted(os.listdir(a.norm))
              if os.path.isdir(os.path.join(a.norm, d)) and os.path.exists(os.path.join(a.norm, d, "index.json"))]
    if only:
        papers = [p for p in papers if p in only]
    papers = [p for p in papers if p not in excl]

    n_tasks = 0
    rep = []
    for sn in papers:
        units = build_units(a.norm, sn)
        if not units:
            rep.append({"stable_name": sn, "chunks": 0, "images": 0, "note": "no_inputs"})
            continue
        chunks = pack(units, a.img_chunk, a.text_chunk, getattr(a, "overlap", 2000))
        targets = tgt.get(sn, [])
        doi = ""
        idx = json.load(open(os.path.join(a.norm, sn, "index.json")))
        doi = idx.get("doi", "")
        for ci, ch in enumerate(chunks):
            task = {
                "stable_name": sn, "doi": doi, "chunk_id": ci, "n_chunks": len(chunks),
                "targets": targets,
                "supplement_text": "\n\n".join(ch["text_parts"]),
                "image_paths": ch["images"],
                "reply_path": os.path.join(a.out, "{}__c{}.verdicts.json".format(sn, ci)),
            }
            tp = os.path.join(a.out, "{}__c{}.task.json".format(sn, ci))
            with open(tp, "w") as fh:
                json.dump(task, fh, ensure_ascii=False)
            n_tasks += 1
        rep.append({"stable_name": sn, "chunks": len(chunks),
                    "images": sum(len(c["images"]) for c in chunks),
                    "note": ""})
    with open(os.path.join(a.out, "harness_report.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stable_name", "chunks", "images", "note"])
        w.writeheader()
        for r in rep:
            w.writerow(r)
    print("papers: {} | task chunks written: {}".format(len(papers), n_tasks))
    print("per paper (chunks/images):")
    for r in sorted(rep, key=lambda x: -x["chunks"]):
        print("  {:3d} chunks {:4d} imgs  {} {}".format(r["chunks"], r["images"], r["stable_name"], r["note"]))
    print("wrote tasks + harness_report.csv under {}/".format(a.out))


def cmd_status(a):
    tasks = [f for f in os.listdir(a.tasks) if f.endswith(".task.json")]
    pending = []
    done = 0
    for tf in sorted(tasks):
        man = json.load(open(os.path.join(a.tasks, tf)))
        rp = man.get("reply_path")
        if rp and os.path.exists(rp):
            done += 1
        else:
            pending.append(tf)
    print("total: {} | done: {} | pending: {}".format(len(tasks), done, len(pending)))
    if getattr(a, "list", 0) and pending:
        for tf in pending[:a.list]:
            print("  " + os.path.join(a.tasks, tf))


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    sn = "p1"
    nd = os.path.join(d, "norm", sn)
    os.makedirs(os.path.join(nd, "text"))
    os.makedirs(os.path.join(nd, "images"))
    # two identical notebooks (dedup), one SI doc text, 5 images, 1 dup image
    open(os.path.join(nd, "text", "nbA.txt"), "w").write("n_qubits = 8\n" * 100)
    open(os.path.join(nd, "text", "nbB.txt"), "w").write("n_qubits = 8\n" * 100)  # identical -> dedup
    open(os.path.join(nd, "text", "si.txt"), "w").write("baseline 0.88 AUC\n")
    for i in range(5):
        open(os.path.join(nd, "images", "si_p{}.png".format(i + 1)), "wb").write(bytes([i]) * 20)
    open(os.path.join(nd, "images", "dup.png"), "wb").write(bytes([0]) * 20)  # identical to si_p1 -> dedup
    index = {"stable_name": sn, "doi": "10/x", "entries": [
        {"source": "si.pdf", "keep_reason": "si_document", "text_file": "si.txt",
         "image_files": ["si_p1.png", "si_p2.png", "si_p3.png", "si_p4.png", "si_p5.png", "dup.png"]},
        {"source": "nbA.ipynb", "keep_reason": "notebook", "text_file": "nbA.txt", "image_files": []},
        {"source": "nbB.ipynb", "keep_reason": "notebook", "text_file": "nbB.txt", "image_files": []},
    ]}
    json.dump(index, open(os.path.join(nd, "index.json"), "w"))
    tj = os.path.join(d, "targets.jsonl")
    with open(tj, "w") as fh:
        fh.write(json.dumps({"stable_name": sn, "doi": "10/x", "field": "qubit_count",
                             "arity": "scalar", "state": "ABSENCE", "current_value": "",
                             "allowed_verdicts": ["fills_blank", "confirms_absence", "not_found"]}) + "\n")

    class A:
        pass
    A.norm = os.path.join(d, "norm")
    A.targets = tj
    A.out = os.path.join(d, "tasks")
    A.papers = None
    A.exclude = None
    A.img_chunk = 2   # force multiple image chunks
    A.text_chunk = 500  # force multiple text chunks
    A.overlap = 100   # overlap so no value is split unseen
    cmd_build(A)

    tasks = [json.load(open(os.path.join(A.out, f))) for f in os.listdir(A.out) if f.endswith(".task.json")]
    # dedup: nbB identical to nbA -> only one notebook text present; dup.png dropped -> 5 unique images
    all_imgs = []
    all_text = ""
    for t in tasks:
        all_imgs += t["image_paths"]
        all_text += t["supplement_text"]
        assert t["targets"] and t["targets"][0]["field"] == "qubit_count", "targets injected per chunk"
    assert len(all_imgs) == len(set(all_imgs)) == 5, ("images deduped+lossless", len(all_imgs))
    c = all_text.count("n_qubits = 8")
    assert 100 <= c < 200, ("one notebook kept (dedup) + lossless with overlap", c)
    assert "0.88 AUC" in all_text
    print("selftest OK: dedup (identical nb + dup image collapsed), lossless chunking, targets per chunk, {} chunks".format(len(tasks)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    b = sub.add_parser("build")
    b.add_argument("--norm", required=True)
    b.add_argument("--targets", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--papers", default=None)
    b.add_argument("--exclude", default=None)
    b.add_argument("--img-chunk", dest="img_chunk", type=int, default=18)
    b.add_argument("--text-chunk", dest="text_chunk", type=int, default=120000)
    b.add_argument("--overlap", type=int, default=2000)
    b.set_defaults(func=cmd_build)
    s = sub.add_parser("status")
    s.add_argument("--tasks", required=True)
    s.add_argument("--list", type=int, default=0)
    s.set_defaults(func=cmd_status)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if getattr(a, "selftest", False):
        selftest()
        return
    if not getattr(a, "cmd", None):
        ap.print_help()
        sys.exit(2)
    a.func(a)


if __name__ == "__main__":
    main()
