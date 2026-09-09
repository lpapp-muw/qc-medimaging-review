"""
paradigm_subcategory.py — Deterministic, grounded resolution of the Pass-B
`paradigm` field for the Step-4 extraction pipeline (IEEE TRPMS systematic
review on quantum computing for medical imaging).

Why this module exists
----------------------
Pass-B records a `paradigm` enum whose §9.2 vocabulary is
{QML, VQE, QAOA, quantum_kernel, FRQI, NEQR, hybrid_quantum_classical, other}.
That enum has no member for several real paradigms that appear in the corpus
(quantum annealing / QUBO, detector / instrumentation physics, non-FRQI/NEQR
quantum-image representations, quantum-inspired classical, quantum cellular
automata, quantum arithmetic/logic circuits, review/perspective secondary
studies). Those papers were therefore forced to `paradigm == "other"`, which
degrades the Step-5 quantum-paradigm axis and any paradigm-based synthesis.

This module does NOT re-run any subagent and does NOT mutate the raw
`paradigm` value. It computes THREE new *derived* fields from already-extracted,
already-grounded Pass-A/Pass-B values:

  paradigm_subcategory        single primary label (the Step-5 axis), drawn from
                              an expanded controlled vocabulary; resolves
                              `other` to a real label wherever a grounded signal
                              exists. `other_unresolved` only if nothing fires.
  paradigm_facets             ALL detected facets, "; "-joined, so multi-paradigm
                              papers lose nothing (multi-label feature for AI
                              analyses; full picture for humans).
  paradigm_resolution_basis   provenance: "inherited" (raw paradigm was already a
                              concrete enum member) or "derived" plus the
                              concrete signal(s) that fired, e.g.
                              `annealing<-hardware_modality=annealing`. Lets a
                              human or AI verify each call without re-reading the
                              PDF; pairs with the existing paradigm_novelty_note
                              column.

All three are returned in the {"value": ..., "_note": ...} wrapper shape used by
compute_derived.py so they render in the workbook's "Derived (auto-computed)"
group with no schema change.

Determinism & grounding
------------------------
Every signal is read from a field that the extraction already produced and
quote-grounded:
  - hardware_modality, image_encoding, paradigm_novelty_note   (Pass B)
  - imaging_task, title                                        (Pass A)
No LLM call, no web, no guessing. The grounding string in
paradigm_resolution_basis names the exact field/value that triggered each facet.

Python 3.8 compatible (no PEP-604/585 unions, no @dataclass with generic
annotations, no from __future__ import).
"""

import argparse
import json
import re
import sys
from collections import OrderedDict


# ----------------------------------------------------------------------------
# Controlled vocabulary
# ----------------------------------------------------------------------------
# Raw §9.2 paradigm enum members that are already concrete: inherited verbatim
# into paradigm_subcategory (lower-cased canonical form).
CANONICAL_PARADIGMS = frozenset([
    "qml", "vqe", "qaoa", "quantum_kernel", "frqi", "neqr",
    "hybrid_quantum_classical",
])

# Facets resolved from `other`, in PRIMARY-PICK priority order (most specific /
# decision-relevant first). When several fire, paradigm_subcategory takes the
# first in this order; paradigm_facets lists all of them.
RESOLVED_FACETS_PRIORITY = (
    "review_or_perspective",        # secondary study; article-type exclusion (overrides topic)
    "quantum_cellular_automata",    # QCA nanoelectronic paradigm (excluded substrate)
    "annealing",                    # quantum annealing / QUBO (D-Wave etc.) -- real QC
    "detector_instrumentation",     # quantum detector / imaging-instrument physics
    "quantum_arithmetic_logic",     # quantum digital/arithmetic circuit design -- real QC
    "quantum_image_representation",  # non-FRQI/NEQR quantum image rep / processing -- real QC
    "quantum_inspired",             # branded quantum, classically executed -- ranked BELOW the
                                    # real-computing facets so a paper that merely *mentions*
                                    # "quantum-inspired" while doing real quantum work resolves
                                    # to the real-quantum facet (avoids false exclusion).
    "quantum_physics_phenomenon",   # entanglement / error-correction / ghost imaging
)

# Full subcategory vocabulary (for documentation / OSF; the workbook column is
# rendered as plain text, no dropdown).
SUBCATEGORY_VOCAB = (
    sorted(CANONICAL_PARADIGMS) + list(RESOLVED_FACETS_PRIORITY) + ["other_unresolved"]
)


# ----------------------------------------------------------------------------
# Value accessors (mirror compute_derived._v semantics exactly)
# ----------------------------------------------------------------------------
def _v(passes, pass_letter, field, default=None):
    """Scalar value of a field in a merged pass record (dict wrapper)."""
    p = passes.get(pass_letter, {}) if passes else {}
    entry = p.get(field)
    if entry is None:
        return default
    if isinstance(entry, dict):
        return entry.get("value", default)
    if isinstance(entry, list):
        return [it.get("value") if isinstance(it, dict) else it for it in entry]
    return entry


def _values_lower(passes, pass_letter, field):
    """Return a set of lower-cased string values for a (possibly multi-value)
    field. Empty set if absent."""
    val = _v(passes, pass_letter, field)
    if val is None:
        return set()
    if isinstance(val, list):
        return set(str(x).strip().lower() for x in val if x is not None and str(x).strip())
    s = str(val).strip().lower()
    return {s} if s else set()


def _scalar_lower(passes, pass_letter, field):
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val).strip().lower()


# ----------------------------------------------------------------------------
# Facet detection
# ----------------------------------------------------------------------------
_RE_REVIEW = re.compile(r"\b(review|perspective|survey)\b")
_RE_QINSPIRED = re.compile(r"quantum[\s\-]inspired")
_RE_QCA = re.compile(r"cellular automata|\bqca\b")

_KW_ANNEAL = ("qubo", "anneal")  # 'anneal' covers annealing/annealer
_KW_DETECTOR = (
    "detector", "single-photon", "single photon", "scintillat",
    "spectral characteri", "photon-counting", "photon counting",
)
_KW_ARITH = ("overflow", "arithmetic", "adder", "logic gate",
             "priority encoder", "comparator")
_KW_REPR = ("representation", "qhed", "radon", "fourier", "quantum image",
            "edge detection", "image processing")
_KW_PHYS = ("entangl", "error channel", "error-correction", "error correction",
            "annihilation", "compton", "ghost imaging")
_ENC_REPR = frozenset(["other", "neqr", "frqi"])


def detect_facets(passes):
    """Return an OrderedDict {facet: grounding_reason} in PRIMARY-PICK priority
    order. Only called when the raw paradigm is `other` (or absent)."""
    novelty = _scalar_lower(passes, "B", "paradigm_novelty_note")
    title = _scalar_lower(passes, "A", "title")
    blob = (novelty + " " + title).strip()
    hardware_modality = _scalar_lower(passes, "B", "hardware_modality")
    imaging_task = _scalar_lower(passes, "A", "imaging_task")
    encodings = _values_lower(passes, "B", "image_encoding")

    hits = {}  # facet -> reason

    m = _RE_REVIEW.search(title)
    if m:
        hits["review_or_perspective"] = "title~{}".format(m.group(1))

    if _RE_QINSPIRED.search(blob):
        hits["quantum_inspired"] = "novelty/title~quantum-inspired"

    if _RE_QCA.search(blob):
        hits["quantum_cellular_automata"] = "novelty/title~cellular_automata"

    if hardware_modality == "annealing":
        hits["annealing"] = "hardware_modality=annealing"
    else:
        for kw in _KW_ANNEAL:
            if kw in blob:
                hits["annealing"] = "novelty/title~{}".format(kw)
                break

    if imaging_task == "instrumentation":
        hits["detector_instrumentation"] = "imaging_task=instrumentation"
    else:
        for kw in _KW_DETECTOR:
            if kw in blob:
                hits["detector_instrumentation"] = "novelty/title~{}".format(kw.strip())
                break

    for kw in _KW_ARITH:
        if kw in blob:
            hits["quantum_arithmetic_logic"] = "novelty/title~{}".format(kw)
            break

    rep_enc = encodings & _ENC_REPR
    if rep_enc:
        hits["quantum_image_representation"] = "image_encoding={}".format(sorted(rep_enc)[0])
    else:
        for kw in _KW_REPR:
            if kw in blob:
                hits["quantum_image_representation"] = "novelty/title~{}".format(kw)
                break

    for kw in _KW_PHYS:
        if kw in blob:
            hits["quantum_physics_phenomenon"] = "novelty/title~{}".format(kw)
            break

    ordered = OrderedDict()
    for facet in RESOLVED_FACETS_PRIORITY:
        if facet in hits:
            ordered[facet] = hits[facet]
    return ordered


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def derive_paradigm_fields(passes):
    """Compute the three paradigm-subcategory derived fields for one paper.

    Returns a dict of three {"value": ..., "_note": ...} wrappers keyed:
      paradigm_subcategory, paradigm_facets, paradigm_resolution_basis.
    """
    raw = _scalar_lower(passes, "B", "paradigm")

    if raw in CANONICAL_PARADIGMS:
        primary = raw
        facets = [raw]
        basis = "inherited<-paradigm={}".format(raw)
        note = "raw paradigm already concrete"
        return {
            "paradigm_subcategory": {"value": primary, "_note": note},
            "paradigm_facets": {"value": "; ".join(facets), "_note": note},
            "paradigm_resolution_basis": {"value": basis, "_note": note},
        }

    # raw == "other" (or absent / unexpected): resolve from grounded signals.
    ordered = detect_facets(passes)
    if ordered:
        facets = list(ordered.keys())
        primary = facets[0]
        basis = "derived | " + "; ".join(
            "{}<-{}".format(f, r) for f, r in ordered.items())
        ambiguous = len(facets) > 1
        note = "resolved from {} signal(s){}".format(
            len(facets), "; AMBIGUOUS (multi-facet)" if ambiguous else "")
    else:
        facets = ["other_unresolved"]
        primary = "other_unresolved"
        basis = "derived | no_grounded_signal (raw paradigm={})".format(raw or "absent")
        note = "no grounded paradigm signal; left other_unresolved"

    out = {
        "paradigm_subcategory": {"value": primary, "_note": note},
        "paradigm_facets": {"value": "; ".join(facets), "_note": note},
        "paradigm_resolution_basis": {"value": basis, "_note": note},
    }
    if len(facets) > 1:
        out["paradigm_subcategory"]["_ambiguous"] = True
        out["paradigm_facets"]["_ambiguous"] = True
    return out


# ----------------------------------------------------------------------------
# CLI: --selftest, or scan a merged jsonl for a distribution report
# ----------------------------------------------------------------------------
def _mk(passes):
    return {"passes": passes}


def _selftest():
    cases = []

    # 1. inherited concrete paradigm
    r = derive_paradigm_fields({
        "B": {"paradigm": {"value": "hybrid_quantum_classical"}}})
    assert r["paradigm_subcategory"]["value"] == "hybrid_quantum_classical", r
    assert r["paradigm_resolution_basis"]["value"].startswith("inherited"), r
    cases.append("inherited")

    # 2. annealing via hardware_modality (raw other)
    r = derive_paradigm_fields({
        "A": {"title": {"value": "Quantum annealing feature selection"},
              "imaging_task": {"value": "classification"}},
        "B": {"paradigm": {"value": "other"},
              "hardware_modality": {"value": "annealing"},
              "paradigm_novelty_note": {"value": "QUBO model solved on a quantum annealer"},
              "image_encoding": [{"value": "not_applicable"}]}})
    assert r["paradigm_subcategory"]["value"] == "annealing", r
    assert "hardware_modality=annealing" in r["paradigm_resolution_basis"]["value"], r
    cases.append("annealing")

    # 3. detector/instrumentation via imaging_task, physics as secondary facet
    r = derive_paradigm_fields({
        "A": {"title": {"value": "Background reduction in PET by entangled annihilation photons"},
              "imaging_task": {"value": "instrumentation"}},
        "B": {"paradigm": {"value": "other"},
              "hardware_modality": {"value": "not_reported"},
              "paradigm_novelty_note": {"value": "quantum entanglement of annihilation photons"},
              "image_encoding": [{"value": "not_applicable"}]}})
    assert r["paradigm_subcategory"]["value"] == "detector_instrumentation", r
    assert "quantum_physics_phenomenon" in r["paradigm_facets"]["value"], r
    assert r["paradigm_facets"].get("_ambiguous") is True, r
    cases.append("detector+physics multifacet")

    # 4. representation beats physics when both fire (priority order)
    r = derive_paradigm_fields({
        "A": {"title": {"value": "IPQIR improved probabilistic quantum image representation"},
              "imaging_task": {"value": "analysis"}},
        "B": {"paradigm": {"value": "other"},
              "hardware_modality": {"value": "simulator_only"},
              "paradigm_novelty_note": {"value": "representation based on entanglement of qubits"},
              "image_encoding": [{"value": "other"}]}})
    assert r["paradigm_subcategory"]["value"] == "quantum_image_representation", r
    cases.append("representation>physics priority")

    # 5. review/perspective from title
    r = derive_paradigm_fields({
        "A": {"title": {"value": "A Review of medical image processing using quantum algorithms"},
              "imaging_task": {"value": "other"}},
        "B": {"paradigm": {"value": "other"},
              "paradigm_novelty_note": {"value": "this review surveys the field"},
              "image_encoding": [{"value": "not_applicable"}]}})
    assert r["paradigm_subcategory"]["value"] == "review_or_perspective", r
    cases.append("review")

    # 6. nothing fires -> other_unresolved (kept as a safety value)
    r = derive_paradigm_fields({
        "A": {"title": {"value": "Some quantum method"}, "imaging_task": {"value": "analysis"}},
        "B": {"paradigm": {"value": "other"},
              "hardware_modality": {"value": "simulator_only"},
              "paradigm_novelty_note": {"value": "a bespoke approach"},
              "image_encoding": [{"value": "amplitude"}]}})
    assert r["paradigm_subcategory"]["value"] == "other_unresolved", r
    cases.append("other_unresolved fallback")

    print("paradigm_subcategory selftest OK:", ", ".join(cases))
    return 0


def _scan(path):
    from collections import Counter
    prim = Counter()
    bare = 0
    multi = 0
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            passes = rec.get("passes", {})
            out = derive_paradigm_fields(passes)
            p = out["paradigm_subcategory"]["value"]
            prim[p] += 1
            if p == "other_unresolved":
                bare += 1
            if "; " in out["paradigm_facets"]["value"]:
                multi += 1
            n += 1
    print("papers: {}".format(n))
    print("bare other_unresolved: {}".format(bare))
    print("multi-facet papers: {}".format(multi))
    print("paradigm_subcategory distribution:")
    for k, v in prim.most_common():
        print("  {:4d}  {}".format(v, k))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Paradigm subcategorisation deriver.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan", default="", help="Path to extractions_merged.jsonl for a distribution report.")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.scan:
        return _scan(args.scan)
    print("Nothing to do. Use --selftest or --scan <merged.jsonl>.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
