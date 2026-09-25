# Quantum Computing for Medical Imaging Applications: systematic review pipeline

Code, configuration, and derived data for the IEEE TRPMS invited systematic review
"Quantum Computing for Medical Imaging Applications" (corresponding author: Laszlo Papp,
Medical University of Vienna). The review is PRISMA-compliant and
LLM-assisted; this repository is the reproducibility deposit referenced in the OSF
registration (docs/OSF_protocol_submitted_2026-06-30.md). Post-registration changes are
recorded in the Transparent Changes log attached to the OSF project.

Locked corpus numbers: 6,839 records screened, 181 full texts assessed, 48 excluded at
full text, 133 included, 7 quality-fit kernel papers. Inter-rater agreement on
extraction: composite stratified kappa 0.669 against the registered 0.61 threshold
(alias-only floor 0.449).

## What is and is not here

Included: every pipeline script, Claude Code orchestrator file (CLAUDE.md), subagent
definition (.claude/agents/*.md), screening and extraction verdict file, adjudication
record, kappa input, provenance log, the registered protocol, and the PRISMA record.

Excluded by policy: publisher PDFs, any text extracted from them (pdftotext output, OCR,
page rasters, chunk files), abstracts and raw database exports (Web of Science, PubMed,
IEEE Xplore, Zotero), the supplementary-material corpus fetched from authors' deposits,
API caches, and per-chunk LLM task files that embed paper or supplement text. What remains
of those inputs is their identifiers: DOI lists, query strings, and file manifests.
Manuscript drafts and per-reviewer appraisal forms are not part of this deposit.

Grounding quotes: verdict and extraction files carry short verbatim excerpts (field-bound,
longest 492 characters) that anchor each extracted value to the source text. They are the
traceability evidence for every extracted value and are retained deliberately.

## Layout

```
1-Search/               query strings, DOI lists per round, PRISMA dedup screenshots
2-Metadata_Analysis/    DOI-centred metadata merge and online enrichment scripts, DOI QC workbooks
DOIRetrieve/            open-access PDF retrieval scripts (Unpaywall, OpenAlex, EuropePMC), sample inputs
3-AbstractRetrieve/     Step 3: abstract recovery and AI scope screening (v1 OA-only, v2 full corpus)
3.5-IonisingPromote/    Step 3.5: ionising-sensing sub-screen of QUANTUM_SENSING_BIOMED records
4-FullTextExtraction/   Step 4: full-text candidate build, PDF fetch, and extract_v1/ five-pass extraction
5-Adjudication/         Step 5: contradiction adjudication and eligibility cull
6-InterRaterKappa/      Step 6: kappa.py, frozen crosswalk, ai.xlsx and human.xlsx inputs, diffs
7-Manuscript/           N=133 recompute output, post-appraisal correction overlay, supp_enrich/ enrichment
docs/                   registered protocol, PRISMA record, methods record, post-deposit exclusion decision
```

Each step folder that was driven by Claude Code has its own CLAUDE.md and .claude/agents/.
These are four independent project-level installations, not one root configuration; run
Claude Code from inside the step folder for the agents to register.

## Pipeline order

1. 1-Search: database queries (round 1 on 30 Jan 2026, open-access filtered; round 2 on 18 May 2026, unfiltered), deduplication to DOI lists.
2. 2-Metadata_Analysis: `merge_doi_metadata_v2.py` merges exports on DOI; `enrich_missing_doi_metadata_online_v3_freeplan.py` fills gaps from Crossref, OpenAlex, Semantic Scholar and Unpaywall. Per-field source provenance: `provenance_online_enrichment_v3.json` (root).
3. 3-AbstractRetrieve: `scripts/v2/recover_abstracts_v2.py`, `recover_titles_v2.py`, `merge_v2_corpus.py`, `seed_v2_verdicts.py` build the 6,847-record v2 corpus and pre-seed 2,874 v1 verdicts. `CLAUDE.md` with `scripts/v2/batch_helper.py` drives the `screen-papers` subagent to `v2_active/verdicts.jsonl` (6,839 verdicts after 8 no-metadata exclusions). `scripts/v2/rescue_v2_quarantine*.py` handle validation failures. v1 (open-access scoping run, 10 to 11 May 2026) is archived under `v1_archive/` with its scripts under `scripts/v1/`; the folder README gives the v1-to-v2 narrative.
4. 3.5-IonisingPromote: `scripts/v2/prepare_3_5_v2_input.py` selects the 96 new sensing records; `CLAUDE.md` with `batch_helper_3_5_v2.py` drives `ionising-promote`; `report_3_5_v2.py` and `audit_3_5_v2_sampling.py` report and audit. v1 (146 records, 4 promotes) under `v1_archive/`.
5. 4-FullTextExtraction: `build_step4_candidates.py`, `fetch_oa_pdfs.py`, `fetch_missing_pdfs.py`, `consolidate_pdfs.py` assemble the full-text set. In `extract_v1/`: `pdf_text_extract.py`, `pdf_page_render.py`, `ocr_pages.py`, `build_augmented_text.py`, `section_tagger.py`, `chunker.py` prepare the hybrid text-plus-image feed; `CLAUDE.md` with `batch_helper_step4.py` drives passes A to E (`extract-pass-a` to `-d`, `reconcile-extraction`); `validate_extraction.py`, `rescue_extraction.py`, `retry_quarantined.py`, `validate_retry.py`, `refill_bc.py`, `validate_refill.py` enforce quote grounding; `merge_passes.py`, `normalize_extractions.py`, `compute_derived.py`, `extractions_to_xlsx.py` produce `extractions_merged.jsonl`, `extractions_derived.jsonl`, `extractions_ai.xlsx`, `extraction_human_blank.xlsx`. Axis scripts (`modality_axis.py`, `imaging_task_axis.py`, `paradigm_axis_b.py`, `paradigm_subcategory.py`, `hardware_execution.py`, `execution_context_axis.py`) implement the registered subcategorisations. `cull_eligibility.py` applies the full-text eligibility cull; `predatory_check.py` screens venues.
6. 5-Adjudication: `apply_step5_contradiction_fixes.py` applies the eleven adjudicated dispositions recorded in `step5_contradiction_adjudication_log.md` and writes `included_step5_v134.csv` and `manual_fulltext_exclusions_v47.csv`; `fix_radphyschem.py` is the single-field corrector used on an already-patched file. The committed `extractions_merged.jsonl` in `4-FullTextExtraction/extract_v1/` is the post-adjudication state; the two `extractions_merged_step5.jsonl` files there are the intermediate states before the final review.
7. 6-InterRaterKappa: `kappa.py` with `kappa_crosswalk_frozen.json` on `ai.xlsx` and `human.xlsx`.
8. 7-Manuscript: `supp_enrich/` phases 0 to 4 (`phase0_build_targets.py`, `phase1_supp_links.py`, `harvest_known_links.py`, `merge_links.py`, `fetch_supp.py`, `triage_corpus.py`, `phase4_filter.py`, `phase4_normalize.py`, `phase4_harness.py`, `phase4_merge.py`, `phase4_overlay.py`) enrich the extraction table from authors' supplements and code deposits into `out/extractions_ai_enriched_v134.xlsx`; `recompute_v133.py` (root) recomputes all corpus statistics at N=133 with output in `provenance/recompute_v133_output.txt`; `provenance/extraction_corrections_post_appraisal.json` is a post-appraisal overlay that any recompute must apply before tabulating `dataset_realism_grade` (see the note inside the file); `build_bibliography.py` (root) builds `supp_enrich/out/references.bib`.

Root-level scripts (`recompute_v133.py`, `build_bibliography.py`) are kept at the root so
that their relative paths remain valid.

## Reproducing the locked numbers

- Screening counts: `python scripts/v2/batch_helper.py summary` in 3-AbstractRetrieve.
- Full-text flow (181 / 48 / 133): `4-FullTextExtraction/extract_v1/cull_summary.txt`, `5-Adjudication/step5_fix_report.txt`, `docs/PRISMA_inclusion_exclusion_record_recovered.md`, `docs/DECISION_N133_access_exclusion.md`.
- Corpus statistics at N=133: `python recompute_v133.py` reproduces `7-Manuscript/provenance/recompute_v133_output.txt`.
- Kappa: `python 6-InterRaterKappa/kappa.py`; method in `docs/METHODS_RECORD_step5_for_paper.md` and the registration, section 12.

## Environment

WSL Ubuntu 20.04, Python 3.8 per-step virtualenvs (`.venv/` inside 3-AbstractRetrieve,
3.5-IonisingPromote, 4-FullTextExtraction/extract_v1, 7-Manuscript/supp_enrich; not
committed; `requirements.txt` frozen from the extract_v1 venv). System tools:
poppler-utils (pdftotext, pdffonts, pdftoppm), tesseract-ocr. LLM steps ran in Claude Code
(Claude Opus 4.7; Opus 4.8 for the Pass-B/C quote-grounding refill and the supplemental
enrichment, as disclosed in the registration) with per-step subagents; they are not
re-runnable byte-for-byte, which is why every verdict file is committed.

## Licence

Code (scripts, CLAUDE.md, agent definitions) is licensed under Apache-2.0 (LICENSE).
Data files and the documents under docs/ are licensed under CC BY 4.0 (LICENSE-DATA).
Quoted passages inside the data files remain the copyright of their original authors
and publishers and are reproduced under the right of quotation with DOI attribution;
see NOTICE. Cite the review when reusing the data.

## Notes for anyone recomputing

- The `pdf_file` column of the enriched workbook is a logical identifier, not a filesystem
  path. The `country_corresponding` column is unnormalised.
- Three derived values were not recomputed after later edits. (1) For
  10.1109/access.2025.3531407, Step 5 corrected `real_or_simulator` to simulator but
  `hardware_modality` still reads superconducting, so `hardware_execution_subcategory`
  counts it as hardware; `execution_context (derived)` is the authoritative execution field
  (21 studies on real hardware). (2) `multimodal (derived)` for 10.1007/s00259-023-06362-6
  predates the supplement-filled secondary modality (MRI). (3)
  `quantum_resource_accounting_completeness (derived)` was computed on the main-text
  extraction before the supplementary pass, which later filled four shot counts; the
  manuscript reports the score on the main text.
- The quantum-versus-classical comparison in Section III-J of the manuscript and Figure F3
  were computed from per-paper verification records of the reported quantum and classical
  metrics, and the circuit-width summary in Figure F2 from a parse of `qubit_count`; these
  intermediate records are not part of this deposit.
