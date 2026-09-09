# METHODS RECORD — Step 5 (Extraction QA, Contradiction Adjudication, Classification Axes)

> **THIS FILE = RECORD FOR THE METHODS SECTION OF THE PAPER.**
> Companion file `CONTINUITY_HANDOFF_step5_for_next_chat.md` is the operational handoff for the next working chat. This file is the citable methods/PRISMA record; that file is "where we are / what's next".

**Review:** "Quantum Computing for Medical Imaging Applications" (IEEE TRPMS), PRISMA-compliant, LLM-assisted extraction.
**Record date:** 2026-06-22. **Final synthesis set: N = 134.**

---

## 1. PRISMA eligibility outcome (verified)
- Full-text records extracted: **181**.
- Excluded at eligibility: **47** = 16 automated paradigm/scope screen + 31 manual full-text adjudication.
- Included in synthesis: **134**.
- Integrity verified this session: included(134) ∩ excluded(47) = ∅; union = all 181; no phantom or leaked records; every record sits in exactly one set.
- Per-paper exclusion reasons: `manual_fulltext_exclusions_v47.csv` (manual rows) + automated cull. Consolidated reason tally: secondary/review 11; quantum-sensing/detector 10; non-imaging-medical 9; non-medical 9; QCA 2; method-only 2; instrumentation/physics 2; physics-phenomenon 1; quantum-inspired 1.

## 2. LLM extraction pipeline
- Per paper, a five-pass extraction (Passes A–E) over an **OCR-augmented** full-text corpus (pdftotext + OCR; OCR is required because pdftotext-only misses results tables and figure text, degrading quote grounding).
- Each extracted field is stored as `{value, quote, chunk_id, pass_id}`: a value with a verbatim grounding quote and its source chunk. Quote grounding is enforced by a strict refill validator with bounded rescue.
- Pass E is a reconciliation pass: a cross-field consistency check that emits per-record contradiction flags with severity (high / medium / low).
- Merged record = one JSON object per paper: `{stable_name, doi, passes:{A..E}, _audit}`.

## 3. Model disclosure / deviations (for AI-disclosure + OSF Transparent Changes)
- Extraction executed on Claude Opus models. A B/C quote-grounding refill (Step 4.7) ran on **Claude Opus 4.8** after a CLI migration removed Opus 4.7 from the model picker. This model change is a **logged deviation**.
- All Step-5 value corrections are deterministic, scripted, and assertion-guarded (each edit asserts the pre-edit value and halts atomically on mismatch) with per-record provenance in `_step5_adjudication`.

## 4. Step-5 high-severity contradiction adjudication
19 high-severity Pass-E contradictions on the synthesis set were adjudicated by full-text validation plus cross-field/definitional checks. Outcome: **5 value corrections, 1 eligibility exclusion (ejca), 5 locked grades** (one of which was corrected on review). Field — basis:
- `access_3531407` real_or_simulator real→**simulator** ("will be executed on the IBM Quantum Experience platform" = planned, not run; Pass D real_vs_sim_explicit=False).
- `access_3581030` dataset_name HAM10000→**4-class pox/skin image set** (~770 images; classes Chickenpox/Measles/Monkeypox/Normal; not HAM10000's 10,015 dermoscopic images). Modality kept dermoscopy (non-ionising).
- `acffa3` modality_primary→**other** + secondary→**X-ray** (data = MedNIST Hand [X-ray] + Breast [MRI] → modality_subcategory multimodal; ionising_flag TRUE).
- `s41598-026-51942-9` image_encoding amplitude→**angle** (ZZFeatureMap is a Pauli-Z/angle feature map).
- `jbhi.2025.3610855` Pass-D "real quantum hardware" quote **struck**, value→False (the same record's note states simulators only; Pass B simulator/simulator_only).
- `ejca.2025.115632` **EXCLUDED** (journal-labelled "Current Perspective"; no dataset/results/circuits; secondary/review).
- Locked grades (merits-based, do not relitigate): `radphyschem` baseline_rigour_grade=**matched_data** (grounding quote "classical CNN under the same data split and preprocessing settings"); `s10791` baseline_rigour_grade=**weak** (classical baseline present but no matched metrics, no same-split language); `s11760` dataset_realism_grade=**public_benchmark** (OsiriX public DICOM library); `s12911` hardware_modality=**simulator_only**; `jestch` modality=**MRI** with an internal KiTS21(CT)/MRI dataset contradiction flagged as a methodological-quality concern.
- Transparency: one draft grade edit (radphyschem) was wrong and was corrected on provenance review; the provenance is retained in-record.
- Medium/low-severity contradictions (≈198) and inter-rater κ are separate QA steps (see §7).

## 5. Derived classification axes (deterministic, no LLM; raw extraction untouched)
Final distributions on **N = 134**:

**Axis A — imaging task** (`task_axis_a`): classification 97; segmentation 15; analysis 7; reconstruction 7; super_resolution 4; denoising 2; radiomics 1; other 1.
Coarser `task_family`: predictive 99; image_to_image 16; segmentation 15; representation 2; other 2.

**Axis B — quantum paradigm** (`paradigm_axis_b`, 9-value): hybrid_quantum_classical 84; quantum_machine_learning 23; quantum_kernel_methods 10; frqi_neqr_encoding 8; quantum_annealing_qubo 5; variational_algorithms 2; qaoa_quantum_optimisation 1; other 1.

**Axis C — imaging modality** (`modality_subcategory`): mri 36; ct 27; x_ray 20; dermoscopy 18; fundus 10; histopathology 8; multimodal 7; pet 2; ultrasound 2; unspecified_medical_imaging 2; endoscopy 2.
`modality_family`: radiological_ionising 47; mr 36; optical 30; microscopy_pathology 8; multimodal 7; nuclear 2; ultrasound 2; unspecified 2.
`ionising_flag` (ionising iff CT/PET/SPECT/X-ray in primary or secondary): ionising 56; non-ionising 78.

**Axis D — execution context** (`execution_context`): ideal_or_unspecified_simulator 110; real_qpu 21; noisy_simulator 3.

## 6. Maturity grading (§10) and sensitivity (§11) inputs (N = 134)
- `dataset_realism_grade`: public_benchmark 98; private_single_centre 21; toy 8; private_multi_centre 6; not_reported 1. → **benchmark + toy = 106/134 (~79%)** (the data-maturity headline).
- `baseline_rigour_grade` (5-level): matched_data_compute 46; matched_data 42; matched_data_compute_stats 29; none 9; weak 8.
- `translational_level`: populated for all 134 (tabulate the distribution at synthesis from the `extractions_ai.xlsx` column).
- §11 registered stricter subset (real-clinical data AND classical baseline AND statistical testing): **8 papers** [prior-session count; reconfirm at synthesis from the final field names — `external_validation`/`statistical_testing` flags live under schema fields that must be re-identified before quoting counts].

## 7. QA status / limitations to state in Methods
- Inclusion (N = 134) is final and integrity-verified.
- Extraction-value QA: high-severity contradictions adjudicated; medium/low-severity flags (≈198) not yet audited at deposit time.
- Inter-rater agreement (Cohen's κ; registered threshold ≥ 0.61) pending the populated co-author human workbook.
- 37 `_quote_unverified` cells pending spot-check.

## 8. Reproducibility artifacts
- `apply_step5_contradiction_fixes.py` — original-state patch (pre-edit assertions + provenance).
- `fix_radphyschem.py` — single-field corrector for already-patched data (idempotent, with a verification table).
- `step5_contradiction_adjudication_log.md` — full adjudication record (resolution table, cited basis, recomputed distributions).
- `included_step5_v134.csv`, `manual_fulltext_exclusions_v47.csv`.
- `compute_derived.py` + axis modules (`imaging_task_axis`, `paradigm_axis_b`, `execution_context_axis`, `modality_axis`, `hardware_execution`, `paradigm_subcategory`).
