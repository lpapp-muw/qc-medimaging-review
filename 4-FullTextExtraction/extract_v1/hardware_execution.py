"""
hardware_execution.py - Deterministic hardware-execution subcategorisation for
the Step-4 extraction pipeline (IEEE TRPMS systematic review on quantum
computing for medical imaging). Third of the four Step-5 subcategorisation axes
(after paradigm_subcategory.py).

Why this module exists
----------------------
Synthesis and the Step-6 kappa audit need one consistent "where/how did the
quantum computation run" axis. The raw signal is split across several Pass-B
fields (real_or_simulator, hardware_modality, plus framework / transpilation /
vendor detail). This module collapses them into one primary label, a facet
string, and a provenance string, deterministically and with no LLM, leaving
every raw field untouched.

Convention (locked, Q1): quantum annealing is its OWN top-level class, distinct
from gate-model superconducting hardware. D-Wave is a superconducting
*substrate* operated as an annealer; the analytic axis readers care about is the
compute model (gate vs anneal), not the qubit substrate, and this matches
paradigm_subcategory.py, which already treats `annealing` as a distinct
paradigm. The same convention governs the Step-6 kappa scoring.

Outputs (three derived {"value":..., "_note":...} wrappers, same shape as the
paradigm trio, so they render in the workbook "Derived (auto-computed)" group
with no schema change):

  hardware_execution_subcategory  single primary label, 8-value vocabulary:
                                  gate_superconducting, annealing, trapped_ion,
                                  photonic, nmr, simulator_only,
                                  real_hardware_unspecified, not_reported.
  hardware_execution_facets       "; "-joined attribute tokens carried for human
                                  review and kappa: mode=<real_or_simulator>,
                                  framework=<simulator_framework>,
                                  transpiled=<yes|nr>.
  hardware_execution_basis        provenance: the raw signals that produced the
                                  label (hardware_modality, real_or_simulator,
                                  hardware_vendor, simulator_framework,
                                  transpilation_level), so a reviewer can verify
                                  each call and, in particular, reclassify any
                                  `real_hardware_unspecified` paper whose vendor
                                  string names a platform the extractor left out
                                  of hardware_modality.

Derivation (first match wins, per paper):
  1. hardware_modality names a platform -> that class
     (superconducting -> gate_superconducting; annealing; trapped_ion; photonic).
  2. else (real/both run) hardware_vendor names a substrate absent from the
     hardware_modality enum -> that substrate (currently only nmr). This keeps
     the principle "if the platform is named anywhere, classify it at that
     granularity"; real_hardware_unspecified is reserved for platform-named-
     nowhere.
  3. else real_or_simulator == simulator -> simulator_only.
  4. else real_or_simulator in {real, both} -> real_hardware_unspecified
     (real run; platform named in neither hardware_modality nor hardware_vendor).
  5. else -> not_reported.
Branch 1 cannot mislabel a pure-simulator paper: in the corpus every
named-platform value co-occurs only with real_or_simulator in {real, both},
never simulator. Vendor-substrate recovery (branch 2) is gated to real/both so a
simulator paper that merely cites real hardware as a noise-model source stays
simulator_only.

Determinism & grounding: every signal is an already-extracted, already
quote-grounded Pass-B field. No LLM, no web, no guessing.

Python 3.8 compatible (no PEP-604/585 unions, no @dataclass with generic
annotations, no from __future__ import).

Usage:
  python3 hardware_execution.py --selftest
  python3 hardware_execution.py --scan extractions_merged.jsonl
"""

import argparse
import json
import re
import sys
from collections import OrderedDict


# ----------------------------------------------------------------------------
# Controlled vocabulary
# ----------------------------------------------------------------------------
# hardware_modality values that name a concrete real platform -> primary class.
# superconducting is renamed to gate_superconducting to make the compute model
# explicit and to keep it distinct from `annealing` per the locked convention.
PLATFORM_LABELS = OrderedDict([
    ("superconducting", "gate_superconducting"),
    ("annealing", "annealing"),
    ("trapped_ion", "trapped_ion"),
    ("photonic", "photonic"),
])

SUBCATEGORY_VOCAB = (
    list(PLATFORM_LABELS.values())
    + ["nmr", "simulator_only", "real_hardware_unspecified", "not_reported"]
)

# Tokens treated as "no value reported" when reading a field.
_ABSENCE = frozenset([
    "", "not_reported", "none", "not_applicable", "null", "n/a", "none_reported",
])

# transpilation_level values that count as "a transpilation step was reported".
_TRANSPILED = frozenset([
    "hardware_aware_mapping", "pulse_level_optimisation", "default_transpilation",
])

# Substrate names that appear in the free-text hardware_vendor field but have NO
# token in the hardware_modality enum. Matched with an unambiguous token regex
# (NOT general vendor parsing), so a REAL run whose platform is named only in the
# vendor string is classified at substrate granularity instead of being called
# "unspecified". NMR is the only such substrate in the current corpus; this tuple
# is the single extension point if a future corpus names another.
_VENDOR_SUBSTRATE_RES = (
    (re.compile(r"\bnmr\b|nuclear magnetic resonance"), "nmr"),
)


# ----------------------------------------------------------------------------
# Value accessors (mirror compute_derived._v / paradigm_subcategory semantics)
# ----------------------------------------------------------------------------
def _v(passes, pass_letter, field, default=None):
    p = passes.get(pass_letter, {}) if passes else {}
    entry = p.get(field)
    if entry is None:
        return default
    if isinstance(entry, dict):
        return entry.get("value", default)
    if isinstance(entry, list):
        return [it.get("value") if isinstance(it, dict) else it for it in entry]
    return entry


def _scalar_lower(passes, pass_letter, field):
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val).strip().lower()


def _scalar_raw(passes, pass_letter, field):
    """Raw (un-lowercased) scalar, for provenance."""
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val).strip()


def _is_absent(token_lower):
    return token_lower in _ABSENCE


def _vendor_substrate(passes):
    """If hardware_vendor names a known substrate that has no hardware_modality
    token, return that substrate label; else None. Only NMR fires currently."""
    hv = _scalar_lower(passes, "B", "hardware_vendor")
    if _is_absent(hv):
        return None
    for rx, label in _VENDOR_SUBSTRATE_RES:
        if rx.search(hv):
            return label
    return None


# ----------------------------------------------------------------------------
# Facet + basis builders
# ----------------------------------------------------------------------------
def _facets(passes):
    ros = _scalar_lower(passes, "B", "real_or_simulator") or "not_reported"
    fw = _scalar_lower(passes, "B", "simulator_framework")
    tl = _scalar_lower(passes, "B", "transpilation_level")
    tokens = ["mode={}".format(ros if ros else "not_reported")]
    if not _is_absent(fw):
        tokens.append("framework={}".format(fw))
    tokens.append("transpiled={}".format("yes" if tl in _TRANSPILED else "nr"))
    return "; ".join(tokens)


def _basis(passes, primary, hm_lower, ros_lower):
    hv = _scalar_raw(passes, "B", "hardware_vendor")
    fw = _scalar_raw(passes, "B", "simulator_framework")
    tl = _scalar_raw(passes, "B", "transpilation_level")
    head = "{}<-hardware_modality={}; real_or_simulator={}".format(
        primary, hm_lower or "absent", ros_lower or "absent")
    extras = []
    if hv and hv.strip().lower() not in _ABSENCE:
        extras.append("vendor={}".format(hv))
    extras.append("framework={}".format(fw if fw else "absent"))
    extras.append("transpilation_level={}".format(tl if tl else "absent"))
    return head + " | " + "; ".join(extras)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def derive_hardware_execution_fields(passes):
    """Compute the three hardware-execution derived fields for one paper.

    Returns a dict of three {"value": ..., "_note": ...} wrappers keyed:
      hardware_execution_subcategory,
      hardware_execution_facets,
      hardware_execution_basis.
    """
    hm = _scalar_lower(passes, "B", "hardware_modality")
    ros = _scalar_lower(passes, "B", "real_or_simulator")
    vsub = _vendor_substrate(passes) if ros in ("real", "both") else None

    if hm in PLATFORM_LABELS:
        primary = PLATFORM_LABELS[hm]
        note = "platform from hardware_modality={}".format(hm)
    elif vsub:
        primary = vsub
        note = ("platform recovered from hardware_vendor (substrate={}); not a "
                "hardware_modality enum token".format(vsub))
    elif ros == "simulator":
        primary = "simulator_only"
        note = "no named platform; real_or_simulator=simulator"
    elif ros in ("real", "both"):
        primary = "real_hardware_unspecified"
        note = ("real-hardware run (real_or_simulator={}); platform named in "
                "neither hardware_modality nor hardware_vendor".format(ros))
    else:
        primary = "not_reported"
        note = "no platform and no execution mode reported"

    facets = _facets(passes)
    basis = _basis(passes, primary, hm, ros)

    return {
        "hardware_execution_subcategory": {"value": primary, "_note": note},
        "hardware_execution_facets": {"value": facets, "_note": note},
        "hardware_execution_basis": {"value": basis, "_note": note},
    }


# ----------------------------------------------------------------------------
# CLI: --selftest, or scan a merged jsonl for a distribution report
# ----------------------------------------------------------------------------
def _selftest():
    results = []

    def chk(name, passes, exp_primary, must_contain_facet=None):
        out = derive_hardware_execution_fields(passes)
        got = out["hardware_execution_subcategory"]["value"]
        ok = got == exp_primary
        if must_contain_facet is not None:
            ok = ok and (must_contain_facet in out["hardware_execution_facets"]["value"])
        print("  {:32s} got={:26s} exp={:26s} {}".format(
            name, got, exp_primary, "PASS" if ok else "FAIL"))
        results.append(ok)

    # 1. superconducting gate platform (both mode) -> gate_superconducting
    chk("gate_superconducting", {"B": {
        "real_or_simulator": {"value": "both"},
        "hardware_modality": {"value": "superconducting"},
        "hardware_vendor": {"value": "IBM Brisbane (127 qubits)"},
        "simulator_framework": {"value": "Qiskit_Aer"},
        "transpilation_level": {"value": "hardware_aware_mapping"}}},
        "gate_superconducting", must_contain_facet="transpiled=yes")

    # 2. annealing (real) -> annealing (NOT folded into superconducting)
    chk("annealing", {"B": {
        "real_or_simulator": {"value": "real"},
        "hardware_modality": {"value": "annealing"},
        "hardware_vendor": {"value": "D-Wave Advantage"},
        "simulator_framework": {"value": "not_reported"},
        "transpilation_level": {"value": "none_reported"}}},
        "annealing", must_contain_facet="mode=real")

    # 3. trapped_ion (both) -> trapped_ion
    chk("trapped_ion", {"B": {
        "real_or_simulator": {"value": "both"},
        "hardware_modality": {"value": "trapped_ion"},
        "simulator_framework": {"value": "other"}}},
        "trapped_ion")

    # 4. photonic (real) -> photonic
    chk("photonic", {"B": {
        "real_or_simulator": {"value": "real"},
        "hardware_modality": {"value": "photonic"}}},
        "photonic")

    # 5. simulator + simulator_only -> simulator_only, framework facet present
    chk("simulator_only", {"B": {
        "real_or_simulator": {"value": "simulator"},
        "hardware_modality": {"value": "simulator_only"},
        "simulator_framework": {"value": "PennyLane"},
        "transpilation_level": {"value": "none_reported"}}},
        "simulator_only", must_contain_facet="framework=pennylane")

    # 6. simulator + hardware_modality not_reported -> still simulator_only
    chk("simulator_only (hm nr)", {"B": {
        "real_or_simulator": {"value": "simulator"},
        "hardware_modality": {"value": "not_reported"}}},
        "simulator_only")

    # 7. both + hardware_modality not_reported, vendor names IBMQ/IonQ but is NOT
    #    a clean substrate token -> stays real_hardware_unspecified (we do not
    #    fuzzy-map vendor brand strings; only unambiguous substrate tokens recover)
    chk("real_hw_unspecified (both)", {"B": {
        "real_or_simulator": {"value": "both"},
        "hardware_modality": {"value": "not_reported"},
        "hardware_vendor": {"value": "16-qubit IBMQ machine and IonQ Aria1"}}},
        "real_hardware_unspecified")

    # 8. real + hardware_modality not_reported -> real_hardware_unspecified
    chk("real_hw_unspecified (real)", {"B": {
        "real_or_simulator": {"value": "real"},
        "hardware_modality": {"value": "not_reported"}}},
        "real_hardware_unspecified")

    # 9. nothing reported -> not_reported
    chk("not_reported", {"B": {
        "real_or_simulator": {"value": "not_reported"},
        "hardware_modality": {"value": "not_reported"}}},
        "not_reported")

    # 10. real run, platform only in vendor as NMR -> nmr (vendor-substrate recovery)
    chk("nmr (vendor recovery)", {"B": {
        "real_or_simulator": {"value": "real"},
        "hardware_modality": {"value": "not_reported"},
        "hardware_vendor": {"value": "Bruker 600 MHz spectrometer (NMR)"},
        "transpilation_level": {"value": "pulse_level_optimisation"}}},
        "nmr")

    # 11. simulator run whose vendor cites real HW as a noise-model source stays
    #     simulator_only (recovery is gated to real/both)
    chk("simulator_only (noise-model vendor)", {"B": {
        "real_or_simulator": {"value": "simulator"},
        "hardware_modality": {"value": "not_reported"},
        "hardware_vendor": {"value": "ibmq_falcon backend (noise model only; simulation)"}}},
        "simulator_only")

    # facet: transpiled=nr when transpilation absent
    out = derive_hardware_execution_fields({"B": {
        "real_or_simulator": {"value": "simulator"},
        "hardware_modality": {"value": "simulator_only"}}})
    ok = "transpiled=nr" in out["hardware_execution_facets"]["value"]
    print("  {:32s} {}".format("facet transpiled=nr default", "PASS" if ok else "FAIL"))
    results.append(ok)

    allok = all(results)
    print("hardware_execution selftest:", "ALL PASS" if allok else "FAILURES ABOVE")
    return 0 if allok else 1


def _scan(path):
    from collections import Counter
    prim = Counter()
    unspecified = []
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            passes = rec.get("passes", {})
            out = derive_hardware_execution_fields(passes)
            p = out["hardware_execution_subcategory"]["value"]
            prim[p] += 1
            n += 1
            if p == "real_hardware_unspecified":
                unspecified.append((rec.get("doi", ""),
                                    out["hardware_execution_basis"]["value"]))
    print("papers: {}".format(n))
    print("hardware_execution_subcategory distribution:")
    for k, v in prim.most_common():
        print("  {:4d}  {}".format(v, k))
    if unspecified:
        print("\nreal_hardware_unspecified rows (verify vendor for a possible platform):")
        for doi, basis in unspecified:
            print("  {}\n      {}".format(doi, basis))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Hardware-execution subcategorisation deriver.")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--scan", default="",
                    help="Path to extractions_merged.jsonl for a distribution report.")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if args.scan:
        return _scan(args.scan)
    print("Nothing to do. Use --selftest or --scan <merged.jsonl>.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
