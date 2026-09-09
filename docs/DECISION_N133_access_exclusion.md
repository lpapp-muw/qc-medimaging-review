# DECISION RECORD: exclusion of `10.1109/ACCESS.2025.3627877` and recompute at N=133

Date 2026-08-04. Decision taken by Laszlo Papp, corresponding author. Locked.
Supersedes N=134 for all corpus statistics.

---

## 1. The decision

**`10.1109/ACCESS.2025.3627877` is excluded at full text under the pre-registered PRISMA category `QUANTUM_INSPIRED_CLASSICAL`.** Two further candidates were adjudicated and **retained**.

| DOI | Verdict | Basis |
|---|---|---|
| `10.1109/ACCESS.2025.3627877` | **EXCLUDE** | Six explicit author statements that no quantum circuit is executed; encoder implemented in classical linear algebra |
| `10.1080/00207454.2026.2656322` | RETAIN | Circuit formalism present in the text; no denial of execution |
| `10.1016/j.compbiomed.2026.111633` | RETAIN | No denial of execution; retained under a positive-evidence standard, flagged as borderline |

### 1.1 Rule applied

> Exclude only when the paper's own text states that no quantum circuit is executed, real or simulated, and the "quantum" component is a classically-computed transform. Simulator execution of an actual circuit is IN, per the registered criteria.

The rule is deliberately **positive-evidence-based**. Absence of evidence of execution is a weaker standard than evidence of absence, and applying the weaker standard uniformly would require re-screening all 134 full texts. See §5.

### 1.2 Evidence for the exclusion, verbatim

Full text read: 17 pages declared, 17 rendered, 17 processed, 0 skipped; 66,203 characters; 42 embedded images; pages 7, 8 and 13 additionally read as rasterised images.

- §V-E, p.7: "While no quantum hardware is used, the structure preserves core principles of quantum logic: superposition, entanglement, and unitary evolution."
- §V-E, p.8: "This encoder is fully differentiable and implemented using classical linear algebra (NumPy/PyTorch), making it compatible with end-to-end training on standard GPUs. Our design offers the representational benefits of PQCs while bypassing quantum hardware dependency."
- Contribution 2, p.3: "trainable parameters representing simulated superposition […] without quantum hardware."
- Introduction, p.2: "a quantum-inspired linear transformation layer that emulates quantum superposition behavior […] without the need for actual quantum hardware."
- Literature survey, p.6: "Our quantum-inspired layer simulates these effects classically"
- Future work, p.16: "Although this work utilized quantum-inspired simulation, transitioning to real quantum circuits on emerging NISQ hardware may further boost feature expressivity."

Equation 18 is `Q = tanh(W_q · Z^(L) + b_q)`, a dense layer with a tanh activation. Equations 19 and 20 describe a "pseudo-qubit vector", a "simulated 1-qubit rotation gate" and a **virtual** entanglement layer. No state vector is evolved, no circuit is executed, no shots are taken. PennyLane 0.31.0 appears in the implementation list, but the encoder is stated as NumPy/PyTorch. "Quantum-inspired" appears 25 times.

### 1.3 Evidence for the two retentions

**`00207454` (QNL-Net).** Full read, 167,248 chars, 42 formfeed blocks, 0 low-content. Equation 15 is `|φ_d⟩ = (C_r(φ))^F |θ_σ⟩`, described as "the encoded input state is subjected to a parameterised unitary operation a number of times to give the final quantum state", with `C_r(φ)` the unitary operator and `F` the number of **ansatz repeats**. The Figure 2 OCR layer carries the labels "Variational Quantum … Measurement Circuit". The paper also states it captures long-range dependencies "using quantum circuits". Zero qubits, shots or framework are reported; software is "Python 3.x, TensorFlow / PyTorch, Anaconda". **Formalism present, execution unverifiable, no denial → RETAIN.**

**`compbiomed 111633` (SQGAN-MCOA).** Full read, 166,645 chars, 28 blocks, 0 low-content. Body-only vocabulary counts, references excluded: `unitary` 0, `ansatz` 0, `quantum circuit` 0, `superposition` 0, `entangl` 0, `Hadamard` 0, `CNOT` 0, `shots` 0, encoding scheme 0, PennyLane/Qiskit/Cirq 0. `qubit` occurs once in the body and once in the references, both pointing at reference [32], a third party's superconducting-qubit QGAN paper. Every quantum sentence is the model's name, vocabulary attributed to [32], or "**quantum-inspired** optimization" (twice). The only method-level mention is an undefined "quantum gradient subroutine". Implementation: "coded in Python 3.7 using the PyTorch deep learning framework". **No formalism, but also no denial → RETAIN under the positive-evidence rule, flagged as the weakest retention in the corpus.**

---

## 2. PRISMA flow, corrected

| Field | Was | Is |
|---|---|---|
| Studies included in synthesis | 134 | **133** |
| Reports excluded at full text | 47 | **48** |
| Of which quantum-inspired classical | 1 | **2** |
| Reports assessed for eligibility | 181 | 181 (unchanged) |

All upstream counts (8,961 / 2,114 / 6,847 / 8 / 6,839 / 191 / 10 / 181) are unchanged. `133 + 48 = 181` reconciles.

---

## 3. Complete recompute: every number that changes

Input partition verified: 134 rows read, 1 dropped, 133 retained, `133 + 1 = 134` reconciles. Exactly one row matched the dropped DOI.

**Every distribution changes by exactly one in exactly one category.** The excluded study's profile: Malaysia, IEEE, in_silico, hybrid_quantum_classical, dermoscopy, optical, classification, predictive, ideal_or_unspecified_simulator, simulator, no transpilation, private_single_centre, matched_data_compute_stats, k-fold, no reporting standard, classical XAI, no error mitigation, reproducibility tier none, real_clinical realism, QRA=1, low mitigation clarity, dishonest resource reporting, readiness 2, moderate concern, non-ionising, non-multimodal, Quality fit YES.

### 3.1 Headline counts

| Metric | N=134 | N=133 |
|---|---|---|
| Classification task | 97 (72.4%) | 96 (72.2%) |
| Classical baseline present | 125 (93.3%) | 124 (93.2%) |
| Statistical testing present | 35 (26.1%) | 34 (25.6%) |
| External validation used | 12 (9.0%) | 11 (8.3%) |
| External test set present | 17 | 16 |
| Code release | 30 (22.4%) | 30 (**22.6%**) |
| Data release | 78 (58.2%) | 78 (**58.6%**) |
| Weights release | 1 | 1 |
| Ionising modality | 56 (41.8%) | 56 (**42.1%**) |
| Real-QPU execution | 21 (15.7%) | 21 (**15.8%**) |
| Sample-size justification | 0 | 0 |
| Prospective clinical evaluation | 0 | 0 |
| Regulatory pathway addressed | 6 | 5 |
| Distinct journals | 79 | 79 |
| Distinct countries (alias-merged) | 31 | 31 |

Note the four entries where the count is unchanged but the **percentage rises** because the denominator fell: ionising modality, real-QPU execution, code release and data release. All four appear in the manuscript and must be updated.

**BUG IDENTIFIED and corrected 2026-08-04, third verification pass.** An earlier version of this table reported code release as 30 → 29 and data release as 78 → 77. That was wrong. The excluded study has `code_release = FALSE`, `data_release = FALSE` and `reproducibility_tier = none`, so removing it decrements the FALSE counts (code 104 → 103, data 56 → 55) and leaves the TRUE counts untouched. Verified directly against the excluded row. The changed-category list in section 3.4 was always correct on this point; only this summary table was wrong.

### 3.2 Quality-fit kernel

| Component | N=134 | N=133 |
|---|---|---|
| Real clinical dataset | 27 | 26 |
| Classical baseline present | 125 | 124 |
| Statistical testing present | 35 | 34 |
| Top baseline rigour (`matched_data_compute_stats`) | 29 | 28 |
| **Kernel, all three criteria** | **8** | **7** |
| `Quality fit` column = YES | 8 | 7 |
| At least 2 of 3 criteria | 54 | 53 |
| Union of the 3 criteria | 125 | 124 |

The kernel recomputed from the three criteria agrees with the stored `Quality fit` column at both N. The excluded study was kernel member 6.

### 3.3 Claims versus evidence, §4.6

| | N=134 | N=133 |
|---|---|---|
| Clinical-impact claim asserted | 128 (95.5%) | 127 (**95.5%**) |
| of those, translation readiness = 0 | 112 | **112** |
| of those, no statistical testing | 94 | **94** |
| of those, no external validation | 116 | **116** |

**The three sub-counts do not change.** The excluded study scored readiness 2, reported statistical testing, and reported external validation, so it sat outside all three subsets. The headline argument of §4.6 is unaffected in substance and the percentage of claimants is identical to one decimal place.

### 3.4 Full changed-category list

Each falls by exactly 1: `country_corresponding` Malaysia 4→3; `publisher` IEEE 21→20; `translational_level` in_silico 16→15; `paradigm` / `paradigm_subcategory` / `paradigm_axis_b` hybrid_quantum_classical 84→83; `modality_primary` and `modality_subcategory` dermoscopy 18→17; `modality_family` optical 30→29; `task_axis_a` and `imaging_task` classification 97→96; `task_family` predictive 99→98; `execution_context` ideal_or_unspecified_simulator 110→109; `real_or_simulator` simulator 113→112; `transpilation_level` none_reported 121→120; `dataset_realism_grade` private_single_centre 21→20; `baseline_rigour_grade` matched_data_compute_stats 29→28; `dataset_type` "private_clinical; public_benchmark" 4→3; `cross_validation_strategy` k_fold 40→39; `reporting_standard_adherence` none 133→132; `explainability_mechanism` classical_XAI 23→22; `error_mitigation_type` none_reported 127→126; `reproducibility_tier` none 50→49; `dataset_realism_rob` real_clinical 27→26; `quantum_resource_accounting_completeness` **1: 53→52**; `error_mitigation_clarity` low 127→126; `honest_resource_reporting` FALSE 116→115; `clinical_translation_readiness` **2: 2→1**; `summary_methodological_quality` moderate_concern 88→87; `ionising_flag` FALSE 78→77; `multimodal` FALSE 108→107; `funding_declared` TRUE 69→68; `anatomy` other 48→47; `image_encoding` angle 87→86.

**QRA distribution at N=133: {0:17, 1:52, 2:43, 3:17, 4:4}.** Discussion chunk D-4 uses this.

---

## 4. Defects found during the recompute

**BUG IDENTIFIED (new, MEDIUM). Country aliasing is not persisted in the workbook.**
`country_corresponding` holds **34 distinct raw strings**, including three alias groups: `Republic of Korea` (7) + `South Korea` (4) = 11; `United States` (4) + `USA` (3) + `United States of America` (3) = 10. Merging these yields exactly the 31 countries, South Korea 11 and United States 10 that manuscript v5 §4.2 reports, so **the manuscript is correct and the workbook is unnormalised**. Anyone recomputing geography from the workbook will get 34/7/4/4/3/3 and silently contradict the paper. Action: persist a normalised `country_corresponding_norm` column, or record the alias map in the methods record. At N=133 the merged count remains 31.

**BUG IDENTIFIED (mine, corrected in place).** My first recompute treated `clinical_impact_claim` as a boolean and returned 0 for all rows. It is a free-text field holding the claim itself. Corrected; the §4.6 block above uses non-empty-and-not-`not_reported` as the presence test, which reproduces the manuscript's 128 at N=134.

**Resolved 2026-08-04.** `year` is blank for all 134 rows in the enriched workbook; the manuscript's year distribution was sourced from `human.xlsx`, which holds all 181 full-text-assessed records with a year value on every one and all 134 corpus DOIs present. The excluded study carries `year = 2025`, matching its IEEE Access metadata. The corrected series at N=133 is **2021:1, 2022:9, 2023:20, 2024:19, 2025:49, 2026:35**, reconciling to 133 with zero unknowns; the same computation at N=134 reproduces the published v5 series exactly. Note for the methods record: the year distribution cannot be reproduced from the enriched workbook alone.

---

## 5. What the exclusion obliges the manuscript to say

The retention of `compbiomed 111633` under a positive-evidence rule, alongside the exclusion of ACCESS, is defensible but will be challenged. Three things must appear.

1. **Limitations, borderline retentions.** Name `10.1016/j.compbiomed.2026.111633` and `10.62347/wohq8174` as studies retained where quantum execution could not be confirmed from the full text, and state the standard of proof that produced the asymmetry.
2. **Limitations, screening failure.** The excluded study passed AI abstract screening, AI full-text screening, AI extraction, the deterministic paradigm cull, and two independent Opus-4.8 enrichment verification passes. At human appraisal it was confirmed by two of the five reviewers, left unanswered by a third, and rejected by two: one on the ground that the quantum layer might not be genuine, the other, a quantum-computing specialist, identifying the mechanism outright. One reviewer questioned it before the full-text re-inspection was performed. That is a measurable result about the limits of both automated and expert screening and it belongs in the paper.
3. **Methods, why it was missed.** The extraction faithfully recorded the authors' own abstract phrase "hybrid quantum-classical deep learning framework" into `paradigm`, and `paradigm_subcategory` inherited it (`basis = inherited<-paradigm=hybrid_quantum_classical`). Nothing downstream re-read the methods section. This is not a failure of the step-4.8 priority rule.

**Queued as Phase 1b: bounded corpus-wide execution re-screen.** All 133 full texts are on disk. QNL-Net proved the extraction fields under-report circuit formalism, so a screen built on those fields alone is unreliable. If `compbiomed 111633` turns out not to be unique, item 1 above converts from a one-off caveat into a systematic finding about "quantum" as branding in this literature, which is a stronger result than the exclusion itself.

---

## 6. Downstream artefacts requiring update

**Status as of 2026-08-04: all analysis artefacts are closed. Only the manuscript itself remains.**

| Artefact | Change | Status |
|---|---|---|
| Manuscript §4.1 | 134→133, 47→48 exclusions, quantum-inspired-classical 1→2 | **OPEN** |
| Manuscript §4.2 to §4.7 | all counts in §3.4 above; **four** percentages rise on a falling denominator (ionising, real-QPU, code release, data release); year count for 2025 falls from 50 to 49 | **OPEN** |
| Manuscript §4.8 | kernel 8→7 | **OPEN** |
| Manuscript §4.9 | replaced in full | CLOSED, `section_4_9_v4_N133_5reviewers.md` |
| Appendix A, PRISMA figure | included 134→133; excluded 47→48; quantum-inspired classical 1→2 | **OPEN** |
| Appendix B, cross-tabs | hybrid×classification 84→83 row and the dermoscopy row | **OPEN** |
| Appendix C, provenance | every affected count | **OPEN** |
| Framing chunks | corpus and appraisal numbers rebuilt | CLOSED, `framing_papers_fulltext_v4.md` |
| Kernel adjudication record | rulings applied | CLOSED, `phase2_kernel_adjudication.md` |
| OSF Transparent Changes | seven entries drafted; TC-3 needs a signature dated before the fifth appraisal | CLOSED as drafts, `phase5_protocol_compliance.md` |

Two intermediate analysis files referenced in earlier versions of this table, a three-reviewer and a four-reviewer composite and the first §4.9 draft, are superseded and are to be deleted rather than updated.

**§4.9 is now written.** The two questions that previously blocked it are closed: the dataset-realism ruling was made by the corresponding author (TC-2) and the statistical-testing audit was completed against all seven full texts (Section 1 of `phase2_kernel_adjudication.md`). The kernel is stable at 7.
