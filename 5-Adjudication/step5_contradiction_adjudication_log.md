# Step-5 Pass-E High-Severity Contradiction Adjudication Log (FINAL)

**Date:** 2026-06-22
**Reviewer:** corresponding author, manual full-text + merits validation
**Corpus:** 181 extracted; synthesis set 135 -> **134** (ejca excluded).
**Reproducible via:** `apply_step5_contradiction_fixes.py` (original-state patch) and, for data already patched, `fix_radphyschem.py` (single-field corrector). Dispositions recorded per record in `_step5_adjudication`.

This is the settled record. The three values below are locked on the merits; revisit only if a paper re-read contradicts the cited basis.

---

## 1. Disposition summary

Eleven records carry a disposition: **5 value edits**, **1 exclusion** (ejca), **5 no-change/locked grades**. A first draft over-applied three grade edits on cross-field logic without full text. On merits review: one (radphyschem) was a genuine value error and is corrected to `matched_data`; the other two land on defensible values and are retained. Net change from the as-run state is a single field (radphyschem).

Root cause of the earlier churn: resolving cross-field contradictions without the paper. Rule going forward: no override of a grounded value without full-text or definitional support.

---

## 2. Value edits (5) + exclusion (1)

| # | Paper (DOI) | Field | From -> To | Basis |
|---|---|---|---|---|
| 1 | 10.1109/access.2025.3531407 | `B.real_or_simulator` | real -> **simulator** | "will be executed on the IBM Quantum Experience platform" - forward-looking; Pass D real_vs_sim_explicit=False. Full-text validated. |
| 2 | 10.1109/access.2025.3581030 | `A.dataset_name` | ['HAM10000'] -> **770-image 4-class skin set** | Abstract: 770 images, classes Chickenpox/Measles/Monkeypox/Normal; not HAM10000. Modality kept dermoscopy (non-ionising). Full-text validated. |
| 5 | 10.1088/2632-2153/acffa3 | `A.modality_primary`/`secondary` | MRI/none -> **other/X-ray** | MedNIST Hand (X-ray) + Breast (MRI). Yields modality_subcategory=multimodal, ionising_flag=TRUE. Original ungrounded. Full-text validated. |
| 7 | 10.1038/s41598-026-51942-9 | `B.image_encoding` | amplitude -> **angle** | ZZFeatureMap is definitionally an angle feature map. Definitional. |
| 8 | 10.1109/jbhi.2025.3610855 | `D...real_vs_sim_explicit` | True/"real hardware" -> **False/struck** | Same record's note states simulators only; Pass B simulator. Self-contradicted quote struck. In-record grounded. |
| 6 | 10.1016/j.ejca.2025.115632 | `_excluded_step5` | included -> **EXCLUDED** | "Current Perspective"; no dataset/results/circuits. PRISMA: secondary/review. Full-text validated. |

False alarms validated (no edit): s44196, s41598_023_41700, qtc2_70010.

---

## 2a. No-change / locked grades (5)

| # | Paper (DOI) | Field | FINAL value | Basis |
|---|---|---|---|---|
| 3 | 10.1016/j.jestch.2026.102386 | modality_primary | **MRI** + quality flag | Internal KiTS21(CT)/MRI contradiction; predominant claim MRI; flagged as methodological-quality concern. |
| 4 | 10.1186/s12911-021-01588-6 | hardware_modality | **simulator_only** | Grounded (quote "quantum simulation"); contradiction predated a prior correction. |
| 10 | 10.1016/j.radphyschem.2025.113545 | baseline_rigour_grade | **matched_data** (CORRECTED) | Grounding quote "classical CNN under the same data split and preprocessing settings" == matched_data. A draft patch wrongly set `weak`; corrected via `fix_radphyschem.py`. |
| 9 | 10.1007/s10791-025-09634-x | baseline_rigour_grade | **weak** | classical_baseline_present=True but performance_metrics_classical=[] and no same-split language; `weak` is the accurate, conservative grade. (Draft had pushed matched_data/none; both wrong.) |
| 11 | 10.1007/s11760-023-02857-9 | dataset_realism_grade | **public_benchmark** | OsiriX DICOM image library is a public sample library -> public_benchmark, over the grounded-but-likely-misread Pass-C private_single_centre. |

---

## 3. Cull and PRISMA impact

- Synthesis set: **135 -> 134** (ejca excluded).
- PRISMA exclusions: **46 -> 47**; secondary/review **10 -> 11**.
- Artifacts: `included_step5_v134.csv` (134), `manual_fulltext_exclusions_v47.csv`.

---

## 4. Recomputed distributions (135 -> 134), affected axes only

Independent of all section-2a grade decisions (baseline/realism grades do not feed these axes); verified by recompute.

- **task_axis_a:** classification 97; segmentation 15; analysis **8->7**; reconstruction 7; super_resolution 4; denoising 2; radiomics 1; other 1.
- **task_family:** predictive 99; image_to_image 16; segmentation 15; representation 2; other **3->2**.
- **paradigm_axis_b:** hybrid_quantum_classical 84; quantum_machine_learning **24->23**; quantum_kernel_methods 10; frqi_neqr_encoding 8; quantum_annealing_qubo 5; variational_algorithms 2; qaoa_quantum_optimisation 1; other 1.
- **execution_context:** ideal_or_unspecified_simulator 110; real_qpu **22->21**; noisy_simulator 3.
- **ionising_flag:** True **56 (unchanged)**; False **79->78**.

---

## 5. Verification

`compute_for_paper` re-run confirms: acffa3 -> multimodal + ionising TRUE; access_3531407 off real_qpu; s12911/jbhi simulator; access_3581030 dermoscopy/non-ionising. The single corrected field (radphyschem) does not alter any axis distribution.

---

## 6. Correction to an interim statement

ejca was earlier described as the lone Axis-B `other`; that was wrong. ejca = `quantum_machine_learning` + `analysis`. The lone `other` is a different paper, remaining at 1.

---

## 7. Disclosure note for Methods / OSF

This adjudication corrects extraction-level contradictions and excludes one secondary/review article (ejca) at full-text validation, consistent with the registered article-type and implementation criteria; PRISMA flow 181 / 47 / 134. No quality-based eligibility gate introduced; low-clinical-relevance studies retained per the registered grading-plus-sensitivity design. One draft grade edit (radphyschem) was corrected on review; two others were retained on the merits.
