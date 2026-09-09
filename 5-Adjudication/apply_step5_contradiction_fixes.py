#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
apply_step5_contradiction_fixes.py

Step-5 high-severity contradiction adjudication. Applies the manually
validated fixes to the merged extraction set and the eligibility cull.

Each edit ASSERTS the current (pre-edit) value before changing it, so the
script halts loudly if the source differs from what was adjudicated. Every
change is recorded in a per-record `_step5_adjudication` provenance list.

Python 3.8 compatible. House style: .format(), no f-strings, no dataclasses.

Revision note (2026-09-09): blocks 9 and 11 previously left s10791 and s11760
unchanged while the committed data (extractions_merged.jsonl and all workbooks)
carried the merits-review grades 'weak' and 'public_benchmark'. The blocks now
apply those grades so that running this script reproduces the committed state.
For radphyschem (block 10) the committed data carries an additional draft entry
('weak', later reverted to 'matched_data' by fix_radphyschem.py); this script
records the final state directly.

Outputs (into --outdir):
  extractions_merged_step5.jsonl        181 records, fixes applied + provenance
  included_step5_v134.csv               134 survivors (ejca removed)
  manual_fulltext_exclusions_v47.csv    manual exclusions + ejca appended
  step5_fix_report.txt                  human-readable change log
"""
import argparse
import csv
import json
import os
import sys

DATE = "2026-06-22"
SRC = "manual full-text validation (Step-5 Pass-E high-severity adjudication)"


class Halt(Exception):
    pass


def expect(cond, msg):
    if not cond:
        raise Halt(msg)


def norm(s):
    return (str(s) if s is not None else "").strip().lower()


def load_jsonl(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def field_entry(rec, pl, f):
    return rec.get("passes", {}).get(pl, {}).get(f)


def wval(entry):
    if isinstance(entry, dict):
        return entry.get("value")
    if isinstance(entry, list):
        return [wval(x) for x in entry]
    return entry


def log_adj(rec, **kw):
    rec.setdefault("_step5_adjudication", []).append(kw)


# ---------------------------------------------------------------------------
# The eleven adjudicated fixes
# ---------------------------------------------------------------------------
def apply_fixes(by_doi, changes):

    # 1. access_3531407: pass B real_or_simulator real -> simulator
    r = by_doi["10.1109/access.2025.3531407"]
    e = field_entry(r, "B", "real_or_simulator")
    expect(isinstance(e, dict) and e.get("value") == "real",
           "access_3531407 real_or_simulator expected 'real'")
    e["value"] = "simulator"
    log_adj(r, field="passes.B.real_or_simulator", old="real", new="simulator",
            basis="'will be executed on the IBM Quantum Experience platform' "
                  "(forward-looking; no real-hardware run). Pass D "
                  "real_vs_sim_explicit=False.",
            source=SRC, date=DATE)
    changes.append("access_3531407       B.real_or_simulator: real -> simulator")

    # 2. access_3581030: dataset_name HAM10000 -> real 770-image skin set
    r = by_doi["10.1109/access.2025.3581030"]
    e = field_entry(r, "A", "dataset_name")
    new_ds = ["Mpox/Measles/Chickenpox/Normal skin-lesion image set "
              "(~770 images, 4 classes)"]
    if isinstance(e, dict):
        expect(e.get("value") == ["HAM10000"],
               "access_3581030 dataset_name expected ['HAM10000']")
        e["value"] = new_ds
    elif isinstance(e, list):
        expect([wval(x) for x in e] == ["HAM10000"],
               "access_3581030 dataset_name(list) expected ['HAM10000']")
        e[0]["value"] = new_ds[0]
    else:
        raise Halt("access_3581030 dataset_name unexpected type")
    log_adj(r, field="passes.A.dataset_name", old=["HAM10000"], new=new_ds,
            basis="Abstract: classes Chickenpox/Measles/Monkeypox/Normal, 770 "
                  "images (462+154+154). Not HAM10000 (10,015 dermoscopic lesion "
                  "images). Size fields already correct; modality kept dermoscopy "
                  "(skin/optical, non-ionising).",
            source=SRC, date=DATE)
    changes.append("access_3581030       A.dataset_name: ['HAM10000'] -> 770-image 4-class skin set")

    # 3. jestch: keep MRI, flag internal contradiction (no value change)
    r = by_doi["10.1016/j.jestch.2026.102386"]
    expect(wval(field_entry(r, "A", "modality_primary")) == "MRI",
           "jestch modality_primary expected 'MRI'")
    log_adj(r, field="passes.A.modality_primary", old="MRI", new="MRI",
            action="no_change",
            basis="Paper internally contradictory: section 3.1.3 '60 volumes from "
                  "KiTS21' (CT) vs section 4 '112 MRI volumes, diffusion-weighted "
                  "MRI'. Predominant claim (title/keywords/section 4) is MRI; "
                  "modality kept MRI. Internal KiTS21/MRI contradiction recorded "
                  "as methodological-quality concern.",
            quality_flag="internal_dataset_contradiction_KiTS21_CT_vs_112_MRI_volumes",
            source=SRC, date=DATE)
    changes.append("jestch               modality kept MRI; internal contradiction flagged (quality)")

    # 4. s12911: already simulator_only/simulator (prior pass corrected); no edit
    r = by_doi["10.1186/s12911-021-01588-6"]
    hm = wval(field_entry(r, "B", "hardware_modality"))
    rs = wval(field_entry(r, "B", "real_or_simulator"))
    expect(hm == "simulator_only" and rs == "simulator",
           "s12911 expected already simulator_only/simulator, got {}/{}".format(hm, rs))
    log_adj(r, field="passes.B.hardware_modality",
            old="(contradiction logged 'annealing')", new="simulator_only",
            action="no_change_already_correct",
            basis="Ruling was annealing->simulator_only; current data already "
                  "simulator_only (gate quanvolutional QNN via TensorFlow "
                  "Quantum/Cirq; D-Wave Leap secondary). Contradiction predates a "
                  "prior correction. Marked adjudicated.",
            source=SRC, date=DATE)
    changes.append("s12911               NO EDIT (already simulator_only/simulator); adjudicated")

    # 5. acffa3: -> multimodal (other + X-ray secondary), ionising TRUE
    r = by_doi["10.1088/2632-2153/acffa3"]
    e_mp = field_entry(r, "A", "modality_primary")
    expect(isinstance(e_mp, dict) and e_mp.get("value") == "MRI",
           "acffa3 modality_primary expected 'MRI'")
    e_mp["value"] = "other"
    e_ms = field_entry(r, "A", "modality_secondary")
    expect(isinstance(e_ms, dict) and (e_ms.get("value") in (None, "")),
           "acffa3 modality_secondary expected empty")
    e_ms["value"] = "X-ray"
    if "quote" not in e_ms or not e_ms.get("quote"):
        e_ms["quote"] = ""
    log_adj(r, field="passes.A.modality_primary/secondary",
            old={"primary": "MRI", "secondary": None},
            new={"primary": "other", "secondary": "X-ray"},
            basis="Medical data is MedNIST Hand (X-ray) + Breast (MRI), loosely "
                  "labelled 'MRI'. primary=other -> modality_subcategory resolves "
                  "'multimodal' via MedNIST; secondary=X-ray flips ionising_flag "
                  "TRUE (hand X-ray).",
            source=SRC, date=DATE)
    changes.append("acffa3               modality -> other + X-ray secondary (=> multimodal, ionising TRUE)")

    # 6. ejca: exclude (cull authority); mark in jsonl for traceability
    r = by_doi["10.1016/j.ejca.2025.115632"]
    basis_ejca = ("Journal-labelled 'Current Perspective' (Eur J Cancer "
                  "226(2025)115632); no dataset, results, or circuits; abstract "
                  "entirely conditional ('could enable/optimise'). Fails OSF v0.6 "
                  "section 7.5 (must implement).")
    r["_excluded_step5"] = {"prisma_reason": "secondary/review",
                            "basis": basis_ejca, "source": SRC, "date": DATE}
    log_adj(r, field="_excluded_step5", old="included", new="excluded",
            basis=basis_ejca, source=SRC, date=DATE)
    changes.append("ejca                 EXCLUDED (secondary/review); N 135 -> 134")

    # 7. s41598_026_51942_9: image_encoding amplitude -> angle (ZZFeatureMap)
    r = by_doi["10.1038/s41598-026-51942-9"]
    e = field_entry(r, "B", "image_encoding")
    expect(isinstance(e, list) and all(isinstance(x, dict) and x.get("value") == "amplitude"
                                       for x in e),
           "s41598_026 image_encoding expected list of 'amplitude'")
    for x in e:
        x["value"] = "angle"
    log_adj(r, field="passes.B.image_encoding",
            old="amplitude (x{})".format(len(e)), new="angle",
            basis="ansatz_family=ZZFeatureMap is a Pauli-Z/angle feature map, not "
                  "amplitude encoding.",
            source=SRC, date=DATE)
    changes.append("s41598_026_51942_9   B.image_encoding: amplitude -> angle")

    # 8. jbhi_2025_3610855: strike mis-extracted Pass-D 'real hardware' quote
    r = by_doi["10.1109/jbhi.2025.3610855"]
    hr = field_entry(r, "D", "honest_resource_reporting_evidence")
    expect(isinstance(hr, dict) and isinstance(hr.get("real_vs_sim_explicit"), dict),
           "jbhi honest_resource_reporting_evidence structure unexpected")
    rv = hr["real_vs_sim_explicit"]
    expect(rv.get("value") is True and "real quantum hardware was used" in (rv.get("quote") or ""),
           "jbhi real_vs_sim_explicit expected True + 'real hardware' quote")
    old_quote = rv.get("quote")
    rv["value"] = False
    rv["quote"] = ""
    rv["_struck"] = ("mis-extracted; struck on adjudication. Record note states "
                     "simulators only (PennyLane default.mixed); Pass B "
                     "simulator/simulator_only.")
    log_adj(r, field="passes.D.honest_resource_reporting_evidence.real_vs_sim_explicit",
            old={"value": True, "quote": old_quote}, new={"value": False, "quote": ""},
            basis="Pass-D quote inverted reality; the same record's note says "
                  "simulators only; Pass B already simulator. Quote struck, value "
                  "corrected to False.",
            source=SRC, date=DATE)
    changes.append("jbhi_2025_3610855    D real_vs_sim_explicit: True/'real hardware' -> False/struck")

    # 9. s10791_025_09634_x: baseline_rigour_grade matched_data -> weak.
    #    classical_baseline_present=True but performance_metrics_classical=[]
    #    and no same-split language: 'matched_data' overclaims, 'none' would
    #    contradict present=True. Retained on merits review (adjudication log
    #    row 9); reproduces the committed data state.
    r = by_doi["10.1007/s10791-025-09634-x"]
    e = field_entry(r, "C", "baseline_rigour_grade")
    expect(isinstance(e, dict) and e.get("value") == "matched_data",
           "s10791 baseline_rigour_grade expected 'matched_data'")
    e["value"] = "weak"
    log_adj(r, field="passes.C.baseline_rigour_grade", old="matched_data", new="weak",
            basis="classical_baseline_present=True (changed since the contradiction "
                  "was logged) but performance_metrics_classical=[]; 'matched_data' "
                  "overclaims (no same-data metric comparison), 'none' would "
                  "contradict present=True; regraded 'weak'. Original Bucket-A "
                  "ruling was 'none' premised on present=False.",
            source=SRC, date=DATE)
    changes.append("s10791_025_09634_x   C.baseline_rigour_grade: matched_data -> weak")

    # 10. radphyschem_2025_113545: baseline_rigour_grade -- NO EDIT (prior
    #     override reverted). 'matched_data' is grounded by an explicit same-split
    #     quote and consistent with classical_baseline_present=True; the logged
    #     contradiction (vs a stale 'none') is already resolved.
    r = by_doi["10.1016/j.radphyschem.2025.113545"]
    e = field_entry(r, "C", "baseline_rigour_grade")
    expect(isinstance(e, dict) and e.get("value") == "matched_data",
           "radphyschem baseline_rigour_grade expected 'matched_data'")
    log_adj(r, field="passes.C.baseline_rigour_grade", old="matched_data", new="matched_data",
            action="no_change_already_resolved",
            basis="Grade 'matched_data' grounded by quote 'classical CNN under the "
                  "same data split and preprocessing settings'; consistent with "
                  "classical_baseline_present=True. Contradiction was logged "
                  "against a stale 'none'; already resolved. A prior draft "
                  "erroneously downgraded to 'weak'; reverted.",
            source=SRC, date=DATE)
    changes.append("radphyschem_2025_113545  baseline_rigour_grade: NO EDIT (kept grounded 'matched_data'; prior downgrade reverted)")

    # 11. s11760_023_02857_9: dataset_realism_grade private_single_centre ->
    #     public_benchmark. Pass A dataset_type=public_benchmark (OsiriX DICOM
    #     sample library is public). Retained on merits review (adjudication log
    #     row 11); reproduces the committed data state.
    r = by_doi["10.1007/s11760-023-02857-9"]
    e = field_entry(r, "C", "dataset_realism_grade")
    expect(isinstance(e, dict) and e.get("value") == "private_single_centre",
           "s11760 dataset_realism_grade expected 'private_single_centre'")
    e["value"] = "public_benchmark"
    log_adj(r, field="passes.C.dataset_realism_grade",
            old="private_single_centre", new="public_benchmark",
            basis="Pass A dataset_type=public_benchmark (DICOM image library / "
                  "osirix-viewer); realism regraded to public_benchmark.",
            source=SRC, date=DATE)
    changes.append("s11760_023_02857_9   C.dataset_realism_grade: private_single_centre -> public_benchmark")


# ---------------------------------------------------------------------------
# Cull updates
# ---------------------------------------------------------------------------
EJCA_DOI = "10.1016/j.ejca.2025.115632"


def update_included(included_path, out_path):
    with open(included_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = rows[0].keys() if rows else ["doi"]
    kept = [r for r in rows if norm(r.get("doi")) != norm(EJCA_DOI)]
    removed = len(rows) - len(kept)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames))
        w.writeheader()
        w.writerows(kept)
    return len(rows), len(kept), removed


def update_exclusions(excl_path, out_path):
    with open(excl_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
        fieldnames = list(rows[0].keys()) if rows else \
            ["doi", "category", "confidence", "prisma_reason", "basis", "evidence"]
    already = any(norm(r.get("doi")) == norm(EJCA_DOI) for r in rows)
    if not already:
        ejca = {k: "" for k in fieldnames}
        defaults = {
            "doi": EJCA_DOI,
            "category": "secondary/review",
            "confidence": "high",
            "prisma_reason": "secondary/review",
            "basis": "Journal-labelled 'Current Perspective'; no implementation",
            "evidence": "Abstract entirely conditional; no dataset/results/circuits; "
                        "Eur J Cancer 226(2025)115632",
        }
        for k, v in defaults.items():
            if k in ejca:
                ejca[k] = v
        rows.append(ejca)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    return len(rows), already


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--merged", default="/mnt/user-data/uploads/extractions_merged.jsonl")
    ap.add_argument("--included", default="/home/claude/cull135/included_step5.csv")
    ap.add_argument("--exclusions", default="/mnt/user-data/outputs/manual_fulltext_exclusions.csv")
    ap.add_argument("--outdir", default="/home/claude/step5_out")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)
    recs = load_jsonl(args.merged)
    by_doi = {norm(r.get("doi")): r for r in recs}

    required = [
        "10.1109/access.2025.3531407", "10.1109/access.2025.3581030",
        "10.1016/j.jestch.2026.102386", "10.1186/s12911-021-01588-6",
        "10.1088/2632-2153/acffa3", "10.1016/j.ejca.2025.115632",
        "10.1038/s41598-026-51942-9", "10.1109/jbhi.2025.3610855",
        "10.1007/s10791-025-09634-x", "10.1016/j.radphyschem.2025.113545",
        "10.1007/s11760-023-02857-9",
    ]
    missing = [d for d in required if norm(d) not in by_doi]
    if missing:
        print("FATAL: required DOIs missing from merged set: {}".format(missing))
        sys.exit(2)

    # normalise the lookup to exact required keys (they are already normalised lower)
    lk = {d: by_doi[norm(d)] for d in required}

    changes = []
    try:
        apply_fixes(lk, changes)
    except Halt as exc:
        print("HALT (no files written): {}".format(exc))
        sys.exit(3)

    # write patched jsonl (all records, order preserved)
    out_jsonl = os.path.join(args.outdir, "extractions_merged_step5.jsonl")
    with open(out_jsonl, "w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    inc_total, inc_kept, inc_removed = update_included(
        args.included, os.path.join(args.outdir, "included_step5_v134.csv"))
    excl_n, excl_already = update_exclusions(
        args.exclusions, os.path.join(args.outdir, "manual_fulltext_exclusions_v47.csv"))

    report = os.path.join(args.outdir, "step5_fix_report.txt")
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("Step-5 high-severity contradiction adjudication - fix report\n")
        fh.write("Date: {}\n\n".format(DATE))
        fh.write("JSONL edits applied ({} records touched):\n".format(len(changes)))
        for c in changes:
            fh.write("  - {}\n".format(c))
        fh.write("\nCull:\n")
        fh.write("  included_step5: {} -> {} (removed {})\n".format(inc_total, inc_kept, inc_removed))
        fh.write("  manual exclusions rows now: {} (ejca already present: {})\n".format(excl_n, excl_already))
        fh.write("  PRISMA excluded total: 46 -> 47; secondary/review 10 -> 11\n")

    print("OK. wrote:")
    print("  ", out_jsonl)
    print("  ", os.path.join(args.outdir, "included_step5_v134.csv"),
          "({} survivors)".format(inc_kept))
    print("  ", os.path.join(args.outdir, "manual_fulltext_exclusions_v47.csv"))
    print("  ", report)
    print("\nChange summary:")
    for c in changes:
        print("  -", c)


if __name__ == "__main__":
    main()
