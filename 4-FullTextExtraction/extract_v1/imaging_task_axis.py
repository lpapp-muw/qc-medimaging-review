"""
imaging_task_axis.py - Deterministic imaging-task subcategorisation for the
Step-4 extraction pipeline (IEEE TRPMS systematic review on quantum computing
for medical imaging). Third of the Step-5 subcategorisation axes (after
hardware_execution.py and modality_axis.py; the paradigm axis is
paradigm_subcategory.py).

PROTOCOL CONFORMANCE (OSF v0.6 sec 7.6, Axis A)
-----------------------------------------------
The REGISTERED Axis A is the editor's 10-tag, multi-label imaging-task
taxonomy, which is exactly the Pass-A IMAGING_TASK_ENUM minus the catch-all:
  {reconstruction, analysis, segmentation, classification, denoising,
   super_resolution, radiomics, fusion, instrumentation, dosimetry}  (+ other).
This module therefore emits two REGISTERED fields that are authoritative for the
manuscript, plus two FINER refinement fields that sit next to them (more
granularity for subsequent analyses; the registered fields stay protocol-exact):

  REGISTERED (authoritative, protocol Axis A):
    task_axis_a        single primary editor tag (one of the 10 + 'other')
    imaging_task_tags  multi-label tag set: [task_axis_a] plus a 'fusion'
                       co-tag when modality_secondary is populated (decision D2)

  REFINEMENT (alongside; does NOT replace the registered fields):
    task_subcategory   finer single label; the 9 inherited enum tasks plus
                       resolution of the raw {other, analysis} tail into
                       {edge_detection, registration, survival, regression,
                        image_generation, image_enhancement, image_representation,
                        other_method}
    task_family        5-way grouping of task_subcategory

  task_basis           provenance for every field (raw value, resolution rule
                       and title signal, the editor rollup, the family, and any
                       fusion co-tag reason)

Resolution of the raw {other, analysis} tail (title keywords; ratified D1).
Only runs when raw imaging_task is 'other' or 'analysis'; the 9 concrete enum
tasks inherit verbatim, so the keyword rules can never perturb a paper that the
extractor already tagged with a concrete task.

Editor rollup (task_subcategory -> registered task_axis_a, ratified D1):
  classification/segmentation/reconstruction/denoising/radiomics/
    super_resolution/fusion/instrumentation/dosimetry/analysis -> same tag
  edge_detection/registration/survival/regression/other_method  -> analysis
  image_generation/image_enhancement                            -> super_resolution
  image_representation                                          -> other

Deterministic, no LLM, no web, raw fields untouched.

Python 3.8 compatible (no PEP-604/585 unions, no @dataclass with generic
annotations, no from __future__ import).

Usage:
  python3 imaging_task_axis.py --selftest
  python3 imaging_task_axis.py --scan extractions_merged.jsonl
"""

import argparse
import json
import re
import sys


# ----------------------------------------------------------------------------
# Vocabularies
# ----------------------------------------------------------------------------
# Concrete Pass-A IMAGING_TASK_ENUM tasks that inherit verbatim into
# task_subcategory (the catch-alls 'analysis' and 'other' are resolved below).
CONCRETE_TASKS = frozenset([
    "reconstruction", "segmentation", "classification", "denoising",
    "super_resolution", "radiomics", "fusion", "instrumentation", "dosimetry",
])

# Tail resolution priority (first match wins). Specific clinical/analytic tasks
# first, then image-to-image, then representation, then the fallback.
RESOLVE_PRIORITY = (
    "edge_detection",
    "registration",
    "survival",
    "regression",
    "image_generation",
    "image_enhancement",
    "image_representation",
)

# task_subcategory -> registered Axis A editor tag (D1).
AXIS_A_OF = {
    "classification": "classification",
    "segmentation": "segmentation",
    "reconstruction": "reconstruction",
    "denoising": "denoising",
    "radiomics": "radiomics",
    "super_resolution": "super_resolution",
    "fusion": "fusion",
    "instrumentation": "instrumentation",
    "dosimetry": "dosimetry",
    "analysis": "analysis",
    "edge_detection": "analysis",
    "registration": "analysis",
    "survival": "analysis",
    "regression": "analysis",
    "other_method": "analysis",
    "image_generation": "super_resolution",
    "image_enhancement": "super_resolution",
    "image_representation": "other",
    "other": "other",
}

# task_subcategory -> family (T2; gives predictive 99 / image_to_image 16 /
# segmentation 15 / representation 2 / other 3 on the 135 survivors).
FAMILY_OF = {
    "classification": "predictive",
    "survival": "predictive",
    "regression": "predictive",
    "segmentation": "segmentation",
    "reconstruction": "image_to_image",
    "denoising": "image_to_image",
    "super_resolution": "image_to_image",
    "image_generation": "image_to_image",
    "image_enhancement": "image_to_image",
    "registration": "image_to_image",
    "edge_detection": "image_to_image",
    "radiomics": "representation",
    "image_representation": "representation",
    "fusion": "other",
    "instrumentation": "other",
    "dosimetry": "other",
    "analysis": "other",
    "other_method": "other",
    "other": "other",
}

SUBCATEGORY_VOCAB = (
    sorted(CONCRETE_TASKS) + ["analysis", "other"] + list(RESOLVE_PRIORITY)
    + ["other_method"]
)
AXIS_A_VOCAB = (
    "reconstruction", "analysis", "segmentation", "classification", "denoising",
    "super_resolution", "radiomics", "fusion", "instrumentation", "dosimetry",
    "other",
)


# ----------------------------------------------------------------------------
# Tail-resolution title patterns
# ----------------------------------------------------------------------------
# NOTE: 'enhancement' (the noun) is matched, NOT 'enhanced'; this keeps
# "Quantum-Enhanced ... Neural Network" out of image_enhancement.
_PATTERNS = {
    "edge_detection": re.compile(r"edge (?:detection|extraction)"),
    "registration": re.compile(r"registration"),
    "survival": re.compile(r"survival"),
    "regression": re.compile(r"brain age|age prediction|age estimation|\bregression\b"),
    "image_generation": re.compile(r"image generation|generative|image synthesis|\bsynthesis\b"),
    "image_enhancement": re.compile(r"enhancement"),
    "image_representation": re.compile(
        r"quantum image|gray code|lossless quantum|image representation|"
        r"image encoding|preparation of .*quantum"),
}


# ----------------------------------------------------------------------------
# Value accessors (mirror modality_axis / paradigm_subcategory semantics)
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
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        val = val[0] if val else ""
    return str(val).strip()


_ABSENCE = frozenset(["", "not_reported", "none", "not_applicable", "null", "n/a"])


def _resolve_tail(title_lower):
    """Resolve a raw {other, analysis} task from its title. Returns a finer
    label, or None if no pattern fires."""
    if not title_lower:
        return None
    for label in RESOLVE_PRIORITY:
        if _PATTERNS[label].search(title_lower):
            return label
    return None


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def derive_imaging_task_fields(passes):
    """Compute the five imaging-task derived fields for one paper.

    Returns a dict of five {"value": ..., "_note": ...} wrappers keyed:
      task_axis_a, imaging_task_tags, task_subcategory, task_family, task_basis.
    """
    raw = _scalar_lower(passes, "A", "imaging_task")
    title = _scalar_lower(passes, "A", "title")
    ms_raw = _scalar_raw(passes, "A", "modality_secondary")

    # --- task_subcategory (finer) ---
    if raw in CONCRETE_TASKS:
        subcat = raw
        sub_note = "inherited concrete imaging_task={}".format(raw)
        sub_src = "imaging_task={}".format(raw)
    elif raw in ("analysis", "other"):
        resolved = _resolve_tail(title)
        if resolved is not None:
            subcat = resolved
            sub_note = "resolved tail (raw={}, rule={})".format(raw, resolved)
            sub_src = "imaging_task={}; resolved={}; title~{}".format(raw, resolved, resolved)
        else:
            subcat = "analysis" if raw == "analysis" else "other_method"
            sub_note = "raw={}; no title signal -> {}".format(raw, subcat)
            sub_src = "imaging_task={}; unresolved".format(raw)
    else:
        # absent or unexpected value
        subcat = "other_method"
        sub_note = "imaging_task absent/unexpected ({!r}) -> other_method".format(raw)
        sub_src = "imaging_task={}".format(raw or "absent")

    # --- registered Axis A primary + family ---
    axis_a = AXIS_A_OF.get(subcat, "other")
    family = FAMILY_OF.get(subcat, "other")

    # --- registered Axis A multi-label tags (D2: fusion co-tag) ---
    tags = [axis_a]
    fusion_reason = ""
    if ms_raw and ms_raw.strip().lower() not in _ABSENCE:
        if "fusion" not in tags:
            tags.append("fusion")
        fusion_reason = "; +fusion (modality_secondary={})".format(ms_raw)
    tags_value = "; ".join(tags)

    basis = "{} | axis_a={}; family={}; tags=[{}]{}".format(
        sub_src, axis_a, family, tags_value, fusion_reason)

    return {
        "task_axis_a": {"value": axis_a, "_note": "registered Axis A primary"},
        "imaging_task_tags": {"value": tags_value, "_note": "registered Axis A multi-label"},
        "task_subcategory": {"value": subcat, "_note": sub_note},
        "task_family": {"value": family, "_note": sub_note},
        "task_basis": {"value": basis, "_note": sub_note},
    }


# ----------------------------------------------------------------------------
# CLI: --selftest, or scan a merged jsonl for a distribution report
# ----------------------------------------------------------------------------
def _selftest():
    results = []

    def chk(name, passes, exp_sub, exp_axis_a, exp_fam):
        out = derive_imaging_task_fields(passes)
        gs = out["task_subcategory"]["value"]
        ga = out["task_axis_a"]["value"]
        gf = out["task_family"]["value"]
        ok = (gs == exp_sub and ga == exp_axis_a and gf == exp_fam)
        print("  {:34s} sub={:20s} axisA={:15s} fam={:14s} {}".format(
            name, gs, ga, gf,
            "PASS" if ok else "FAIL (exp {}/{}/{})".format(exp_sub, exp_axis_a, exp_fam)))
        results.append(ok)

    def A(task, title="", mods=""):
        b = {"A": {"imaging_task": {"value": task}, "title": {"value": title}}}
        if mods:
            b["A"]["modality_secondary"] = {"value": mods}
        return b

    # inherited concrete tasks
    chk("classification", A("classification"), "classification", "classification", "predictive")
    chk("segmentation", A("segmentation"), "segmentation", "segmentation", "segmentation")
    chk("reconstruction", A("reconstruction"), "reconstruction", "reconstruction", "image_to_image")
    chk("denoising", A("denoising"), "denoising", "denoising", "image_to_image")
    chk("radiomics", A("radiomics"), "radiomics", "radiomics", "representation")

    # ratified D1 tail (13 papers) - one case per finer label
    chk("analysis/edge CT (s13369)",
        A("analysis", "Quantum Edge Extraction of Chest CT Image for Detection"),
        "edge_detection", "analysis", "image_to_image")
    chk("other/edge (s11760)",
        A("other", "Quantum edge detection of medical images using enhanced QRA"),
        "edge_detection", "analysis", "image_to_image")
    chk("other/registration (fdgth)",
        A("other", "Comparison of physics-based deformable registration methods"),
        "registration", "analysis", "image_to_image")
    chk("analysis/survival (s41598)",
        A("analysis", "Survival prediction for bladder cancer using multimodal data"),
        "survival", "analysis", "predictive")
    chk("analysis/regression (brainsci)",
        A("analysis", "Predicting Brain Age and Gender from Brain Volume Data"),
        "regression", "analysis", "predictive")
    chk("other/generation (add1a9)",
        A("other", "Quantum Generative Learning for High-Resolution Medical Image Generation"),
        "image_generation", "super_resolution", "image_to_image")
    chk("other/generation (access.3638383)",
        A("other", "High-Resolution Medical Image Generation With Leak-Prevention Mechanism"),
        "image_generation", "super_resolution", "image_to_image")
    chk("other/enhancement (access.3531407)",
        A("other", "Design of an Iterative Model for Incremental Enhancements in Quantum Image"),
        "image_enhancement", "super_resolution", "image_to_image")
    chk("other/enhancement (access.3686925)",
        A("other", "Quantum Overflow Detection for Reliable Bioinspired Image Enhancement"),
        "image_enhancement", "super_resolution", "image_to_image")
    chk("other/representation (s11128)",
        A("other", "Efficient preparation of lossless quantum images based on Gray code"),
        "image_representation", "other", "representation")
    # generic/methods -> other_method -> analysis. CRITICAL: 'Quantum-Enhanced'
    # must NOT trip image_enhancement (matches 'enhanced', not 'enhancement').
    chk("other/methods enhanced-NN (access.3542807)",
        A("other", "A Quantum-Enhanced Artificial Neural Network Model for Efficient Medical"),
        "other_method", "analysis", "other")
    chk("other/methods broad QML (access.3663498)",
        A("other", "Quantum Machine Learning in Medical Image Analysis From Diagnostics"),
        "other_method", "analysis", "other")
    chk("other/methods control (ejca)",
        A("other", "Unlocking clinical quantum oncology through quantum control"),
        "other_method", "analysis", "other")

    # raw analysis with no signal stays analysis; raw absent -> other_method
    chk("analysis no-signal -> analysis",
        A("analysis", "Some quantum approach to imaging"),
        "analysis", "analysis", "other")
    chk("absent -> other_method", A("", ""), "other_method", "analysis", "other")

    # D2 fusion co-tag from modality_secondary
    out = derive_imaging_task_fields(A("classification", "PET/CT fusion classifier", "CT"))
    ok = out["imaging_task_tags"]["value"] == "classification; fusion"
    print("  {:34s} tags={:24s} {}".format(
        "fusion co-tag (secondary=CT)", out["imaging_task_tags"]["value"],
        "PASS" if ok else "FAIL"))
    results.append(ok)
    # no secondary -> single tag
    out = derive_imaging_task_fields(A("classification", "single modality"))
    ok = out["imaging_task_tags"]["value"] == "classification"
    print("  {:34s} tags={:24s} {}".format(
        "no fusion co-tag (no secondary)", out["imaging_task_tags"]["value"],
        "PASS" if ok else "FAIL"))
    results.append(ok)

    allok = all(results)
    print("imaging_task_axis selftest:", "ALL PASS" if allok else "FAILURES ABOVE")
    return 0 if allok else 1


def _scan(path):
    from collections import Counter
    sub = Counter()
    axis = Counter()
    fam = Counter()
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out = derive_imaging_task_fields(rec.get("passes", {}))
            sub[out["task_subcategory"]["value"]] += 1
            axis[out["task_axis_a"]["value"]] += 1
            fam[out["task_family"]["value"]] += 1
            n += 1
    print("papers: {}".format(n))
    print("task_axis_a (REGISTERED):")
    for k, v in axis.most_common():
        print("  {:4d}  {}".format(v, k))
    print("task_subcategory (refinement):")
    for k, v in sub.most_common():
        print("  {:4d}  {}".format(v, k))
    print("task_family (refinement):")
    for k, v in fam.most_common():
        print("  {:4d}  {}".format(v, k))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Imaging-task subcategorisation deriver.")
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
