# OSF REGISTRATION (submitted) — Quantum Computing for Medical Imaging Applications

> **What this file is.** A faithful capture of the **submitted** OSF registration for the review, as deposited (~2026-06-30) using the OSF **Generalized Systematic Review Registration Form**. Source: the registration form text (author's own document, reproduced for internal use). This is the **registered plan**; the manuscript Methods must describe what was registered and note any post-deposit deviation. The form uses the named sections below, NOT a `§`-numbered structure. The project file `OSF_Protocol_QC_MedImaging_v0_6.docx` is a stale v0.2 stub and is NOT this protocol.
>
> **Deposit timing.** Retrospective-then-prospective. Stages 1–4 executed before deposit; Stage 5 in progress at deposit; Stages 6–7 prospective at deposit. (Post-deposit, Stages 5–7 were completed; see the enrichment handoff and METHODS_RECORD. NOTE the deposit's internal inconsistency: "Review stages" says Stage 5 in progress, "Extraction stages" says Stage 5 complete — reconcile in the manuscript, Stage 5 completed post-deposit.)

---

## REVIEW METHODS

**Type of review.** Systematic review with narrative synthesis. No meta-analysis (anticipated heterogeneity). Protocol developed following PRISMA 2020 (Page et al., BMJ 2021).

**Review stages.**
- Stage 1: DOI consolidation and deduplication (complete).
- Stage 2: Metadata enrichment and manual filtering (complete).
- Stage 3: Abstract screening — AI-assisted (complete).
- Stage 3.5: Quantum-sensing ionising sub-screen (complete).
- Stage 4: Full-text screening and data extraction — AI-assisted with human cross-check (complete).
- Stage 5: Subcategorisation (in progress at deposit).
- Stage 6: Inter-rater kappa audit (prospective at deposit).
- Stage 7: Manuscript composition and reporting (prospective at deposit).

**Why not PROSPERO.** PROSPERO accepts only registrations before data extraction; search, screening, and extraction were already executed at deposit.

**Start date** 30 January 2026. **Estimated completion** end of July 2026.

**Background (condensed).** Quantum computing (superposition, entanglement, interference) has matured into cloud-accessible hardware (superconducting, trapped-ion, photonic, neutral-atom) with hybrid quantum-classical algorithms (VQE, QAOA, QML) for the NISQ era. Medical imaging is computationally intensive (reconstruction, segmentation, classification, denoising, radiomics, dosimetry). "Quantum" is frequently overloaded onto adjacent fields (quantum dots, fluorescence probes, magnetometry, ghost imaging, cryptography, "quantum-inspired" classical methods), motivating a systematic delineation. **Invited by the Editor-in-Chief of IEEE Transactions on Radiation and Plasma Medical Sciences (TRPMS)**; scope is ionising-radiation imaging hardware/software plus a substantial applications section requested by the Editor.

**Primary research questions.**
1. Which medical imaging tasks have been addressed using quantum-computing methods?
2. Which imaging modalities appear most often, and what is the distribution with respect to ionising-radiation modalities?
3. What datasets are used (real clinical, public benchmark, synthetic), typical sample sizes, and to what extent are classical baselines reported?
4. Which quantum-computing paradigms are most frequent, and on which hardware/simulator frameworks?
5. What validation strength is demonstrated (cross-validation, external validation, statistical testing, reproducibility)?
6. What clinical translational maturity is demonstrated, and which application areas appear closest to clinical usefulness?
7. What open methodological/engineering challenges are identified (hardware: qubit count, decoherence; software: compilation, error mitigation; data: encoding of medical images into quantum states)?

**Secondary research questions.**
1. Fraction reporting hardware-aware circuit synthesis/mapping/transpilation, and correlation with translational-maturity claims.
2. Distribution by geography (corresponding-author country), venue, and academic vs industrial/private-sector affiliation.

**Hypotheses.** None; descriptive characterisation of the state of the art.

**Outcomes / main variables (methodological and descriptive).**
1. Imaging-task performance metrics (PSNR, SSIM, Dice, accuracy, AUC, etc.).
2. Quantum-resource metrics (qubit count, circuit depth, gate count, training cost).
3. Classical-baseline comparisons (quantum-advantage claims).
4. Validation strength (cross-validation, external validation, statistical testing, reproducibility; open code, reproducible pipeline, parameter transparency).
5. Translational maturity (proof of concept, in-silico, retrospective clinical, prospective clinical).

**Variables for subgroup/cross-tab/sensitivity analyses.**
- **Axis A** (imaging task, multi-label) — applications Results grouping.
- **Axis B** (quantum paradigm) — technical Results grouping.
- **Axis C** (imaging modality with ionising-radiation flag) — applications Results grouping.
- **Axis D** (hardware execution context + hardware-aware compilation level) — cross-cutting maturity signal.
- Methodological-quality indicators: baseline-rigour grade, dataset-realism grade, reproducibility tier, quantum-resource-accounting completeness (0–4), error-mitigation clarity, explainability mechanism, reporting-standard adherence (CLAIM 2024, TRIPOD+AI 2024, METRICS 2024).
- Bibliometric: corresponding-author country, venue, academic vs industrial.

**Software.** Zotero (dedup). Microsoft Excel (screening/extraction/audit). **Claude Opus 4.7** with extended thinking via Claude Code (independent parallel analysis at abstract screening, full-text screening, extraction, subcategorisation; human analysis completed first, AI compared post-hoc). **After a Claude Code update removed Opus 4.7, a quote-grounding refill pass used Claude Opus 4.8; this change applied only to the refill and affected no eligibility or inclusion decision.** Python (Crossref/OpenAlex/EuropePMC APIs; JSON schema validation; audit logging).

**Funding.** Unfunded. **Conflicts of interest.** None.

**Overlapping authorships.** Some co-authors are authors on papers that may be included. All co-authors remain involved in all stages (screening, extraction, quality assessment, synthesis) because excluding them would compromise consistent application given team size. Mitigation: every included co-authored paper is explicitly flagged in a dedicated manuscript subsection so readers can assess influence. Considered sufficient for a descriptive methodological review (no pooled effect estimate).

---

## SEARCH STRATEGY

**Databases.** PubMed; Web of Science Core Collection; IEEE Xplore.

**Grey literature.** None (no hand-searching, citation-chasing, or expert contact).

**Query strings.**
- **v1 (30 Jan 2026):** PubMed `(quantum) AND (medical OR clinical) AND (imaging OR image)` (last 5 years, free full text, English, exclude preprints, page 200); WoS `(ALL=(quantum)) AND (ALL=(medical) OR ALL=(clinical)) AND (ALL=(imaging) OR ALL=(image))` (filters applied later in Zotero); IEEE Xplore `("All Metadata":quantum) AND ("All Metadata":medical OR "All Metadata":clinical) AND ("All Metadata":imaging OR "All Metadata":image)` (open access, 2021–2026, journals only).
- **v2 (18 May 2026):** same query strings; filters last 5 years / English / exclude preprints (PubMed), document type article + last 5 years + English (WoS), 2021–2026 journals only (IEEE Xplore); open-access filters removed.

**Search validation.** None formal. Full URLs in `query_links.txt` on OSF.

**Search expiration/repetition.** Two passes. v1 pilot (30 Jan 2026, OA filters on) → **2978 unique DOIs**, used to develop and lock the taxonomy. v2 full (18 May 2026, OA filters off) → **canonical corpus of 6847 unique DOIs**. No further repetition.

**Search strategy justification.** Three databases (biomedical, multidisciplinary, engineering). Single keyword 'quantum' to keep reproducible and avoid term explosion. Five-year window 2021–2026 (practical quantum hardware availability). Two-pass design: v1 locks taxonomy, v2 produces corpus.

**Deduplication counts.** v1 and v2 share **2904** DOIs; **74** unique to v1 (verified out-of-scope); **3943** unique to v2. Cross-database dedup by lower-casing DOIs + exact string match. For v2, metadata/abstracts for the 3943 new DOIs fetched from Crossref/OpenAlex/EuropePMC; Zotero manual filtering not repeated for v2-only records.

---

## SCREENING

**Screening stages.**
- Stage 1: Deduplication (raw exports → single workbook; DOIs lower-cased, exact-match dedup).
- Stage 2: Metadata enrichment + manual filtering (Zotero; flag retractions, duplicates, non-English).
- Stage 3: Abstract screening — AI-assisted (Claude Opus 4.7, extended thinking, Claude Code); ten mutually exclusive categories; QC_FOR_IMAGING high-confidence = IN; low-confidence = uncertain; else OUT. Human cross-check on all uncertain + a stratified random sample for kappa.
- Stage 3.5: Quantum-sensing sub-screen — dedicated AI subagent screens QUANTUM_SENSING_BIOMED for ionising-radiation imaging context; all promote_to_IN cross-checked by a co-author.
- Stage 4: Full-text screening — human-led verdict for every record; independent AI analysis in parallel; discrepancies resolved by consensus with a third co-author.

**Ten-category taxonomy.** QC_FOR_IMAGING (IN); QUANTUM_DOTS; NANO_THERAPEUTICS; FLUORESCENCE_PROBES; QUANTUM_SENSING_BIOMED (OUT, subject to Stage 3.5); QUANTUM_CRYPTO_MED; QUANTUM_INSPIRED_CLASSICAL; CLASSICAL_AI_FOR_IMAGING; CLINICAL_NON_IMAGING; OTHER — all others OUT.

**Inclusion criteria.** Peer-reviewed journal articles; published 1 Jan 2021 to search date 18 May 2026; English; implementing/simulating/evaluating a quantum-computing method (QML, VQE, QAOA, quantum kernels, FRQI, NEQR, parameterised quantum circuits, hybrid quantum-classical) on real quantum hardware (IBM, IonQ, Rigetti, Quantinuum, Pasqal, QuEra) or a simulator (Qiskit Aer, PennyLane, Cirq, Microsoft QDK, Strawberry Fields); applied to a medical imaging task (reconstruction, analysis, segmentation, classification, denoising, super-resolution, radiomics, fusion, instrumentation, dosimetry simulation).

**Exclusion criteria.** Conference papers, abstracts, theses, preprints, editorials, commentaries, letters, retracted, predatory-journal papers; non-English; outside date window; quantum-dot chemistry without an algorithmic quantum-computing component; nano-therapeutics/photothermal/photodynamic/drug-delivery/theranostics; fluorescence probes/dyes/biosensors/microscopy without a quantum-computing component; quantum sensing (OPM, NV magnetometry, ghost imaging, quantum illumination, single-photon) without a quantum-computing component (EXCEPT ionising-radiation imaging contexts → Stage 3.5); quantum cryptography/QKD/watermarking/blockchain on medical images; "quantum-inspired" classical algorithms without quantum hardware/simulator execution; classical AI/ML/DL for medical imaging with no quantum component; clinical trials/biomarker/epidemiology/outcomes without an imaging methodology component.

**Predatory-journal operational definition (fully deterministic; both must hold to exclude).** (i) Flag: journal appears at Stage 4 on EITHER Cabells Predatory Reports (MedUni Vienna subscription; criteria v1.1) OR Retraction Watch Hijacked Journal Checker (Beall's List NOT used). (ii) No override: journal NOT currently indexed in MEDLINE ("indexed for MEDLINE"; PMC deposit insufficient) AND NOT in WoS Core at SCIE/SSCI/AHCI (ESCI insufficient). Indexing in MEDLINE or WoS Core = auto-retain override. Full audit record kept for every flagged paper. Expected exclusions very low (curated sources).

**Screened fields / blinding.** Title + abstract visible at Stage 3; no blinding (journal/authors/year visible but not used as criteria); classifier instructed to use title+abstract content only.

**Screening reliability.** Stage 3.5: 100% of promote_to_IN + 10% random sample of stay_OUT cross-checked. Stage 4: every AI verdict cross-checked by a human against the full PDF. **Stage 6 (kappa):** subset independently extracted by co-authors, compared on cells both completed; single composite Cohen's κ by stratified pooling (observed agreement pooled across scored cells; chance agreement a cell-count-weighted average of per-field marginals, not a naive pooled confusion matrix). Categorical/boolean scored; numeric/free-text descriptive. Human values mapped to canonical vocabulary via a frozen symmetric crosswalk (deposited); vocabulary-normalised κ reported. **Acceptance threshold composite κ ≥ 0.61 (Landis & Koch 1977).** Residual disagreements adjudicated against the source paper.

**Screening flow counts.** 6847 unique DOIs; 2904 shared v1/v2; 74 unique v1; 3943 unique v2. 2904 common records reused v1 verdicts (flag `_reused_from_v1`) without re-screening. 8 v2 DOIs had no retrievable metadata → PRISMA-excluded ("no metadata retrievable") → **6839 records entering Stage 3** (3935 fresh v2-only + 2904 reused). Stage 3.5: **12 records promoted to IN** across v1+v2.

**Screening artefacts on OSF:** `query_links.txt`, `DOIs.xlsx`, `removed_duplicates.png` (uploaded at registration); plus Stage 3 verdict JSON, audit logs, pipeline scripts, subagent prompts (deposited).

---

## EXTRACTION

**Entities to extract.**
1. **Bibliographic:** DOI, title, authors, year, journal, publisher, corresponding-author country, funding declared.
2. **Quantum-computing characterisation:** paradigm; hardware platform (vendor, qubit count, qubit modality; real vs simulator); simulator framework; circuit depth, parameter count, ansatz family; image encoding; error-mitigation strategy; hardware-aware synthesis/mapping/transpilation level {none reported / default transpilation / hardware-aware mapping / pulse-level}; circuit-complexity readout (qubit count, circuit depth, total or two-qubit gate count, shot count) each verbatim-or-"not reported", each contributing to a **quantum-resource-accounting completeness score (0–4)**; extended image-encoding vocabulary (basis, angle, amplitude, FRQI, NEQR, NASS, MCRQI, NCQI, BRQI, hybrid, not applicable).
3. **Imaging characterisation:** modality (+multi-modal flag); anatomy/disease context; imaging task; dataset (public benchmark w/ name; private clinical w/ size; synthetic w/ generator); sample size (train/val/test); **ionising-radiation flag** derived deterministically from modality (ionising = PET, SPECT, CT, photon-counting CT, planar X-ray, mammography, fluoroscopy, gamma camera, scintigraphy, Compton camera, proton CT/radiography, EPID, in-vivo dosimetric; non-ionising = MRI, ultrasound, OCT, fundus, histopathology, dermoscopy, microscopy).
4. **Validation and methodological quality:** classical baseline present (+identification); performance metrics for quantum and baseline; cross-validation, external test set, statistical testing (+method); reproducibility (code/data/weights release); computational cost; **baseline-rigour grade** {none / weak / matched-data / matched-data+matched-compute / +statistical significance test}; **dataset-realism grade** {toy / public benchmark / private single-centre / private multi-centre / prospective clinical}; **reproducibility tier**; **reporting-standard adherence** {none / CLAIM 2024 / TRIPOD+AI / METRICS 2024 / multiple}.
5. **Translational maturity:** translational level; clinical-impact claim (authors); independent clinical-impact assessment by the review team (low/moderate/high +justification); **clinical-translation readiness score (0–4)** = sample-size justification + external/independent validation + regulatory pathway addressed + prospective clinical evaluation (each 0/1, quote-grounded).

**Extraction stages.**
- Stage 4 — Full-text screening and extraction (complete): AI-assisted pipeline (**Claude Opus 4.7 and Claude Opus 4.8** via Claude Code subagents) on full PDF text, with quote grounding against the PDF; every AI extraction cross-checked by a human co-author.
- Stage 5 — Subcategorisation (complete in Extraction stages section): four orthogonal axes A/B/C/D; AI-assisted with human cross-check; each axis operationalised by a deterministic derived field computed from already-extracted quote-grounded values (no re-extraction, no model inference); full derived-field spec in the deposited data dictionary.

**Extractor instructions.** Opus 4.7 extended thinking, isolated Claude Code subagents; fixed prompt with JSON schema, controlled vocabularies, decision rules; **mandatory quote grounding** (verbatim quote from full PDF per value); JSON-schema validated. Human cross-check per record: (a) quote is a genuine PDF substring, (b) value matches quote, (c) vocabulary consistent with rules.

**Extractor masking.** None (descriptive review).

**Extraction reliability.** Every AI verdict cross-checked by ≥1 co-author; rubric criteria rated by ≥1 AI + 1 human; disagreements resolved by consensus + third-co-author adjudicator. No formal IRR statistic pre-specified for extraction itself; the only formal reliability statistic is the Stage-3 kappa (performed as Stage 6). Manuscript reports number/nature of extraction disagreements.

---

## SYNTHESIS AND QUALITY ASSESSMENT

**Planned data transformations.** No effect-size conversion (no meta-analysis). Two derived scores: quantum-resource-accounting completeness (0–4); clinical-translation readiness (0–4). Ionising-radiation flag derived from modality via the eligibility whitelist. All other fields used directly.

**Missing data.** Not-reported items recorded as 'not reported', treated as missing in aggregate; authors not contacted; per-item missingness reported. Full text not retrievable → PRISMA exclusion "full text not retrievable", listed explicitly.

**Data validation.** Stage 3 abstract rules (≥250 chars, ≥30 words, not placeholder, not substring of title, token Jaccard <0.7). Stage 3 hallucination controls (verbatim evidence quote validated as source substring; JSON-schema validation; every model call logged: model ID, prompt hash, raw response, latency, validation outcome; failing records quarantined + rescued via relaxed-substring/field-swap with audit flags). Stage 4: each quote verified by the human cross-check as a genuine PDF substring. Retractions excluded at Stage 2 (2 retracted papers); no further retraction monitoring post-deposit.

**Quality assessment.** Standard clinical-trial RoB tools (Cochrane RoB 2, ROBINS-I) NOT applicable; a **custom rubric locked before extraction**: dataset realism (toy/benchmark/real clinical); sample-size justification (y/n); classical baseline presence + identification; statistical testing (y/n, method); external/independent validation (y/n); honest reporting of quantum resources + hardware-vs-simulator distinction; reproducibility (code/data); quantum-resource-accounting completeness (0–4); error-mitigation-strategy clarity {none / readout-only / ZNE / PEC / dynamical decoupling / T-REx / Clifford-data regression / composite}; explainability mechanism {none / classical XAI / quantum XAI / both}. Each criterion rated low/moderate/high concern; a summary methodological-quality category derived per paper; each rating ≥1 AI + 1 human, disagreements → consensus + third-co-author adjudicator.

**Synthesis plan.** Narrative + aggregate descriptive statistics + figures; no meta-analysis. Planned aggregate analyses: distributions by year, modality, imaging task, paradigm, hardware platform; by corresponding-author country and venue; by dataset type; proportion reporting classical baseline / statistical testing / releasing code or data; translational-maturity and methodological-quality distributions; cross-tabs paradigm × task and modality × task. Results organised by Axis B (technical) and Axis A × Axis C (applications); Axis D as cross-table + maturity signal in every subsection.

**Inference criteria.** None formal; descriptive (frequencies, proportions, narrative patterns).

**Synthesist blinding.** None.

**Publication bias.** No formal analysis; grey literature/preprints excluded by design (acknowledged limitation).

**Sensitivity analyses.** Restrict synthesis to papers satisfying **stricter criteria: real clinical dataset, classical baseline present, statistical testing performed**; report robustness of headline statements to these restrictions. (This defines the quality-fit kernel = 8 papers; see enrichment handoff.)

**Data management/sharing.** Screening artefacts, extracted data, audit logs, aggregated tables (XLSX) deposited on OSF; no embargo. Synthesis is prospective; results reported per PRISMA 2020 (flow diagram + checklist).

---

## KEY REGISTERED NUMBERS (quick reference for Methods/PRISMA)

- v1 pilot (30 Jan 2026): 2978 unique DOIs (taxonomy lock).
- v2 full (18 May 2026): 6847 unique DOIs (canonical). Shared 2904; unique v1 74; unique v2 3943.
- 8 v2 DOIs no metadata → excluded → **6839 into Stage 3**.
- Stage 3.5: 12 promoted to IN.
- 2 retracted papers excluded at Stage 2.
- Kappa threshold: composite κ ≥ 0.61.
- Downstream (post-deposit, from METHODS_RECORD / handoffs, NOT in the registration): **N=134 included**; κ=0.669 / κ=0.449; supplemental-enrichment pass; quality-fit kernel = 8 papers.
