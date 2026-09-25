# PRISMA Inclusion / Exclusion Record (Recovered, Verified)

**Review:** "Quantum Computing for Medical Imaging Applications" (IEEE TRPMS), PRISMA 2020, LLM-assisted extraction.
**Record date:** 2026-06-23. **Final synthesis set: N = 134.**

> **Status note (September 2026).** This record is superseded in two respects. The synthesis set is N = 133 with 48 full-text exclusions (see `DECISION_N133_access_exclusion.md`). The identification counts in Sections 1, 6, 8 and 9 have been corrected against the round-2 search workbook `1-Search/Round-2-All/DOIs.xlsx` (round 2 run on 18 May 2026).
**Canonical pipeline:** the v2 full-corpus pass (open-access filter removed). The v1 open-access pilot locked the 10-category screening taxonomy and is retained only as an audit trail (Section 6). All PRISMA numbers below are the v2 figures unless explicitly marked v1.

**Purpose.** Preserve the inclusion/exclusion chain for the Methods section after two numbering gaps were recovered this session: (1) the 191 -> 181 transition (reports sought vs not retrieved), previously undocumented; (2) the per-paper composition of the 47 full-text exclusions and the 165 -> 134 reconciliation. Every number carries a source tag: `[SOURCE: ...]` for a project handoff or record, `[VERIFIED: computed]` for a value recomputed this session from the uploaded data files.

**Label key.** Inclusion is locked at full text: N = 134 does not shrink again. No quality-based eligibility gate is introduced; low-quality and poorly reported studies are retained and handled by grading plus a pre-registered sensitivity stratum, not by exclusion `[SOURCE: METHODS_RECORD_step5 §7; step5_contradiction_adjudication_log §7]`.

---

## 1. PRISMA flow, top to bottom (v2 canonical)

| Stage | Count | Source |
|---|---:|---|
| **Identification** | | |
| Records identified, database search (18 May 2026, no OA filter): Web of Science 4,813, PubMed 3,127, IEEE Xplore 929 | 8,869 | `[VERIFIED: 1-Search/Round-2-All/DOIs.xlsx]` |
| Export entries without a DOI | 9 | `[VERIFIED: 8,869 − 8,860 non-empty]` |
| Duplicates removed (lower-cased DOI exact match) | 2,013 | `[VERIFIED: 8,860 − 6,847]` |
| Unique records after deduplication | 6,847 | `[SOURCE: v2-and-step4 §1.2]` |
| **Pre-screening removal** | | |
| Records removed: no metadata retrievable (no abstract/title via Crossref/OpenAlex/EuropePMC) | 8 | `[SOURCE: v2-and-step4 §1.2]` |
| **Records screened (title/abstract, Stage 3)** | **6,839** | `[SOURCE: v2-and-step4 §1.2, §1.3]` |
| Records excluded at screening (out-of-scope category; see §2) | 6,660 | `[SOURCE: v2-and-step4 §1.3]` |
| Records promoted back by Stage-3.5 ionising sub-screen (from QUANTUM_SENSING_BIOMED) | 12 | `[SOURCE: v2-and-step4 §1.4]` |
| Net excluded at screening after Stage 3.5 | 6,648 | `[VERIFIED: 6,660 − 12]` |
| **Reports sought for retrieval (full-text candidate set)** | **191** | `[SOURCE: v2-and-step4 §1.5; VERIFIED: all_candidates sheet = 191]` |
| Reports not retrieved (no OA URL; PDF not obtained; see §3) | 10 | `[VERIFIED: computed]` |
| **Reports assessed for eligibility (full text)** | **181** | `[SOURCE: step-4-and-4_5 §2; VERIFIED: extractions = 181, zero extras]` |
| Reports excluded at full text (see §4) | 47 | `[SOURCE: METHODS_RECORD_step5 §1; VERIFIED: 16 + 31]` |
| **Studies included in synthesis** | **134** | `[SOURCE: METHODS_RECORD_step5 §1; VERIFIED: included_step5_v134.csv = 134; 134 + 47 = 181; zero set overlap]` |

Candidate-set composition (191): QC_FOR_IMAGING high 120 + QC_FOR_IMAGING low 59 + Stage-3.5 promotes 12 = 191 `[SOURCE: v2-and-step4 §1.5]`.

Stage-3 QC_FOR_IMAGING totals: 179 (120 high, 59 low) `[SOURCE: v2-and-step4 §1.3]`. Sought (191) − QC_FOR_IMAGING (179) = the 12 Stage-3.5 promotes.

**OSF registration boundary.** The OSF protocol submitted today carries the flow through "reports assessed for eligibility = 181" plus the registered eligibility criteria and the generic inter-rater (Cohen's kappa) method and threshold. The 47/134 split, the achieved kappa value, and the qualitative synthesis are manuscript content, executed at or after submission, consistent with PRISMA item 24 retrospective/prospective disclosure.

---

## 2. Screening exclusions by category (Stage 3, v2)

Primary-category distribution of the 6,839 screened records `[SOURCE: v2-and-step4 §1.3]`:

| Category | Count | Disposition |
|---|---:|---|
| OTHER | 1,381 | OUT |
| QUANTUM_DOTS | 1,211 | OUT |
| CLASSICAL_AI_FOR_IMAGING | 1,095 | OUT |
| CLINICAL_NON_IMAGING | 1,062 | OUT |
| FLUORESCENCE_PROBES | 924 | OUT |
| NANO_THERAPEUTICS | 612 | OUT |
| QUANTUM_SENSING_BIOMED | 238 | OUT (borderline; Stage-3.5 sub-screen, §2.1) |
| QUANTUM_INSPIRED_CLASSICAL | 73 | OUT |
| QUANTUM_CRYPTO_MED | 64 | OUT |
| **QC_FOR_IMAGING** | **179** | **IN (to full-text candidate set)** |
| **Total** | **6,839** | |

Out-of-scope subtotal = 6,660. After Stage-3.5 promotes 12 QUANTUM_SENSING_BIOMED records back to IN, net screening exclusions = 6,648.

### 2.1 Stage-3.5 ionising sub-screen (combined v1 + v2)

Re-evaluation of QUANTUM_SENSING_BIOMED records against a two-part criterion (ionising-modality whitelist AND quantum-sensing component role) `[SOURCE: step-3_5 handoff §3; v2-and-step4 §1.4]`:

| Sub-screen | Records evaluated | Promoted to IN |
|---|---:|---:|
| v1 (pilot) | 146 | 4 (all PET) |
| v2 (new records not in v1) | 96 | 8 (3 PET, 2 radiotherapy dosimetric, 1 proton radiography, 1 planar X-ray, 1 CT) |
| **Combined** | | **12** |

Note for the discussion: 11 of the 12 promotes were later reversed at full text as quantum-sensing / detector-physics studies (Section 4), under the primary quantum-computing inclusion criterion.

---

## 3. Reports sought but not retrieved (191 -> 181) — recovered

Ten of the 191 candidates were never retrieved as full text. All 10 appear in the `missing_pdfs` sheet of the candidate workbook with the note `no_oa_url_advertised` (no advertised open-access URL; PDF not obtained) `[VERIFIED: computed from step4_candidates_v2 vs extractions_ai; diff is exact, zero extracted papers fall outside the 191]`. This is the PRISMA 2020 "reports not retrieved" bucket. They were **not** assessed and are **not** part of either the 47 exclusions or the 134 included.

| DOI | Screen stage / confidence | Publisher | Title (truncated) |
|---|---|---|---|
| 10.1007/s00261-025-04900-4 | Step-3 high | Springer | Kidney cancer diagnosis and surgery selection by double decker ... |
| 10.1142/s0218001422520085 | Step-3 high | World Scientific | CPRO: Competitive Poor and Rich Optimizer-Enabled Deep Learning ... |
| 10.1142/s0218001425500132 | Step-3 high | World Scientific | An Advanced AI Approach-Based Skin Disease Prediction System ... |
| 10.1142/s2010324725400090 | Step-3 high | World Scientific | Deep Learning-Based Analysis of X-Ray Images for Bone Fracture ... |
| 10.4015/s1016237222500442 | Step-3 high | World Scientific | Taylor-IIWO: Taylor Improved Invasive Weed Optimization-Enabled ... |
| 10.4015/s1016237224500054 | Step-3 high | World Scientific | Automated Detection of Parkinson's Disease Based on Hybrid CNN ... |
| 10.1007/s11082-023-05684-x | Step-3 low | Springer | **RETRACTED ARTICLE:** Optical bio sensor based cancer cell detection ... |
| 10.1007/s11082-023-06187-5 | Step-3 low | Springer | **RETRACTED ARTICLE:** Neuro quantum computing based optoelectronic ... |
| 10.1007/s11082-023-06203-8 | Step-3 low | Springer | **RETRACTED ARTICLE:** Enhanced image diagnosing approach in medicine ... |
| 10.1088/1748-0221/18/11/c11011 | Step-3.5 promote / high | IOP | Quantum-imaging detection of secondary neutrons in proton radiotherapy ... |

Composition: 6 Step-3 high-confidence, 3 Step-3 low-confidence, 1 Stage-3.5 promote `[VERIFIED: computed]`.

Notes for Methods:
- **3 of the 10 are retracted** (Springer, *Optical and Quantum Electronics*), flagged "RETRACTED ARTICLE" in their titles `[VERIFIED: title strings in all_candidates]`. Even if retrieved they would be excluded; recording them as "not retrieved" is therefore conservative. The v2 metadata stage did not apply a Zotero-style retraction filter, so these reached the candidate set (unlike the v1 pilot, which removed 2 different retracted records at the Zotero stage; Section 6).
- **2 of the 10 had a co-author assigned for retrieval** but the PDF was not delivered: `10.1088/1748-0221/18/11/c11011` (initial GK, the lone Stage-3.5 promote in this set) and `10.4015/s1016237224500054` (initial MS) `[VERIFIED: PDF_retrieved_by column]`. These two are the only recoverable candidates if institutional access is later obtained; the other eight advertised no OA route.
- The not-retrieved set is dominated by World Scientific and Springer titles, consistent with the paywalled-publisher distribution recorded at the candidate stage `[SOURCE: v2-and-step4 §1.6]`.

---

## 4. Full-text exclusions (181 -> 134) — recovered composition

47 reports were excluded at full text by two mechanisms `[SOURCE: METHODS_RECORD_step5 §1; VERIFIED: counts and reasons recomputed from the two CSVs]`:

- **Automated paradigm/scope cull (Step 4.8): 16** (`excluded_fulltext.csv`)
- **Manual full-text adjudication (Step 5): 31** (`manual_fulltext_exclusions_v47.csv`)

### 4.1 Automated cull (16), by PRISMA reason `[VERIFIED]`

| Reason | n |
|---|---:|
| Quantum sensing / detector physics, not quantum computing | 10 |
| Secondary study (review / perspective) | 2 |
| Quantum cellular automata, not gate/annealing quantum computing | 2 |
| Quantum physics phenomenon (entanglement / error-correction), not quantum computing | 1 |
| Quantum-inspired classical method, no quantum computation | 1 |
| **Total** | **16** |

> **Correction preserved:** the Step-4.8 handoff prose described this set as "11 quantum sensing + 2 QCA + 2 review + 1 quantum-inspired." That split is wrong. The verified split is the table above (10 sensing, 1 physics-phenomenon as a distinct reason). Use the table; do not carry the handoff prose into Methods.

### 4.2 Manual adjudication (31), by PRISMA reason `[VERIFIED]`

| Reason | n |
|---|---:|
| Not a medical-imaging study (non-imaging medical data) | 9 |
| Not a medical-imaging study (non-medical data) | 9 |
| Secondary study (review / perspective) | 8 |
| Quantum instrumentation / physics, not an imaging application | 2 |
| No medical-imaging dataset (method demonstration on synthetic / non-imaging data) | 2 |
| Secondary / review (ejca; see §5) | 1 |
| **Total** | **31** |

### 4.3 Consolidated exclusion tally (47) `[SOURCE: METHODS_RECORD_step5 §1; VERIFIED: reproduces from 4.1 + 4.2]`

| Consolidated reason | n |
|---|---:|
| Secondary / review | 11 |
| Quantum sensing / detector physics | 10 |
| Non-imaging medical data | 9 |
| Non-medical data | 9 |
| Quantum cellular automata | 2 |
| Method-only (no medical-imaging dataset) | 2 |
| Instrumentation / physics | 2 |
| Physics phenomenon | 1 |
| Quantum-inspired classical | 1 |
| **Total** | **47** |

Reconciliation: secondary/review 11 = 2 (auto) + 8 (manual SECONDARY) + 1 (manual ejca). Sensing 10 = auto only. Instrumentation/physics 2 = manual; physics-phenomenon 1 = auto. All other rows map one-to-one `[VERIFIED]`.

Specific dispositions worth recording:
- `10.18280/ts.420531` is **excluded** (non-medical data); the previously flagged "confirm medical scope" item is resolved as an exclusion `[VERIFIED: in manual_fulltext_exclusions_v47.csv, NON_MEDICAL]`.

---

## 5. The 165 / 135 / 134 reconciliation (interim numbers superseded)

Two interim figures appear in earlier records and must not be carried into Methods as final:

- **165 included** appears in the Step-4.8 handoff. That is the count after the 16 automated exclusions only (181 − 16 = 165) and before the 31 manual exclusions `[SOURCE: step-4_8 §9]`.
- **46 excluded / 135 included** appears in `OSF_v0_6_submission_edits.md` (§9, §C). That is a snapshot taken after 30 of the 31 manual exclusions but before the final one `[SOURCE: OSF_v0_6_submission_edits §C]`.

The final figure is **47 excluded / 134 included**. The one-paper delta is `10.1016/j.ejca.2025.115632` (ejca), a journal-labelled "Current Perspective" with no dataset, results, or circuits, excluded as secondary/review during the Step-5 high-severity contradiction adjudication. Its exclusion moved excluded 46 -> 47, included 135 -> 134, and secondary/review 10 -> 11 `[SOURCE: step5_contradiction_adjudication_log §3, §7; VERIFIED: ejca present in manual_fulltext_exclusions_v47.csv, reason secondary/review]`.

Any place still showing 46/135 (including the OSF draft, if not yet edited) is the pre-ejca snapshot and is superseded by 47/134.

---

## 6. v1 pilot record (superseded; audit trail only)

The v1 open-access pilot is not the PRISMA corpus. It is retained because it locked the screening taxonomy and because some Stage-3.5 promotes originate there `[SOURCE: v2-and-step4 §1.1]`.

| v1 stage | Count | Source |
|---|---:|---|
| Raw hits (PubMed 1,569 + WoS 2,201 + IEEE Xplore 123) | 3,893 | `[SOURCE: step-1 §3]` |
| Unique DOIs after deduplication | 2,977 | `[SOURCE: step-1 §6]` |
| Imported to Zotero | 2,978 | `[SOURCE: step-2 §3]` |
| Removed at metadata stage (2 retracted, 1 duplicate-by-content, 1 non-English) | 4 | `[SOURCE: step-2 §8]` |
| Entering v1 screening | 2,974 | `[SOURCE: step-2 §8]` |

v1/v2 overlap: 2,904 shared; 74 v1-only (dropped in v2); 3,943 v2-only `[SOURCE: v2-and-step4 §1.2]`.

Per-database v2 raw counts: Web of Science 4,813, PubMed 3,127, IEEE Xplore 929 (total 8,869), of which 9 export entries carried no DOI `[VERIFIED: 1-Search/Round-2-All/DOIs.xlsx]`.

---

## 7. Provenance and reproducibility of the recovered numbers

- **191 vs 181 diff.** Lower-cased DOI set difference between the `all_candidates` sheet of `step4_candidates_v2*.xlsx` (191) and the `extractions` sheet of `extractions_ai.xlsx` (181). Result: exactly 10 in candidates-not-extracted, 0 extracted-not-in-candidates. The 10 cross-reference fully to the `missing_pdfs` sheet (`no_oa_url_advertised`). Reproducible from those two workbooks.
- **47 / 134 reconciliation.** Row counts and PRISMA-reason value counts of `excluded_fulltext.csv` (16), `manual_fulltext_exclusions_v47.csv` (31), `included_step5_v134.csv` (134). Checks: 134 + 16 + 31 = 181; included ∩ excluded = empty; ejca present in the manual set. Reproducible from those three files.
- **Authoritative records.** Final eligibility outcome and the Step-5 adjudication: `METHODS_RECORD_step5_for_paper.md` and `step5_contradiction_adjudication_log.md`. Upstream flow: `v2-and-step4-handoff.md` (Section 1).

---

## 8. Residual gaps not yet recovered (flag for completeness before submission)

1. **v2 per-database raw counts.** Resolved: Web of Science 4,813, PubMed 3,127, IEEE Xplore 929 (8,869), 9 without a DOI, 2,013 duplicates `[VERIFIED: 1-Search/Round-2-All/DOIs.xlsx]`.
2. **v2 metadata-stage retraction / non-English itemization.** v2 records only "no metadata retrievable = 8" before screening. Retracted and non-English handling at the v2 metadata stage is not itemized; note that 3 retracted papers consequently surfaced later in the not-retrieved set (Section 3), which is the documentable trace.
3. **Deposited OSF protocol file.** The file named `OSF_Protocol_QC_MedImaging_v0_6.docx` in the working project is a mislabeled v0.2-era markdown stub carrying the superseded screening-kappa text and no final PRISMA numbers `[VERIFIED: not a docx; zero hits for 181/134/47]`. The genuine deposited v0.6 protocol is the authoritative registration artifact and should be archived alongside this record.

---

## 9. PRISMA 2020 flow-diagram skeleton (box-by-box, for the figure)

```
Identification
  Records identified from databases (n = 8,869; 3 databases)
  Records removed before screening:
    Export entries with no DOI (n = 9)
    Duplicate records removed (n = 2,013)
    Records with no retrievable metadata (n = 8)

Screening
  Records screened (n = 6,839)
  Records excluded (n = 6,648)
    [out-of-scope categories per Section 2, net of 12 Stage-3.5 promotions]

  Reports sought for retrieval (n = 191)
  Reports not retrieved (n = 10)
    [no advertised OA route; 3 also retracted]

  Reports assessed for eligibility (n = 181)
  Reports excluded (n = 47):
    Secondary / review (11)
    Quantum sensing / detector physics (10)
    Non-imaging medical data (9)
    Non-medical data (9)
    Quantum cellular automata (2)
    Method-only, no imaging dataset (2)
    Instrumentation / physics (2)
    Physics phenomenon (1)
    Quantum-inspired classical (1)

Included
  Studies included in review (n = 134)
```

OSF registration is drawn through "Reports assessed for eligibility (n = 181)". The exclusion box (47) and the included box (134) are reported in the manuscript.

---

*End of record.*
