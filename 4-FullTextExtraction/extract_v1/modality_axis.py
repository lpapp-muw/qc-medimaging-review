"""
modality_axis.py - Deterministic imaging-modality subcategorisation for the
Step-4 extraction pipeline (IEEE TRPMS systematic review on quantum computing
for medical imaging). Second of the three remaining Step-5 axes (after
hardware_execution.py; the paradigm axis is paradigm_subcategory.py).

Why this module exists
----------------------
Synthesis needs one consistent imaging-modality axis plus a family grouping for
the results table. Named modalities are already clean in Pass-A modality_primary
(MRI, CT, X-ray, dermoscopy, fundus, histopathology, PET, ultrasound); the only
work is (a) resolving the modality_primary == 'other' papers that are genuine
medical imaging but whose modality is named only in the dataset, and (b)
grouping every modality into a family. Deterministic, no LLM, raw fields
untouched.

Resolution of modality_primary == 'other' (dataset_name keywords, ratified M2):
  endoscopy                   WCE / capsule / colonoscopy / Kvasir / CVC-ColonDB
                              / CVC-ClinicDB / endoscopy datasets
  multimodal                  MedMNIST / MedNIST / Medical MNIST collections, or
                              a dataset naming >= 2 distinct imaging modalities
  unspecified_medical_imaging generic "medical image(s)" with no named modality
  (unmatched 'other' -- e.g. excluded non-imaging papers -- stay 'other')

Family map (ratified M1 + M3: PET is its own `nuclear` family):
  radiological_ionising  CT, X-ray, mammography
  nuclear                PET, SPECT
  mr                     MRI
  ultrasound             ultrasound
  optical                dermoscopy, fundus, endoscopy
  microscopy_pathology   histopathology, microscopy
  multimodal             multimodal
  unspecified            unspecified_medical_imaging
  other                  unresolved 'other'

ionising_flag is a SEPARATE existing derived field and is not recomputed here:
it counts any ionising modality used (including ionising secondaries and ionising
subsets inside a multimodal benchmark), so it runs slightly above the
radiological-primary count. The family here is keyed on the primary modality.

Outputs (three derived {"value":..., "_note":...} wrappers, same shape as the
paradigm / hardware trios, so they render in the workbook "Derived
(auto-computed)" group with no schema change):
  modality_subcategory  resolved primary modality (single label)
  modality_family       family grouping
  modality_basis        provenance (modality_primary, the resolution rule and
                        dataset signal for resolved 'other', and any secondary
                        modality)

Python 3.8 compatible (no PEP-604/585 unions, no @dataclass with generic
annotations, no from __future__ import).

Usage:
  python3 modality_axis.py --selftest
  python3 modality_axis.py --scan extractions_merged.jsonl
"""

import argparse
import json
import re
import sys
from collections import OrderedDict


# ----------------------------------------------------------------------------
# Canonical named modalities (modality_primary values normalised to lowercase).
# ----------------------------------------------------------------------------
_NORMALISE = {
    "mri": "mri",
    "ct": "ct",
    "x-ray": "x_ray", "xray": "x_ray", "x ray": "x_ray",
    "pet": "pet",
    "spect": "spect",
    "ultrasound": "ultrasound",
    "dermoscopy": "dermoscopy",
    "fundus": "fundus",
    "histopathology": "histopathology",
    "microscopy": "microscopy",
    "mammography": "mammography",
}

NAMED = frozenset(_NORMALISE.values())

# subcategory -> family
FAMILY_OF = {
    "ct": "radiological_ionising",
    "x_ray": "radiological_ionising",
    "mammography": "radiological_ionising",
    "pet": "nuclear",
    "spect": "nuclear",
    "mri": "mr",
    "ultrasound": "ultrasound",
    "dermoscopy": "optical",
    "fundus": "optical",
    "endoscopy": "optical",
    "histopathology": "microscopy_pathology",
    "microscopy": "microscopy_pathology",
    "multimodal": "multimodal",
    "unspecified_medical_imaging": "unspecified",
    "other": "other",
}

# ---- resolution of modality_primary == 'other' (dataset_name keywords) ----
_ENDOSCOPY_RE = re.compile(
    r"\b(wce|kvasir|colondb|clinicdb|colonoscop|endoscop|capsule endoscop)\b")
_BENCH_MULTI_RE = re.compile(r"medmnist|mednist|medical mnist")
_GENERIC_IMG_RE = re.compile(r"medical image|medical imaging|medical picture")
# distinct imaging-modality tokens, word-bounded for the short/ambiguous ones.
_MODALITY_TOKEN_RES = [
    re.compile(r"\bct\b"), re.compile(r"\bmri\b"), re.compile(r"\bpet\b"),
    re.compile(r"\bspect\b"), re.compile(r"\bx-?ray\b"), re.compile(r"ultrasound"),
    re.compile(r"dermoscop"), re.compile(r"fundus"), re.compile(r"histopath"),
    re.compile(r"microscop"), re.compile(r"mammograph"), re.compile(r"\boct\b"),
]


# ----------------------------------------------------------------------------
# Value accessors (mirror paradigm_subcategory / hardware_execution semantics)
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


def _all_lower(passes, pass_letter, field):
    """All values of a (possibly multi-value) field, joined and lowercased."""
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        return " ; ".join(str(x) for x in val if x is not None).strip().lower()
    return str(val).strip().lower()


def _all_raw(passes, pass_letter, field):
    val = _v(passes, pass_letter, field)
    if val is None:
        return ""
    if isinstance(val, list):
        return " ; ".join(str(x) for x in val if x is not None).strip()
    return str(val).strip()


_ABSENCE = frozenset(["", "not_reported", "none", "not_applicable", "null", "n/a"])


def _resolve_other(dataset_name_lower):
    """Resolve a modality_primary=='other' paper from its dataset_name.
    Returns a subcategory label, or None to leave it as raw 'other'."""
    dn = dataset_name_lower
    if not dn:
        return None
    if _ENDOSCOPY_RE.search(dn):
        return "endoscopy"
    if _BENCH_MULTI_RE.search(dn):
        return "multimodal"
    n_tok = sum(1 for rx in _MODALITY_TOKEN_RES if rx.search(dn))
    if n_tok >= 2:
        return "multimodal"
    if _GENERIC_IMG_RE.search(dn):
        return "unspecified_medical_imaging"
    return None


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------
def derive_modality_fields(passes):
    """Compute the three modality derived fields for one paper.

    Returns a dict of three {"value": ..., "_note": ...} wrappers keyed:
      modality_subcategory, modality_family, modality_basis.
    """
    mp_lower = _scalar_lower(passes, "A", "modality_primary")
    mp = _NORMALISE.get(mp_lower, mp_lower)
    ms_raw = _scalar_raw(passes, "A", "modality_secondary")
    dn_lower = _all_lower(passes, "A", "dataset_name")

    if mp in NAMED:
        subcat = mp
        note = "primary modality from modality_primary={}".format(mp_lower)
        basis_src = "modality_primary={}".format(mp_lower)
    elif mp == "other" or mp == "":
        resolved = _resolve_other(dn_lower)
        if resolved is not None:
            subcat = resolved
            note = "resolved from dataset_name (rule={})".format(resolved)
            basis_src = "modality_primary=other; resolved={}; dataset={}".format(
                resolved, _all_raw(passes, "A", "dataset_name"))
        else:
            subcat = "other"
            note = "modality_primary=other; not resolvable from dataset (left as other)"
            basis_src = "modality_primary=other; dataset={}".format(
                _all_raw(passes, "A", "dataset_name"))
    else:
        # Unexpected named value: keep it, group as unspecified family.
        subcat = mp
        note = "non-enum modality_primary={}; carried verbatim".format(mp_lower)
        basis_src = "modality_primary={}".format(mp_lower)

    family = FAMILY_OF.get(subcat, "unspecified")

    basis = "{} | family={}".format(basis_src, family)
    if ms_raw and ms_raw.strip().lower() not in _ABSENCE:
        basis = basis + "; modality_secondary={}".format(ms_raw)

    return {
        "modality_subcategory": {"value": subcat, "_note": note},
        "modality_family": {"value": family, "_note": note},
        "modality_basis": {"value": basis, "_note": note},
    }


# ----------------------------------------------------------------------------
# CLI: --selftest, or scan a merged jsonl for a distribution report
# ----------------------------------------------------------------------------
def _selftest():
    results = []

    def chk(name, passes, exp_sub, exp_fam):
        out = derive_modality_fields(passes)
        gs = out["modality_subcategory"]["value"]
        gf = out["modality_family"]["value"]
        ok = (gs == exp_sub and gf == exp_fam)
        print("  {:30s} sub={:26s} fam={:22s} {}".format(
            name, gs, gf, "PASS" if ok else "FAIL (exp {}/{})".format(exp_sub, exp_fam)))
        results.append(ok)

    def A(modp, dataset="", mods=""):
        b = {"A": {"modality_primary": {"value": modp}}}
        if dataset:
            b["A"]["dataset_name"] = {"value": dataset}
        if mods:
            b["A"]["modality_secondary"] = {"value": mods}
        return b

    # named modalities inherit, family per M1/M3
    chk("MRI -> mr", A("MRI"), "mri", "mr")
    chk("CT -> radiological_ionising", A("CT"), "ct", "radiological_ionising")
    chk("X-ray -> radiological_ionising", A("X-ray"), "x_ray", "radiological_ionising")
    chk("PET -> nuclear (M3)", A("PET"), "pet", "nuclear")
    chk("ultrasound", A("ultrasound"), "ultrasound", "ultrasound")
    chk("dermoscopy -> optical", A("dermoscopy"), "dermoscopy", "optical")
    chk("fundus -> optical", A("fundus"), "fundus", "optical")
    chk("histopathology -> micro", A("histopathology"), "histopathology", "microscopy_pathology")

    # 'other' resolution (M2)
    chk("other: WCE -> endoscopy",
        A("other", "WCE Curated Colon Disease Dataset;Kvasir"), "endoscopy", "optical")
    chk("other: colonoscopy -> endoscopy",
        A("other", "CVC-ColonDB;CVC-ClinicDB;SRMC real-time"), "endoscopy", "optical")
    chk("other: MedMNIST -> multimodal",
        A("other", "MedMNIST v2 (ChestMNIST, PneumoniaMNIST)"), "multimodal", "multimodal")
    chk("other: MedNIST -> multimodal",
        A("other", "MedNIST"), "multimodal", "multimodal")
    chk("other: multi-token -> multimodal",
        A("other", "Mendeley Medical imaging (CT scan, MRI, X-ray, microscopic)"),
        "multimodal", "multimodal")
    chk("other: generic -> unspecified",
        A("other", "sample of ten medical images (not named)"),
        "unspecified_medical_imaging", "unspecified")
    chk("other: medical image datasets -> unspecified",
        A("other", "medical image datasets"), "unspecified_medical_imaging", "unspecified")
    # unresolvable 'other' (e.g. excluded non-imaging) stays 'other'
    chk("other: EEG dataset -> other",
        A("other", "Temple University Hospital EEG Corpus"), "other", "other")

    # multi-value dataset_name: resolving keyword in a NON-first element
    chk("other: list, Medical MNIST in elem[1]",
        {"A": {"modality_primary": {"value": "other"},
               "dataset_name": [{"value": "MNIST"}, {"value": "Medical MNIST"},
                                {"value": "CIFAR-10"}]}},
        "multimodal", "multimodal")
    chk("other: list, ColonDB in elem[1]",
        {"A": {"modality_primary": {"value": "other"},
               "dataset_name": [{"value": "SRMC real-time (DS-1)"},
                                {"value": "CVC-ColonDB"}, {"value": "CVC-ClinicDB"}]}},
        "endoscopy", "optical")

    # secondary modality carried in basis
    out = derive_modality_fields(A("MRI", "in-house", "CT"))
    ok = "modality_secondary=CT" in out["modality_basis"]["value"]
    print("  {:30s} {}".format("secondary in basis", "PASS" if ok else "FAIL"))
    results.append(ok)

    allok = all(results)
    print("modality_axis selftest:", "ALL PASS" if allok else "FAILURES ABOVE")
    return 0 if allok else 1


def _scan(path):
    from collections import Counter
    sub = Counter()
    fam = Counter()
    n = 0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            out = derive_modality_fields(rec.get("passes", {}))
            sub[out["modality_subcategory"]["value"]] += 1
            fam[out["modality_family"]["value"]] += 1
            n += 1
    print("papers: {}".format(n))
    print("modality_subcategory:")
    for k, v in sub.most_common():
        print("  {:4d}  {}".format(v, k))
    print("modality_family:")
    for k, v in fam.most_common():
        print("  {:4d}  {}".format(v, k))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Imaging-modality subcategorisation deriver.")
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
