"""
verdict_diff.py
Compare verdicts before vs after the PDF-abstract rescue + re-screen cycle.

Inputs:
    verdicts.jsonl                 current (post-rescue) verdicts
    verdicts_pre_pdf_rescue.jsonl  verdicts that existed before rescue, removed
                                   from verdicts.jsonl by merge_pdf_abstracts.py

Outputs:
    pdf_rescue_diff.csv     per-record before/after row
    Console summary: distribution of category changes, confidence shifts,
                     and explicit flips into / out of QC_FOR_IMAGING.

Usage:
    python verdict_diff.py
"""

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--current", default="verdicts.jsonl")
    p.add_argument("--pre", default="verdicts_pre_pdf_rescue.jsonl")
    p.add_argument("--out", default="pdf_rescue_diff.csv")
    args = p.parse_args()

    pre = load_jsonl(args.pre)
    cur = load_jsonl(args.current)

    pre_by_rid = {v.get("record_id"): v for v in pre if v.get("record_id")}
    cur_by_rid = {v.get("record_id"): v for v in cur if v.get("record_id")}

    print(f"Pre-rescue verdicts (re-screened set): {len(pre_by_rid)}", file=sys.stderr)
    print(f"Current verdicts total:                {len(cur_by_rid)}", file=sys.stderr)

    rids = sorted(pre_by_rid.keys())
    rows = []
    cat_change_counts = Counter()
    conf_change_counts = Counter()
    flips_into_qc = []
    flips_out_of_qc = []
    confidence_up = 0      # low -> high
    confidence_down = 0    # high -> low
    confidence_stable = 0
    missing_in_current = 0

    for rid in rids:
        old = pre_by_rid[rid]
        new = cur_by_rid.get(rid)
        if not new:
            missing_in_current += 1
            rows.append({
                "record_id": rid,
                "old_category": old.get("primary", {}).get("category", ""),
                "new_category": "(missing)",
                "old_confidence": old.get("confidence", ""),
                "new_confidence": "",
                "old_secondary": (old.get("secondary") or {}).get("category", "") if old.get("secondary") else "",
                "new_secondary": "",
                "category_changed": "",
                "flip_into_qc": "",
                "flip_out_of_qc": "",
            })
            continue
        oc = old.get("primary", {}).get("category", "")
        nc = new.get("primary", {}).get("category", "")
        of = old.get("confidence", "")
        nf = new.get("confidence", "")
        os_ = (old.get("secondary") or {}).get("category", "") if old.get("secondary") else ""
        ns_ = (new.get("secondary") or {}).get("category", "") if new.get("secondary") else ""

        changed = oc != nc
        flip_in = (oc != "QC_FOR_IMAGING") and (nc == "QC_FOR_IMAGING")
        flip_out = (oc == "QC_FOR_IMAGING") and (nc != "QC_FOR_IMAGING")

        if changed:
            cat_change_counts[(oc, nc)] += 1
        if flip_in:
            flips_into_qc.append(rid)
        if flip_out:
            flips_out_of_qc.append(rid)

        if of == nf:
            confidence_stable += 1
        elif of == "low" and nf == "high":
            confidence_up += 1
        elif of == "high" and nf == "low":
            confidence_down += 1
        conf_change_counts[(of, nf)] += 1

        rows.append({
            "record_id": rid,
            "old_category": oc, "new_category": nc,
            "old_confidence": of, "new_confidence": nf,
            "old_secondary": os_, "new_secondary": ns_,
            "category_changed": "yes" if changed else "no",
            "flip_into_qc": "yes" if flip_in else "",
            "flip_out_of_qc": "yes" if flip_out else "",
        })

    # ---- Console report ----
    n = len(rids)
    print("\n" + "=" * 60)
    print("Verdict diff: pre-rescue vs post-rescue")
    print("=" * 60)
    print(f"  Records compared:      {n}")
    print(f"  Category unchanged:    {n - sum(cat_change_counts.values()) - missing_in_current}")
    print(f"  Category changed:      {sum(cat_change_counts.values())}")
    print(f"  Missing in current:    {missing_in_current}")
    print(f"\nConfidence trajectory:")
    print(f"  low → high (upgraded): {confidence_up}")
    print(f"  high → low (worsened): {confidence_down}")
    print(f"  unchanged:             {confidence_stable}")

    print(f"\nFlips INTO QC_FOR_IMAGING:  {len(flips_into_qc)}")
    for rid in flips_into_qc[:20]:
        print(f"  {rid}")
    if len(flips_into_qc) > 20:
        print(f"  ... and {len(flips_into_qc) - 20} more")

    print(f"\nFlips OUT OF QC_FOR_IMAGING: {len(flips_out_of_qc)}")
    for rid in flips_out_of_qc[:20]:
        print(f"  {rid}")
    if len(flips_out_of_qc) > 20:
        print(f"  ... and {len(flips_out_of_qc) - 20} more")

    if cat_change_counts:
        print(f"\nTop category transitions (old → new):")
        for (o, ne), c in cat_change_counts.most_common(15):
            print(f"  {c:4d}  {o:30s} → {ne}")

    # ---- CSV output ----
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [
            "record_id", "old_category", "new_category", "old_confidence",
            "new_confidence", "old_secondary", "new_secondary",
            "category_changed", "flip_into_qc", "flip_out_of_qc",
        ])
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
