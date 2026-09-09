"""
compute_derived.py — Deterministic computation of the 9 derived fields.

Reads extractions_merged.jsonl (consolidated A+B+C+D per paper) and writes
extractions_derived.jsonl with the derived fields added under a "derived" block,
each carrying a transparent provenance note. NO AI; pure rule application.

Derivation rules (anchored to OSF protocol v0.6):

§9.3 ionising_flag
  Ionising = {PET, SPECT, CT, photon-counting CT, planar X-ray, mammography,
  fluoroscopy, gamma camera, scintigraphy, Compton camera, proton CT,
  proton radiography, EPID, in-vivo dosimetric imaging}.
  Non-ionising = {MRI, ultrasound, OCT, fundus, histopathology, dermoscopy,
  microscopy}. Derived from modality_primary (and modality_secondary if present:
  ionising if EITHER modality is ionising).

multimodal
  True iff modality_secondary is non-null.

§9.4 reproducibility_tier (refinement of code/data/weights booleans)
  none / code released only / data released only /
  weights or circuit parameters released only / code + data + weights.
  Mapping:
    code & data & weights         -> code_plus_data_plus_weights
    code only                     -> code_only
    data only                     -> data_only
    weights only                  -> weights_or_circuit_params_only
    none / other partial combos   -> none  (per protocol's 5-level scheme;
                                     mixed partials that are not one of the
                                     named singletons collapse to the closest
                                     lower tier and are flagged)

§9.5 clinical_translation_readiness (0-4)
  sum of: sample_size_justification_reported, external_validation_used,
  regulatory_pathway_addressed, prospective_clinical_present.

§10 quantum_resource_accounting_completeness (0-4)
  (qubit_count reported) + (circuit_depth reported) + (gate_count reported)
  + (shot_count reported). "reported" = not null-equivalent in Pass B.

§10 error_mitigation_clarity
  high  if a named non-trivial strategy is identifiable
        (error_mitigation_type in {ZNE,PEC,dynamical_decoupling,T_REx,CDR,
         composite,readout_only});
  low   if only generic mention (strategy text present but type == none_reported
        OR strategy text is non-null but unclassified);
  none  if error_mitigation_type == none_reported AND strategy null.

§10 dataset_realism_rob (toy / benchmark / real_clinical)
  from Pass C dataset_realism_grade:
    toy                         -> toy           (protocol §10 label 'toy')
    public_benchmark            -> benchmark
    private_single_centre,
    private_multi_centre,
    prospective_clinical        -> real_clinical
  (Protocol §10 vocabulary is toy / benchmark / real clinical.)

§10 honest_resource_reporting (bool)
  real_vs_sim_explicit (from Pass D evidence) AND QRA completeness >= 3.

§10 summary_methodological_quality (low/moderate/high concern)
  CONFIRMED RUBRIC (thresholds delegated by protocol; to be logged as a
  Transparent Change in the OSF record):
    concern signals counted across:
      - baseline_rigour_grade in {none, weak}
      - dataset_realism_rob == toy
      - quantum_resource_accounting_completeness < 2
      - honest_resource_reporting == False
      - reproducibility_tier == none
      - statistical_testing_present == False
      - external_validation (Pass A external_validation_used
        OR Pass C external_test_set) == False
    low_concern    : 0-2 signals
    moderate_concern: 3-4 signals
    high_concern   : 5+ signals

Python 3.8 compatible.

Usage:
  python3 compute_derived.py            # merged -> derived
  python3 compute_derived.py --selftest
"""

import argparse
import json
import sys

import paths_step4 as P
from validate_extraction import is_null_equivalent
from paradigm_subcategory import derive_paradigm_fields
from hardware_execution import derive_hardware_execution_fields
from modality_axis import derive_modality_fields
from imaging_task_axis import derive_imaging_task_fields
from execution_context_axis import derive_execution_context_fields
from paradigm_axis_b import derive_paradigm_axis_b


# ----------------------------------------------------------------------------
# Modality ionising lookup (schema MODALITY_ENUM uses these tokens)
# ----------------------------------------------------------------------------
IONISING = frozenset(["CT", "PET", "SPECT", "X-ray"])
NON_IONISING = frozenset(["MRI", "ultrasound", "fundus", "OCT",
                          "histopathology", "dermoscopy", "microscopy"])
# 'other' -> unknown; treated as non-ionising but flagged.


# ----------------------------------------------------------------------------
# Accessors
# ----------------------------------------------------------------------------
def _v(passes, pass_letter, field, default=None):
    """Return the scalar value of a field in a given merged pass record."""
    p = passes.get(pass_letter, {})
    entry = p.get(field)
    if entry is None:
        return default
    if isinstance(entry, dict):
        return entry.get("value", default)
    if isinstance(entry, list):
        return [it.get("value") if isinstance(it, dict) else it for it in entry]
    return entry


def _reported(passes, pass_letter, field):
    val = _v(passes, pass_letter, field)
    return not is_null_equivalent(val)


# ----------------------------------------------------------------------------
# Individual derivations
# ----------------------------------------------------------------------------
def derive_ionising_flag(passes):
    mods = []
    mp = _v(passes, "A", "modality_primary")
    ms = _v(passes, "A", "modality_secondary")
    for m in (mp, ms):
        if m and not is_null_equivalent(m):
            mods.append(m)
    if not mods:
        return {"value": None, "_note": "no modality reported"}
    ionising = any(m in IONISING for m in mods)
    unknown = any(m == "other" for m in mods)
    note = "modalities={}".format(mods)
    if unknown and not ionising:
        note += "; contains 'other' (ionising status uncertain, treated non-ionising)"
    return {"value": bool(ionising), "_note": note}


def derive_multimodal(passes):
    ms = _v(passes, "A", "modality_secondary")
    return {"value": not is_null_equivalent(ms), "_note": "modality_secondary={}".format(ms)}


def derive_reproducibility_tier(passes):
    code = _v(passes, "C", "code_release") is True
    data = _v(passes, "C", "data_release") is True
    weights = _v(passes, "C", "weights_release") is True
    flagged = None
    if code and data and weights:
        tier = "code_plus_data_plus_weights"
    elif code and not data and not weights:
        tier = "code_only"
    elif data and not code and not weights:
        tier = "data_only"
    elif weights and not code and not data:
        tier = "weights_or_circuit_params_only"
    elif not (code or data or weights):
        tier = "none"
    else:
        # mixed partial not matching a named singleton (e.g. code+data but no
        # weights). Collapse to the closest named lower tier and flag.
        if code and data:
            tier = "code_only"  # nearest named singleton; flagged
        elif code and weights:
            tier = "code_only"
        elif data and weights:
            tier = "data_only"
        else:
            tier = "none"
        flagged = "mixed_partial(code={},data={},weights={})".format(code, data, weights)
    out = {"value": tier, "_note": "code={} data={} weights={}".format(code, data, weights)}
    if flagged:
        out["_partial_collapse"] = flagged
    return out


def derive_clinical_translation_readiness(passes):
    bits = [
        _v(passes, "A", "sample_size_justification_reported") is True,
        _v(passes, "A", "external_validation_used") is True,
        _v(passes, "A", "regulatory_pathway_addressed") is True,
        _v(passes, "A", "prospective_clinical_present") is True,
    ]
    score = sum(1 for b in bits if b)
    return {"value": score, "_note": "ss_just={} ext_val={} reg={} prosp={}".format(*bits)}


def derive_qra_completeness(passes):
    bits = [
        _reported(passes, "B", "qubit_count"),
        _reported(passes, "B", "circuit_depth"),
        _reported(passes, "B", "gate_count"),
        _reported(passes, "B", "shot_count"),
    ]
    score = sum(1 for b in bits if b)
    return {"value": score, "_note": "qubit={} depth={} gates={} shots={}".format(*bits)}


def derive_error_mitigation_clarity(passes):
    emt = _v(passes, "B", "error_mitigation_type")
    ems = _v(passes, "B", "error_mitigation_strategy")
    named = {"readout_only", "ZNE", "PEC", "dynamical_decoupling",
             "T_REx", "CDR", "composite"}
    if emt in named:
        val = "high"
    elif emt == "none_reported" and is_null_equivalent(ems):
        val = "none"
    else:
        val = "low"
    return {"value": val, "_note": "type={} strategy_present={}".format(emt, not is_null_equivalent(ems))}


def derive_dataset_realism_rob(passes):
    grade = _v(passes, "C", "dataset_realism_grade")
    mapping = {
        "toy": "toy",
        "public_benchmark": "benchmark",
        "private_single_centre": "real_clinical",
        "private_multi_centre": "real_clinical",
        "prospective_clinical": "real_clinical",
    }
    val = mapping.get(grade)
    return {"value": val, "_note": "from dataset_realism_grade={}".format(grade)}


def derive_honest_resource_reporting(passes, qra_score):
    d = passes.get("D", {})
    evid = d.get("honest_resource_reporting_evidence", {})
    rvs = evid.get("real_vs_sim_explicit", {})
    rvs_val = rvs.get("value") if isinstance(rvs, dict) else None
    val = bool(rvs_val) and (qra_score >= 3)
    return {"value": val, "_note": "real_vs_sim_explicit={} qra={}".format(rvs_val, qra_score)}


def derive_summary_quality(passes, derived):
    signals = []
    if _v(passes, "C", "baseline_rigour_grade") in ("none", "weak"):
        signals.append("weak_baseline")
    if derived["dataset_realism_rob"]["value"] == "toy":
        signals.append("toy_data")
    if derived["quantum_resource_accounting_completeness"]["value"] < 2:
        signals.append("low_qra")
    if derived["honest_resource_reporting"]["value"] is False:
        signals.append("resource_reporting_concern")
    if derived["reproducibility_tier"]["value"] == "none":
        signals.append("no_reproducibility")
    if _v(passes, "C", "statistical_testing_present") is not True:
        signals.append("no_stats_test")
    ext = (_v(passes, "A", "external_validation_used") is True) or \
          (_v(passes, "C", "external_test_set") is True)
    if not ext:
        signals.append("no_external_validation")

    n = len(signals)
    if n <= 2:
        val = "low_concern"
    elif n <= 4:
        val = "moderate_concern"
    else:
        val = "high_concern"
    return {"value": val, "_note": "{} concern signal(s): {}".format(n, signals)}


# ----------------------------------------------------------------------------
# Orchestration
# ----------------------------------------------------------------------------
def compute_for_paper(record):
    passes = record.get("passes", {})
    derived = {}
    derived["ionising_flag"] = derive_ionising_flag(passes)
    derived["multimodal"] = derive_multimodal(passes)
    derived["reproducibility_tier"] = derive_reproducibility_tier(passes)
    derived["clinical_translation_readiness"] = derive_clinical_translation_readiness(passes)
    derived["quantum_resource_accounting_completeness"] = derive_qra_completeness(passes)
    derived["error_mitigation_clarity"] = derive_error_mitigation_clarity(passes)
    derived["dataset_realism_rob"] = derive_dataset_realism_rob(passes)
    derived["honest_resource_reporting"] = derive_honest_resource_reporting(
        passes, derived["quantum_resource_accounting_completeness"]["value"])
    derived["summary_methodological_quality"] = derive_summary_quality(passes, derived)
    # Paradigm subcategorisation: resolve Pass-B paradigm=="other" into a finer,
    # grounded label (raw paradigm untouched). Adds three derived fields:
    # paradigm_subcategory / paradigm_facets / paradigm_resolution_basis.
    derived.update(derive_paradigm_fields(passes))
    # Hardware-execution subcategorisation: single primary platform label plus
    # facets and provenance, from already-extracted Pass-B fields (no LLM, raw
    # fields untouched). Adds three derived fields:
    # hardware_execution_subcategory / hardware_execution_facets /
    # hardware_execution_basis.
    derived.update(derive_hardware_execution_fields(passes))
    # Imaging-modality subcategorisation: resolved primary modality + family,
    # from Pass-A modality fields and dataset_name (no LLM, raw fields untouched).
    # Adds: modality_subcategory / modality_family / modality_basis.
    derived.update(derive_modality_fields(passes))
    # Imaging-task subcategorisation (OSF v0.6 Axis A): registered single primary
    # tag + multi-label tag set, plus a finer subcategory and family alongside,
    # from Pass-A imaging_task/title/modality_secondary (no LLM, raw untouched).
    # Adds: task_axis_a / imaging_task_tags / task_subcategory / task_family /
    # task_basis.
    derived.update(derive_imaging_task_fields(passes))
    # Registered Axis D execution context (ideal/noisy/real) from Pass-B
    # real_or_simulator + error_mitigation_type, with vendor / qubit-modality /
    # compilation echoed in the basis (those three are already first-class
    # columns). Adds: execution_context / execution_context_basis.
    derived.update(derive_execution_context_fields(passes))
    # Registered Axis B paradigm tag (9-value), rolled up deterministically from
    # paradigm_subcategory computed just above. Adds: paradigm_axis_b.
    derived["paradigm_axis_b"] = derive_paradigm_axis_b(
        derived["paradigm_subcategory"]["value"])
    record["derived"] = derived
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not P.MERGED_EXTRACTIONS.exists():
        print("ERROR: {} missing. Run merge_passes.py --consolidate first.".format(
            P.MERGED_EXTRACTIONS), file=sys.stderr)
        return 2

    n = 0
    with open(P.MERGED_EXTRACTIONS, encoding="utf-8") as fin, \
         open(P.DERIVED_EXTRACTIONS, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rec = compute_for_paper(rec)
            fout.write(json.dumps(rec) + "\n")
            n += 1
    print("Computed derived fields for {} paper(s) -> {}".format(n, P.DERIVED_EXTRACTIONS))
    return 0


def _selftest():
    rec = {
        "stable_name": "paperX", "doi": "10.x/y",
        "passes": {
            "A": {
                "modality_primary": {"value": "PET"},
                "modality_secondary": {"value": None},
                "sample_size_justification_reported": {"value": False},
                "external_validation_used": {"value": True},
                "regulatory_pathway_addressed": {"value": False},
                "prospective_clinical_present": {"value": False},
            },
            "B": {
                "qubit_count": {"value": "6 qubits"},
                "circuit_depth": {"value": "depth 4"},
                "gate_count": {"value": "not_reported"},
                "shot_count": {"value": "4096 shots"},
                "error_mitigation_type": {"value": "readout_only"},
                "error_mitigation_strategy": {"value": "matrix inversion"},
            },
            "C": {
                "code_release": {"value": True},
                "data_release": {"value": False},
                "weights_release": {"value": False},
                "baseline_rigour_grade": {"value": "matched_data_compute_stats"},
                "dataset_realism_grade": {"value": "public_benchmark"},
                "statistical_testing_present": {"value": True},
                "external_test_set": {"value": True},
            },
            "D": {
                "honest_resource_reporting_evidence": {
                    "real_vs_sim_explicit": {"value": True, "quote": "ran on ibmq"}
                }
            },
        }
    }
    out = compute_for_paper(rec)["derived"]
    checks = [
        ("ionising_flag", out["ionising_flag"]["value"], True),
        ("multimodal", out["multimodal"]["value"], False),
        ("reproducibility_tier", out["reproducibility_tier"]["value"], "code_only"),
        ("clinical_translation_readiness", out["clinical_translation_readiness"]["value"], 1),
        ("quantum_resource_accounting_completeness", out["quantum_resource_accounting_completeness"]["value"], 3),
        ("error_mitigation_clarity", out["error_mitigation_clarity"]["value"], "high"),
        ("dataset_realism_rob", out["dataset_realism_rob"]["value"], "benchmark"),
        ("honest_resource_reporting", out["honest_resource_reporting"]["value"], True),
    ]
    allok = True
    for name, got, exp in checks:
        ok = got == exp
        allok = allok and ok
        print("  {:42s} got={!r:30s} exp={!r:18s} {}".format(name, got, exp, "PASS" if ok else "FAIL"))
    # summary quality: signals = ss_just false->no? let's see. weak_baseline no,
    # toy no, low_qra no(3), resource concern no(True), no_repro no(code_only),
    # no_stats no(True present), no_ext no(ext True) => 0 signals -> low_concern
    sq = out["summary_methodological_quality"]["value"]
    print("  {:42s} got={!r:30s} exp={!r:18s} {}".format(
        "summary_methodological_quality", sq, "low_concern",
        "PASS" if sq == "low_concern" else "FAIL"))
    print("SELFTEST:", "ALL PASS" if (allok and sq == "low_concern") else "FAILURES ABOVE")


if __name__ == "__main__":
    sys.exit(main())
