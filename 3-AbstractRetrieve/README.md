# 3-AbstractRetrieve — Abstract recovery and screening pipeline

This folder implements Step 3 of the systematic review pipeline for the
IEEE TRPMS invited review "Quantum computing for medical imaging applications"
(corresponding author: Laszlo Papp).

The folder went through two pipeline iterations. This README first explains
the v1 to v2 narrative, then provides a file-by-file map.

================================================================
PIPELINE NARRATIVE
================================================================

v1 (open-access scoping pass, executed 10 to 11 May 2026)
---------------------------------------------------------

The original keyword searches in PubMed, Web of Science, and IEEE Xplore on
30 January 2026 applied a free-full-text / open-access filter on PubMed and
IEEE Xplore. This yielded 2978 unique DOIs (after deduplication across the
three databases). Step-1 deliverable: inputs/DOI.xlsx.

Step-3 v1 then:
  1. Recovered abstracts via Crossref, OpenAlex, EuropePMC
     (scripts/v1/recover_abstracts.py).
  2. For records still missing an abstract, matched local PDFs from Zotero
     storage via an RDF inventory (scripts/v1/rdf_inventory.py) and extracted
     abstracts using pdftotext with Tesseract fallback (scripts/v1/pdf_extract.py).
  3. Merged everything into a canonical corpus
     (scripts/v1/merge_abstracts.py, scripts/v1/merge_pdf_abstracts.py),
     producing v1_archive/merged_dataset_OA_only_v1.json.
  4. Ran AI-assisted scope screening against a 10-class taxonomy via Claude
     Code subagents (orchestrator config in CLAUDE.md, subagent definitions
     in .claude/agents/), producing v1_archive/verdicts_OA_only_v1.jsonl.
  5. 88 papers reached the Step-4 candidate set (in-scope full-text review).

v2 (full corpus including paywalled papers, executed from 19 May 2026)
----------------------------------------------------------------------

A co-author observed that the open-access filter introduced a selection
bias. The searches were re-run without the OA filter, yielding 6847 unique
DOIs (inputs/DOIs-RemovedDuplicates.xlsx). Cross-check against v1:

  In both v1 and v2:        2904 (abstracts reusable)
  Only in v1 (dropped):       74 (all OUT-of-scope in v1, safely discarded)
  Only in v2 (new):         3943 (need abstract recovery + screening)
  v2 corpus total:          6847

v2 reuses validated v1 abstracts where the DOI matches AND the v1 abstract
passed validation (>=250 chars, >=30 words):
  2876 DOIs have valid abstracts reusable from v1.
  3971 DOIs need fresh abstract retrieval in v2.

The v2 abstract retrieval (recover_abstracts_v2.py) uses the same three
APIs and the same validation rules as v1. After it finishes, a merge step
(to be added in scripts/v2/) will combine reusable v1 abstracts with fresh
v2 abstracts into a new merged_dataset.json for the 6847-record corpus.
AI-assisted scope screening will then be re-run on the full corpus.

v1 was an open-access-only scoping iteration; v2 is the authoritative search
and its PRISMA flow supersedes v1. The 74 DOIs present only in v1 and the 25
v1 records that failed abstract retrieval belong to the superseded iteration
and are archived here (intermediates/, v1_archive/) for audit.

================================================================
DIRECTORY MAP
================================================================

3-AbstractRetrieve/
  README.md                       this file
  CLAUDE.md                       Claude Code orchestrator system instructions
  .claude/                        Claude Code subagent definitions, hooks
  .venv/                          Python virtualenv (Python 3.8)

  inputs/
    DOI.xlsx                      v1 input: 2978 DOIs with Step-2 status col
    DOIs-RemovedDuplicates.xlsx   v2 input (authoritative): 6847 DOIs
    zotero_csl_json_v1/
      IEEE-QC-NoDuplicates-*.json v1 input: 6 Zotero CSL-JSON exports

  scripts/
    v1/                           v1 pipeline scripts (OA-only iteration)
      recover_abstracts.py          Crossref/OpenAlex/EuropePMC fetcher
      merge_abstracts.py            merge recovered abstracts into corpus
      rdf_inventory.py              index local PDFs from Zotero RDF
      pdf_extract.py                extract abstract text from PDFs
      merge_pdf_abstracts.py        merge PDF-extracted abstracts
      batch_helper.py               prepare and validate screening batches
      rescue_quarantine.py          rescue quarantined verdicts (relaxed match)
      finalize_residue.py           finalize remaining quarantined verdicts
      verdict_diff.py               diff verdicts pre/post PDF rescue
      _unused_screen_scope.py       NEVER EXECUTED. Alternative API-based
                                    screener considered but rejected in
                                    favour of the Claude Code subagent path
                                    (which uses the existing Pro Max
                                    subscription). Kept for audit trail.
    v2/                           v2 pipeline scripts
      (recover_abstracts_v2.py moves here after fetch finishes)
    debug/                        one-off diagnostic scripts (v1 era)
      inspect_pdfs.py
      inspect_orphans.py
      inspect_parse.py
      inspect_q.py
      inspect_q2.py

  v1_archive/                     v1 outputs, frozen
    merged_dataset_OA_only_v1.json     v1 canonical corpus (2974 records)
    verdicts_OA_only_v1.jsonl          v1 screening verdicts (2974 verdicts)
    verdicts_pre_pdf_rescue.jsonl      v1 verdicts snapshot pre PDF rescue
    verdicts_manual.jsonl              v1 manual interventions log
    verdicts_manual_template.jsonl     v1 manual-intervention template
    title_only_records_OA_only_v1.json v1 unprocessable records (25)
    rdf_pdf_index.csv                  v1 RDF-to-local-PDF lookup
    _archive_v1_backups/               v1 .bak files and intermediates

  v2_active/                      v2 outputs (populated as v2 progresses)

  intermediates/                  v2 diagnostic txt files
    added_dois.txt                  3943 DOIs only in v2 corpus
    dropped_dois.txt                  74 DOIs only in v1 corpus

  Top-level (active during v2 fetch; moves into v2_active/ and scripts/v2/
  after the fetch completes):
    recover_abstracts_v2.py       v2 abstract fetcher (currently running)
    recovered_abstracts_v2.jsonl  v2 fetch results (append-only)
    recover_v2.log                v2 fetch log
    recover_v2_full.log           v2 tee'd full log
    dois_to_fetch.txt             v2 input: 3971 DOIs to fetch
    dois_reusable_from_v1.txt     v2 input: 2876 DOIs to reuse from v1

================================================================
REPRODUCIBILITY
================================================================

All v1 and v2 scripts are deposited in this repository. The OSF protocol
(../docs/OSF_protocol_submitted_2026-06-30.md) documents the methodology and
is the source of truth for criteria and decisions.

Last updated: 2026-05-19 (during v2 fetch execution); wording revised 2026-09-09.
