"""
cull_eligibility.py — Final full-text eligibility cull for the Step-4 corpus
(IEEE TRPMS systematic review on quantum computing for medical imaging).

Applies the locked eligibility rule (quantum computing AND medical image
analysis) as a deterministic filter over the derived `paradigm_subcategory`
field produced by paradigm_subcategory.py / compute_derived.py. No re-reading,
no LLM: the classification was already done and grounded; this just partitions
the corpus and writes the PRISMA exclusion accounting.

Excluded subcategories and their PRISMA full-text exclusion reason:
  detector_instrumentation     quantum sensing / detector physics, not quantum computing
  quantum_physics_phenomenon   quantum physics phenomenon (entanglement/error-correction),
                               not quantum computing
  quantum_inspired             quantum-inspired classical method, no quantum computation
  quantum_cellular_automata    quantum cellular automata, not gate/annealing quantum computing
  review_or_perspective        secondary study (review / perspective)

Everything else (the inherited gate-model/kernel/annealing paradigms plus
quantum_image_representation and quantum_arithmetic_logic) is INCLUDED: real
quantum computation applied to a medical-imaging task.

NOTE on multi-facet papers: a paper is included/excluded on its PRIMARY
`paradigm_subcategory`. The priority order in paradigm_subcategory.py ranks the
real-computing facets above `quantum_inspired`, so a paper that both mentions
"quantum-inspired" and performs real quantum work resolves to the real-quantum
facet and is INCLUDED. `paradigm_facets` is carried into the output so any
masked facet remains visible for the Step-5 human review.

Outputs (under --outdir, default EXTRACT_DIR):
  included_step5.csv        doi, stable_name, paradigm_subcategory, paradigm_facets
  excluded_fulltext.csv     doi, stable_name, paradigm_subcategory, prisma_reason, paradigm_facets
  cull_summary.txt          counts (PRISMA-ready)

Usage:
  python3 cull_eligibility.py
  python3 cull_eligibility.py --infile extractions_derived.jsonl --outdir .
  python3 cull_eligibility.py --selftest

Python 3.8 compatible.
"""

import argparse
import csv
import json
import sys
from collections import Counter, OrderedDict
from pathlib import Path

try:
    import paths_step4 as P
    _DEFAULT_INFILE = str(P.DERIVED_EXTRACTIONS)
    _DEFAULT_OUTDIR = str(P.EXTRACT_DIR)
except Exception:  # allow running outside the project tree (e.g. selftest)
    _DEFAULT_INFILE = "extractions_derived.jsonl"
    _DEFAULT_OUTDIR = "."


# Excluded primary subcategories -> PRISMA full-text exclusion reason.
EXCLUDE_REASON = OrderedDict([
    ("detector_instrumentation",
     "quantum sensing / detector physics, not quantum computing"),
    ("quantum_physics_phenomenon",
     "quantum physics phenomenon (entanglement / error-correction), not quantum computing"),
    ("quantum_inspired",
     "quantum-inspired classical method, no quantum computation"),
    ("quantum_cellular_automata",
     "quantum cellular automata, not gate/annealing quantum computing"),
    ("review_or_perspective",
     "secondary study (review / perspective)"),
])
# Safety: if a paper somehow stayed unresolved, surface it rather than silently
# include or exclude it.
UNRESOLVED = "other_unresolved"


def _derived_value(rec, field):
    d = rec.get("derived", {}).get(field, {})
    if isinstance(d, dict):
        return d.get("value")
    return d


def classify(rec):
    """Return (decision, subcategory, facets, reason).

    decision in {"include", "exclude", "review_manually"}.
    """
    sub = _derived_value(rec, "paradigm_subcategory")
    facets = _derived_value(rec, "paradigm_facets") or ""
    if sub is None:
        return "review_manually", None, facets, "paradigm_subcategory missing (run compute_derived.py)"
    if sub == UNRESOLVED:
        return "review_manually", sub, facets, "unresolved paradigm; needs human decision"
    if sub in EXCLUDE_REASON:
        return "exclude", sub, facets, EXCLUDE_REASON[sub]
    return "include", sub, facets, ""


def run(infile, outdir):
    inpath = Path(infile)
    if not inpath.exists():
        print("ERROR: input not found: {}".format(inpath), file=sys.stderr)
        return 2
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    included = []
    excluded = []
    manual = []
    excl_by_reason = Counter()
    incl_by_sub = Counter()

    with open(inpath, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            doi = rec.get("doi", "") or rec.get("passes", {}).get("A", {}).get("doi", "")
            stable = rec.get("stable_name", "") or rec.get("_meta", {}).get("stable_name", "")
            decision, sub, facets, reason = classify(rec)
            row = {"doi": doi, "stable_name": stable,
                   "paradigm_subcategory": sub or "", "paradigm_facets": facets}
            if decision == "include":
                included.append(row)
                incl_by_sub[sub] += 1
            elif decision == "exclude":
                row["prisma_reason"] = reason
                excluded.append(row)
                excl_by_reason[sub] += 1
            else:
                row["prisma_reason"] = reason
                manual.append(row)

    # write included
    inc_path = outdir / "included_step5.csv"
    with open(inc_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["doi", "stable_name",
                                           "paradigm_subcategory", "paradigm_facets"])
        w.writeheader()
        for r in included:
            w.writerow(r)

    # write excluded (+ any manual-review rows, tagged)
    exc_path = outdir / "excluded_fulltext.csv"
    with open(exc_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["doi", "stable_name",
                                           "paradigm_subcategory", "prisma_reason",
                                           "paradigm_facets"])
        w.writeheader()
        for r in excluded:
            w.writerow(r)
        for r in manual:
            w.writerow(r)

    # summary
    total = len(included) + len(excluded) + len(manual)
    lines = []
    lines.append("Step-4 full-text eligibility cull")
    lines.append("=================================")
    lines.append("Assessed for eligibility (full text): {}".format(total))
    lines.append("Included in qualitative synthesis:    {}".format(len(included)))
    lines.append("Excluded after full-text assessment:  {}".format(len(excluded)))
    if manual:
        lines.append("FLAGGED for manual decision:          {}".format(len(manual)))
    lines.append("")
    lines.append("Exclusions by reason (PRISMA):")
    for sub in EXCLUDE_REASON:
        if excl_by_reason.get(sub):
            lines.append("  {:4d}  {}  [{}]".format(
                excl_by_reason[sub], EXCLUDE_REASON[sub], sub))
    lines.append("")
    lines.append("Included by paradigm_subcategory:")
    for sub, n in incl_by_sub.most_common():
        lines.append("  {:4d}  {}".format(n, sub))
    if manual:
        lines.append("")
        lines.append("Manual-decision rows (see excluded_fulltext.csv):")
        for r in manual:
            lines.append("  {}  [{}]  {}".format(
                r["doi"], r.get("paradigm_subcategory"), r["prisma_reason"]))
    summary = "\n".join(lines)

    (outdir / "cull_summary.txt").write_text(summary + "\n", encoding="utf-8")
    print(summary)
    print()
    print("Wrote: {}  ({} rows)".format(inc_path, len(included)))
    print("Wrote: {}  ({} rows)".format(exc_path, len(excluded) + len(manual)))
    print("Wrote: {}".format(outdir / "cull_summary.txt"))
    return 0


def _selftest():
    import tempfile
    recs = [
        {"doi": "x/inc-hybrid", "stable_name": "a",
         "derived": {"paradigm_subcategory": {"value": "hybrid_quantum_classical"},
                     "paradigm_facets": {"value": "hybrid_quantum_classical"}}},
        {"doi": "x/inc-anneal", "stable_name": "b",
         "derived": {"paradigm_subcategory": {"value": "annealing"},
                     "paradigm_facets": {"value": "annealing"}}},
        {"doi": "x/inc-rep", "stable_name": "c",
         "derived": {"paradigm_subcategory": {"value": "quantum_image_representation"},
                     "paradigm_facets": {"value": "quantum_image_representation; quantum_inspired"}}},
        {"doi": "x/exc-detector", "stable_name": "d",
         "derived": {"paradigm_subcategory": {"value": "detector_instrumentation"},
                     "paradigm_facets": {"value": "detector_instrumentation; quantum_physics_phenomenon"}}},
        {"doi": "x/exc-inspired", "stable_name": "e",
         "derived": {"paradigm_subcategory": {"value": "quantum_inspired"},
                     "paradigm_facets": {"value": "quantum_inspired"}}},
        {"doi": "x/exc-review", "stable_name": "f",
         "derived": {"paradigm_subcategory": {"value": "review_or_perspective"},
                     "paradigm_facets": {"value": "review_or_perspective"}}},
        {"doi": "x/manual", "stable_name": "g",
         "derived": {"paradigm_subcategory": {"value": "other_unresolved"},
                     "paradigm_facets": {"value": "other_unresolved"}}},
    ]
    d = Path(tempfile.mkdtemp())
    f = d / "in.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in recs), encoding="utf-8")
    rc = run(str(f), str(d))
    inc = list(csv.DictReader(open(d / "included_step5.csv")))
    exc = list(csv.DictReader(open(d / "excluded_fulltext.csv")))
    assert len(inc) == 3, inc
    assert {r["doi"] for r in inc} == {"x/inc-hybrid", "x/inc-anneal", "x/inc-rep"}, inc
    # 3 excluded + 1 manual row both land in excluded_fulltext.csv
    assert len(exc) == 4, exc
    print("\ncull_eligibility selftest OK")
    return rc


def main():
    ap = argparse.ArgumentParser(description="Final full-text eligibility cull.")
    ap.add_argument("--infile", default=_DEFAULT_INFILE,
                    help="extractions_derived.jsonl (default from paths_step4).")
    ap.add_argument("--outdir", default=_DEFAULT_OUTDIR)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    return run(args.infile, args.outdir)


if __name__ == "__main__":
    raise SystemExit(main())
