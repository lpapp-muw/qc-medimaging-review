# -*- coding: utf-8 -*-
# Phase 1 driver - supplemental-link detection batches + reply validation.
#
# prepare : slice each included paper's OCR-augmented main text into chunk
#           manifests for the detect-supp-links subagent.
# validate: ingest subagent replies, ground every link quote as a verbatim
#           substring of the source text, dedupe locators per paper, classify,
#           and write supp_links.jsonl + the list of zero-link papers.
# selftest: exercise grounding + dedupe on synthetic input (no PDFs needed).
#
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python phase1_supp_links.py prepare  --text-dir DIR --included CSV --out MANIFEST_DIR [--chunk-chars 90000 --overlap 2000]
#   python phase1_supp_links.py validate --manifest-dir DIR --text-dir DIR --out supp_links.jsonl
#   python phase1_supp_links.py selftest

import sys
import os
import csv
import json
import re
import argparse

WS = re.compile(r"\s+")


def norm_ws(s):
    if s is None:
        return ""
    return WS.sub(" ", str(s)).strip().lower()


def norm_nospace(s):
    """Alnum-only, space-stripped, lower-cased. For grounding URL/DOI/identifier
    quotes that split across a line break or carry OCR drift."""
    return re.sub(r"[^a-z0-9]+", "", str(s if s is not None else "").lower())


def grounded(quote, source_norm, source_nospace):
    """True iff quote grounds in the source. Primary: whitespace+case-normalised
    substring (mirrors the extraction validator). Fallback for URL/DOI/identifier
    quotes that get split across a line break or carry OCR drift: alnum-only,
    space-stripped substring, applied only when the stripped quote is long enough
    to stay distinctive (>= 15 alnum chars)."""
    q = norm_ws(quote)
    if q and q in source_norm:
        return True
    qn = norm_nospace(quote)
    if len(qn) >= 15 and qn in source_nospace:
        return True
    return False


def norm_locator(loc):
    """Canonicalise a URL/DOI/filename for dedupe."""
    s = str(loc or "").strip()
    s = s.rstrip(").,;]")
    low = s.lower()
    low = re.sub(r"^https?://", "", low)
    low = re.sub(r"^www\.", "", low)
    low = low.rstrip("/")
    # collapse a bare doi.org form and an https form to the same key
    low = low.replace("dx.doi.org/", "doi.org/")
    return low


def read_text(text_dir, stable):
    p = os.path.join(text_dir, stable + ".txt")
    with open(p, "r", errors="replace") as fh:
        return fh.read()


def cmd_prepare(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    inc = []
    with open(a.included, "r") as fh:
        for row in csv.DictReader(fh):
            sn = str(row.get("stable_name") or "").strip()
            doi = str(row.get("doi") or "").strip()
            if sn:
                inc.append((sn, doi))
    n_man = 0
    n_missing = 0
    for sn, doi in inc:
        try:
            text = read_text(a.text_dir, sn)
        except IOError:
            n_missing += 1
            sys.stderr.write("MISSING TEXT: {}\n".format(sn))
            continue
        chunks = []
        if len(text) <= a.chunk_chars:
            chunks = [text]
        else:
            step = a.chunk_chars - a.overlap
            i = 0
            while i < len(text):
                chunks.append(text[i:i + a.chunk_chars])
                i += step
        for ci, ctext in enumerate(chunks):
            man = {
                "stable_name": sn,
                "doi": doi,
                "chunk_id": ci,
                "n_chunks": len(chunks),
                "instruction": ("Detect supplemental-material links in the TEXT "
                                "block below per the detect-supp-links contract. "
                                "Output only the JSON object."),
                "text": ctext,
                "reply_path": os.path.join(a.out, "{}__c{}.links.json".format(sn, ci)),
            }
            mp = os.path.join(a.out, "{}__c{}.task.json".format(sn, ci))
            with open(mp, "w") as fh:
                json.dump(man, fh, ensure_ascii=False)
            n_man += 1
    print("prepared manifests: {} | papers: {} | missing-text: {}".format(n_man, len(inc), n_missing))


def cmd_validate(a):
    # group manifests by paper, collect source text per paper (concat chunks once)
    tasks = [f for f in os.listdir(a.manifest_dir) if f.endswith(".task.json")]
    src_cache = {}
    by_paper = {}
    accepted = 0
    rejected = 0
    reject_rows = []
    for tf in sorted(tasks):
        with open(os.path.join(a.manifest_dir, tf), "r") as fh:
            man = json.load(fh)
        sn = man["stable_name"]
        if sn not in src_cache:
            try:
                _t = read_text(a.text_dir, sn)
            except IOError:
                _t = man.get("text", "")
            src_cache[sn] = (norm_ws(_t), norm_nospace(_t))
        src_norm, src_nospace = src_cache[sn]
        rp = man.get("reply_path")
        if not rp or not os.path.exists(rp):
            sys.stderr.write("NO REPLY: {}\n".format(tf))
            continue
        with open(rp, "r") as fh:
            raw = fh.read().strip()
        if raw.startswith("```"):
            raw = raw.strip("`")
            raw = raw[raw.find("{"):]
        try:
            reply = json.loads(raw)
        except ValueError:
            sys.stderr.write("BAD JSON: {}\n".format(rp))
            continue
        for ln in reply.get("links", []) or []:
            loc = ln.get("locator")
            q = ln.get("quote")
            typ = ln.get("type", "other_resource")
            if grounded(q, src_norm, src_nospace):
                by_paper.setdefault(sn, {"doi": man.get("doi", ""), "links": {}})
                key = (typ, norm_locator(loc))
                if key not in by_paper[sn]["links"]:
                    by_paper[sn]["links"][key] = {
                        "type": typ, "locator": loc, "quote": q,
                        "chunk_id": ln.get("chunk_id"),
                    }
                accepted += 1
            else:
                rejected += 1
                reject_rows.append({"stable_name": sn, "type": typ, "locator": loc,
                                    "reason": "quote_not_in_source"})

    # write outputs
    inc_papers = sorted(set(json.load(open(os.path.join(a.manifest_dir, tf)))["stable_name"]
                            for tf in tasks)) if tasks else []
    with open(a.out, "w") as fh:
        for sn in sorted(by_paper):
            rec = {"stable_name": sn, "doi": by_paper[sn]["doi"],
                   "links": list(by_paper[sn]["links"].values())}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    zero = [sn for sn in inc_papers if sn not in by_paper]
    zpath = os.path.splitext(a.out)[0] + "__zero_link_papers.txt"
    with open(zpath, "w") as fh:
        fh.write("\n".join(zero) + ("\n" if zero else ""))
    rpath = os.path.splitext(a.out)[0] + "__rejected_quotes.csv"
    with open(rpath, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stable_name", "type", "locator", "reason"])
        w.writeheader()
        for r in reject_rows:
            w.writerow(r)
    # tally
    type_tally = {}
    for sn in by_paper:
        for v in by_paper[sn]["links"].values():
            type_tally[v["type"]] = type_tally.get(v["type"], 0) + 1
    print("papers with >=1 grounded link: {} | zero-link papers: {}".format(len(by_paper), len(zero)))
    print("grounded links: {} | rejected (ungrounded): {}".format(accepted, rejected))
    print("link type tally: {}".format(type_tally))
    print("wrote: {} | {} | {}".format(a.out, zpath, rpath))


def cmd_selftest(_a):
    src = ("Methods. Code and trained circuit parameters are available at "
           "https://github.com/example/qmed-imaging . The dataset is deposited "
           "on Zenodo (https://doi.org/10.5281/zenodo.7654321). See the "
           "Supporting Information (qc-supp.pdf). We used Qiskit "
           "(https://qiskit.org).")
    src_norm = norm_ws(src)
    src_nospace = norm_nospace(src)
    # one grounded, one dup of it, one ungrounded fabrication, one split-URL
    reply_links = [
        {"type": "code_repository", "locator": "https://github.com/example/qmed-imaging",
         "quote": "available at https://github.com/example/qmed-imaging"},
        {"type": "code_repository", "locator": "https://github.com/example/qmed-imaging/",
         "quote": "Code and trained circuit parameters are available at"},
        {"type": "data_repository", "locator": "https://doi.org/10.5281/zenodo.7654321",
         "quote": "deposited on Zenodo (https://doi.org/10.5281/zenodo.7654321)"},
        {"type": "supplementary_material", "locator": "qc-supp.pdf",
         "quote": "Supporting Information (qc-supp.pdf)"},
        {"type": "data_doi", "locator": "https://doi.org/10.9999/fabricated",
         "quote": "deposited at https://doi.org/10.9999/fabricated"},
    ]
    seen = {}
    grounded_ct = 0
    ungrounded_ct = 0
    for ln in reply_links:
        if grounded(ln["quote"], src_norm, src_nospace):
            grounded_ct += 1
            seen[(ln["type"], norm_locator(ln["locator"]))] = ln
        else:
            ungrounded_ct += 1
    # expectations: 4 grounded link-instances, 1 ungrounded; after dedupe 3 unique locators
    assert grounded_ct == 4, grounded_ct
    assert ungrounded_ct == 1, ungrounded_ct
    assert len(seen) == 3, list(seen.keys())
    assert ("code_repository", "github.com/example/qmed-imaging") in seen
    # URL-grounding patch: a URL quote split across a line break fails the
    # whitespace-substring check but must still ground via the nospace fallback.
    split_q = "https://github.com/example/qmed-\nimaging"
    assert norm_ws(split_q) not in src_norm, "precondition: split URL should fail whitespace match"
    assert grounded(split_q, src_norm, src_nospace), "split URL must ground via nospace fallback"
    # locator normalisation collapses the trailing-slash duplicate
    assert norm_locator("https://github.com/example/qmed-imaging/") == \
        norm_locator("https://github.com/example/qmed-imaging")
    # dx.doi.org vs doi.org collapse
    assert norm_locator("http://dx.doi.org/10.5281/zenodo.7654321") == \
        norm_locator("https://doi.org/10.5281/zenodo.7654321")
    print("selftest OK: 4 grounded, 1 ungrounded, 3 unique locators after dedupe")


def cmd_status(a):
    """Report pending manifests (those whose reply_path does not yet exist).
    The orchestrator gates on the 'pending:' number; advance only when 0."""
    tasks = [f for f in os.listdir(a.manifest_dir) if f.endswith(".task.json")]
    pending = []
    done = 0
    for tf in sorted(tasks):
        with open(os.path.join(a.manifest_dir, tf), "r") as fh:
            man = json.load(fh)
        rp = man.get("reply_path")
        if rp and os.path.exists(rp):
            done += 1
        else:
            pending.append(tf)
    print("total: {} | done: {} | pending: {}".format(len(tasks), done, len(pending)))
    if getattr(a, "list", 0) and pending:
        k = min(a.list, len(pending))
        print("next pending ({}):".format(k))
        for tf in pending[:k]:
            print("  " + os.path.join(a.manifest_dir, tf))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("prepare")
    p.add_argument("--text-dir", dest="text_dir", required=True)
    p.add_argument("--included", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--chunk-chars", dest="chunk_chars", type=int, default=90000)
    p.add_argument("--overlap", type=int, default=2000)
    p.set_defaults(func=cmd_prepare)
    v = sub.add_parser("validate")
    v.add_argument("--manifest-dir", dest="manifest_dir", required=True)
    v.add_argument("--text-dir", dest="text_dir", required=True)
    v.add_argument("--out", required=True)
    v.set_defaults(func=cmd_validate)
    s = sub.add_parser("selftest")
    s.set_defaults(func=cmd_selftest)
    st = sub.add_parser("status")
    st.add_argument("--manifest-dir", dest="manifest_dir", required=True)
    st.add_argument("--list", type=int, default=0)
    st.set_defaults(func=cmd_status)
    a = ap.parse_args()
    if not getattr(a, "cmd", None):
        ap.print_help()
        sys.exit(2)
    a.func(a)


if __name__ == "__main__":
    main()
