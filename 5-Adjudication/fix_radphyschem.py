#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_radphyschem.py

Single-field correction + verification, for the case where
apply_step5_contradiction_fixes.py was ALREADY run (your data has the six good
edits applied, plus three questionable ones).

On merits review, only ONE field is a genuine value error:

  radphyschem (10.1016/j.radphyschem.2025.113545) C.baseline_rigour_grade
    'weak' -> 'matched_data'
  Grounding quote in that field: "classical CNN under the same data split and
  preprocessing settings" == matched_data by definition.

s10791 (C.baseline_rigour_grade = 'weak') and s11760
(C.dataset_realism_grade = 'public_benchmark') are LEFT AS-IS: both are
defensible on the merits. This script does not touch them.

DO NOT re-run apply_step5_contradiction_fixes.py on already-patched data; its
pre-edit assertions expect the ORIGINAL values and will halt.

Idempotent: safe to run more than once (accepts the field already at target).
Python 3.8. .format() style.

Usage:
  python3 fix_radphyschem.py --merged extractions_merged.jsonl \
                             --out extractions_merged_fixed.jsonl
"""
import argparse
import json
import sys

RADPHYS = "10.1016/j.radphyschem.2025.113545"


def norm(s):
    return (str(s) if s is not None else "").strip().lower()


def field_entry(rec, pl, f):
    return rec.get("passes", {}).get(pl, {}).get(f)


def wval(e):
    if isinstance(e, dict):
        return e.get("value")
    if isinstance(e, list):
        return [wval(x) for x in e]
    return e


def nested_real_vs_sim(rec):
    hr = field_entry(rec, "D", "honest_resource_reporting_evidence")
    if isinstance(hr, dict) and isinstance(hr.get("real_vs_sim_explicit"), dict):
        return hr["real_vs_sim_explicit"].get("value")
    return "(n/a)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged", required=True, help="your CURRENT extractions_merged.jsonl")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    recs = []
    with open(a.merged, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                recs.append(json.loads(line))
    by = {norm(r.get("doi")): r for r in recs}

    # --- the one correction ---
    r = by.get(norm(RADPHYS))
    if r is None:
        print("FATAL: radphyschem (%s) not found in --merged" % RADPHYS)
        sys.exit(2)
    e = field_entry(r, "C", "baseline_rigour_grade")
    cur = wval(e)
    if cur not in ("weak", "matched_data"):
        print("HALT (no file written): radphyschem C.baseline_rigour_grade is {!r}; "
              "expected 'weak' or 'matched_data'. Not touching.".format(cur))
        sys.exit(3)
    if cur == "weak":
        e["value"] = "matched_data"
        r.setdefault("_step5_adjudication", []).append({
            "field": "passes.C.baseline_rigour_grade", "old": "weak", "new": "matched_data",
            "basis": "Correction: grounding quote 'classical CNN under the same data "
                     "split and preprocessing settings' == matched_data. A prior "
                     "patch wrongly set 'weak'.",
            "source": "Step-5 merits review", "date": "2026-06-22"})
        action = "weak -> matched_data  (CORRECTED)"
    else:
        action = "already matched_data  (no change; idempotent)"

    with open(a.out, "w", encoding="utf-8") as fh:
        for x in recs:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")

    # --- verification table over all affected fields ---
    def show(doi, pl, f, label, expect):
        rr = by.get(norm(doi))
        v = wval(field_entry(rr, pl, f)) if rr else "(missing row)"
        flag = "OK" if (expect == "(any)" or str(v) == str(expect)) else "?? expected {!r}".format(expect)
        print("  {:30s} {:22s} = {!r:46s} {}".format(label, f, v, flag))

    print("\nVERIFICATION (your data after this fix):")
    show("10.1109/access.2025.3531407", "B", "real_or_simulator", "access_3531407 [good]", "simulator")
    show("10.1109/access.2025.3581030", "A", "dataset_name", "access_3581030 [good]", "(any)")
    show("10.1088/2632-2153/acffa3", "A", "modality_primary", "acffa3 [good]", "other")
    show("10.1088/2632-2153/acffa3", "A", "modality_secondary", "acffa3 [good]", "X-ray")
    show("10.1038/s41598-026-51942-9", "B", "image_encoding", "s41598_026 [good]", "(any)")
    rr = by.get(norm("10.1109/jbhi.2025.3610855"))
    print("  {:30s} {:22s} = {!r:46s} {}".format(
        "jbhi [good]", "real_vs_sim_explicit", nested_real_vs_sim(rr),
        "OK" if nested_real_vs_sim(rr) is False else "?? expected False"))
    show("10.1016/j.radphyschem.2025.113545", "C", "baseline_rigour_grade", "radphyschem [FIXED]", "matched_data")
    show("10.1007/s10791-025-09634-x", "C", "baseline_rigour_grade", "s10791 [left: defensible]", "(any)")
    show("10.1007/s11760-023-02857-9", "C", "dataset_realism_grade", "s11760 [left: defensible]", "(any)")

    print("\nradphyschem:", action)
    print("wrote:", a.out)
    print("\nNote: ejca exclusion is tracked in included_step5.csv (134), not in this jsonl.")


if __name__ == "__main__":
    main()
