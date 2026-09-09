"""
paradigm_axis_b.py - Registered quantum-paradigm axis (OSF v0.6 sec 7.6, Axis B)
as a deterministic rollup of the finer paradigm_subcategory field.

PROTOCOL CONFORMANCE
--------------------
sec 7.6 fixes Axis B as a mutually-exclusive primary tag drawn from 9 values:
  quantum_machine_learning, quantum_kernel_methods, variational_algorithms,
  qaoa_quantum_optimisation, quantum_annealing_qubo, frqi_neqr_encoding,
  hybrid_quantum_classical, quantum_inspired, other.
The pipeline's paradigm_subcategory (paradigm_subcategory.py) is FINER than this
(16 labels, including several scope-excluded substrates). This module maps the
finer label onto the 9 registered Axis B values so the manuscript can report the
protocol axis verbatim while paradigm_subcategory remains available alongside as
the refinement.

DECISION FLAGGED FOR THE CORRESPONDING AUTHOR
---------------------------------------------
'quantum_image_representation' (non-FRQI/NEQR quantum image representations:
IPQIR, QHED, Quantum Radon/FFT, etc.) is mapped to the registered
'frqi_neqr_encoding' bucket, read as "quantum-image encoding-based pipelines"
broadly. A strict reading of the registered label (FRQI/NEQR only) would instead
send these to 'other'. To switch, change the single line marked DECISION below.

Scope-excluded substrates (detector_instrumentation, quantum_cellular_automata,
quantum_physics_phenomenon, review_or_perspective, other_unresolved) and
quantum_arithmetic_logic have no registered Axis B home and map to 'other'; in
the included corpus these are removed by the eligibility cull, so they should not
appear among synthesised papers.

Python 3.8 compatible.

Usage:
  python3 paradigm_axis_b.py --selftest
  python3 paradigm_axis_b.py --scan extractions_merged.jsonl   (needs paradigm_subcategory.py)
"""

import argparse
import sys


AXIS_B_VOCAB = (
    "quantum_machine_learning",
    "quantum_kernel_methods",
    "variational_algorithms",
    "qaoa_quantum_optimisation",
    "quantum_annealing_qubo",
    "frqi_neqr_encoding",
    "hybrid_quantum_classical",
    "quantum_inspired",
    "other",
)

# paradigm_subcategory (16 labels) -> registered Axis B (9 values).
PARADIGM_AXIS_B_MAP = {
    "qml": "quantum_machine_learning",
    "quantum_kernel": "quantum_kernel_methods",
    "vqe": "variational_algorithms",
    "qaoa": "qaoa_quantum_optimisation",
    "annealing": "quantum_annealing_qubo",
    "frqi": "frqi_neqr_encoding",
    "neqr": "frqi_neqr_encoding",
    "quantum_image_representation": "frqi_neqr_encoding",  # DECISION: -> "other" for strict reading
    "hybrid_quantum_classical": "hybrid_quantum_classical",
    "quantum_inspired": "quantum_inspired",
    # no registered Axis B home -> other (cull-excluded substrates + arithmetic)
    "quantum_arithmetic_logic": "other",
    "detector_instrumentation": "other",
    "quantum_cellular_automata": "other",
    "quantum_physics_phenomenon": "other",
    "review_or_perspective": "other",
    "other_unresolved": "other",
}


def derive_paradigm_axis_b(paradigm_subcategory_value):
    """Map a paradigm_subcategory value to the registered Axis B tag.

    Returns a {"value": ..., "_note": ...} wrapper (same shape as the other
    derived fields, so it renders in the workbook 'Derived (auto-computed)'
    group with no schema change).
    """
    sub = (paradigm_subcategory_value or "").strip().lower()
    axis_b = PARADIGM_AXIS_B_MAP.get(sub, "other")
    note = "registered Axis B from paradigm_subcategory={}".format(sub or "absent")
    if sub not in PARADIGM_AXIS_B_MAP:
        note += " (unmapped -> other)"
    return {"value": axis_b, "_note": note}


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _selftest():
    cases = [
        ("qml", "quantum_machine_learning"),
        ("hybrid_quantum_classical", "hybrid_quantum_classical"),
        ("quantum_kernel", "quantum_kernel_methods"),
        ("vqe", "variational_algorithms"),
        ("qaoa", "qaoa_quantum_optimisation"),
        ("annealing", "quantum_annealing_qubo"),
        ("frqi", "frqi_neqr_encoding"),
        ("neqr", "frqi_neqr_encoding"),
        ("quantum_image_representation", "frqi_neqr_encoding"),
        ("quantum_inspired", "quantum_inspired"),
        ("quantum_arithmetic_logic", "other"),
        ("detector_instrumentation", "other"),
        ("review_or_perspective", "other"),
        ("other_unresolved", "other"),
        ("", "other"),
    ]
    ok = True
    for sub, exp in cases:
        got = derive_paradigm_axis_b(sub)["value"]
        good = got == exp
        ok = ok and good
        print("  {:32s} -> {:28s} {}".format(
            sub or "(empty)", got, "PASS" if good else "FAIL (exp {})".format(exp)))
    # every output is a member of the registered vocabulary
    for sub in PARADIGM_AXIS_B_MAP:
        assert derive_paradigm_axis_b(sub)["value"] in AXIS_B_VOCAB
    print("paradigm_axis_b selftest:", "ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


def _scan(path):
    import json
    from collections import Counter
    from paradigm_subcategory import derive_paradigm_fields
    axis = Counter()
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            passes = rec.get("passes", {})
            sub = derive_paradigm_fields(passes)["paradigm_subcategory"]["value"]
            axis[derive_paradigm_axis_b(sub)["value"]] += 1
            n += 1
    print("papers: {}".format(n))
    print("paradigm_axis_b (REGISTERED) distribution:")
    for k, v in axis.most_common():
        print("  {:4d}  {}".format(v, k))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Registered Axis B paradigm rollup.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan", default="",
                    help="Path to extractions_merged.jsonl (needs paradigm_subcategory.py).")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.scan:
        return _scan(args.scan)
    print("Nothing to do. Use --selftest or --scan <merged.jsonl>.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
