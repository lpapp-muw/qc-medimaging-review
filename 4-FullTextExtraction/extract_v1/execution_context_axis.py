"""
execution_context_axis.py - Registered quantum-hardware execution context
(OSF v0.6 sec 7.6, Axis D), assembled deterministically from already-extracted
Pass-B fields. No LLM, raw fields untouched.

PROTOCOL CONFORMANCE
--------------------
sec 7.6 Axis D = execution context {ideal simulator, noisy simulator, real QPU}
combined with compilation level {none reported, default transpilation,
hardware-aware mapping, pulse-level optimisation}, vendor and qubit modality
reported separately. Three of those four dimensions are ALREADY first-class
extracted columns and need no new field:
  - compilation level   = transpilation_level   (Pass B; enum is an exact match)
  - vendor              = hardware_vendor        (Pass B)
  - qubit modality      = hardware_modality      (Pass B)
The only missing dimension is the ideal-vs-noisy split of the simulator branch,
which the schema does not capture as a dedicated field. This module derives it
and emits the registered three-way execution_context plus a provenance string
that also echoes the three existing columns so Axis D reads as one block.

ideal-vs-noisy heuristic (decision D3)
--------------------------------------
  real / both        -> real_qpu
  simulator AND a noise signal is present (error_mitigation_type is a named,
    non-none strategy)                              -> noisy_simulator
  simulator otherwise                               -> ideal_or_unspecified_simulator
  no real_or_simulator value                        -> not_reported
This is a heuristic: noise reporting is not a dedicated extracted field, so a
simulator paper that ran with a noise model but reported no error mitigation will
read as ideal_or_unspecified. The basis string records the signal used, so every
call is auditable and re-classifiable.

Outputs (two derived {"value":..., "_note":...} wrappers):
  execution_context        ideal_or_unspecified_simulator / noisy_simulator /
                           real_qpu / not_reported
  execution_context_basis  the signal used, plus vendor / qubit-modality /
                           compilation passthrough for the full Axis D readout

Python 3.8 compatible.

Usage:
  python3 execution_context_axis.py --selftest
  python3 execution_context_axis.py --scan extractions_merged.jsonl
"""

import argparse
import json
import sys


# Named, non-trivial error-mitigation strategies -> presence implies device noise.
_NAMED_MITIGATION = frozenset([
    "readout_only", "ZNE", "PEC", "dynamical_decoupling", "T_REx", "CDR", "composite",
])

EXECUTION_CONTEXT_VOCAB = (
    "ideal_or_unspecified_simulator", "noisy_simulator", "real_qpu", "not_reported",
)

_ABSENCE = frozenset(["", "not_reported", "none", "not_applicable", "null", "n/a"])


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
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val).strip()


def derive_execution_context_fields(passes):
    """Compute the registered Axis D execution_context (+ basis) for one paper.

    Returns a dict of two {"value": ..., "_note": ...} wrappers keyed:
      execution_context, execution_context_basis.
    """
    ros = _scalar_lower(passes, "B", "real_or_simulator")
    emt = _scalar_raw(passes, "B", "error_mitigation_type")
    vendor = _scalar_raw(passes, "B", "hardware_vendor")
    modality = _scalar_raw(passes, "B", "hardware_modality")
    compilation = _scalar_raw(passes, "B", "transpilation_level")

    if ros in ("real", "both"):
        ctx = "real_qpu"
        signal = "real_or_simulator={}".format(ros)
    elif ros == "simulator":
        if emt in _NAMED_MITIGATION:
            ctx = "noisy_simulator"
            signal = "real_or_simulator=simulator; noise signal error_mitigation_type={}".format(emt)
        else:
            ctx = "ideal_or_unspecified_simulator"
            signal = "real_or_simulator=simulator; no noise signal (error_mitigation_type={})".format(
                emt or "absent")
    else:
        ctx = "not_reported"
        signal = "real_or_simulator absent/unexpected ({})".format(ros or "absent")

    basis = "{} | vendor={}; qubit_modality={}; compilation_level={}".format(
        signal,
        vendor if vendor and vendor.lower() not in _ABSENCE else "not_reported",
        modality if modality and modality.lower() not in _ABSENCE else "not_reported",
        compilation if compilation and compilation.lower() not in _ABSENCE else "not_reported",
    )

    return {
        "execution_context": {"value": ctx, "_note": "registered Axis D execution context (D3 heuristic)"},
        "execution_context_basis": {"value": basis, "_note": "Axis D signal + vendor/modality/compilation"},
    }


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _selftest():
    results = []

    def chk(name, passes, exp_ctx):
        out = derive_execution_context_fields(passes)
        got = out["execution_context"]["value"]
        ok = got == exp_ctx
        print("  {:40s} ctx={:32s} {}".format(
            name, got, "PASS" if ok else "FAIL (exp {})".format(exp_ctx)))
        results.append(ok)

    def B(ros, emt="none_reported", vendor="", modality="", comp=""):
        d = {"B": {"real_or_simulator": {"value": ros},
                   "error_mitigation_type": {"value": emt}}}
        if vendor:
            d["B"]["hardware_vendor"] = {"value": vendor}
        if modality:
            d["B"]["hardware_modality"] = {"value": modality}
        if comp:
            d["B"]["transpilation_level"] = {"value": comp}
        return d

    chk("real -> real_qpu", B("real", "none_reported", "IBM", "superconducting"), "real_qpu")
    chk("both -> real_qpu", B("both", "ZNE", "IonQ", "trapped_ion"), "real_qpu")
    chk("simulator + ZNE -> noisy", B("simulator", "ZNE"), "noisy_simulator")
    chk("simulator + readout_only -> noisy", B("simulator", "readout_only"), "noisy_simulator")
    chk("simulator + none -> ideal/unspec", B("simulator", "none_reported"), "ideal_or_unspecified_simulator")
    chk("simulator + absent emt -> ideal/unspec",
        {"B": {"real_or_simulator": {"value": "simulator"}}}, "ideal_or_unspecified_simulator")
    chk("absent ros -> not_reported", {"B": {}}, "not_reported")

    # basis echoes the three passthrough dimensions
    out = derive_execution_context_fields(B("real", "ZNE", "IBM", "superconducting", "default_transpilation"))
    b = out["execution_context_basis"]["value"]
    ok = ("vendor=IBM" in b and "qubit_modality=superconducting" in b
          and "compilation_level=default_transpilation" in b)
    print("  {:40s} {}".format("basis echoes vendor/modality/compilation", "PASS" if ok else "FAIL"))
    results.append(ok)

    # output is always in the registered vocabulary
    for ros in ("real", "both", "simulator", "", "weird"):
        assert derive_execution_context_fields(B(ros))["execution_context"]["value"] in EXECUTION_CONTEXT_VOCAB

    allok = all(results)
    print("execution_context_axis selftest:", "ALL PASS" if allok else "FAILURES ABOVE")
    return 0 if allok else 1


def _scan(path):
    from collections import Counter
    ctx = Counter()
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out = derive_execution_context_fields(rec.get("passes", {}))
            ctx[out["execution_context"]["value"]] += 1
            n += 1
    print("papers: {}".format(n))
    print("execution_context (REGISTERED) distribution:")
    for k, v in ctx.most_common():
        print("  {:4d}  {}".format(v, k))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Registered Axis D execution-context deriver.")
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
