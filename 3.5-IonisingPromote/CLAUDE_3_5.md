# CLAUDE_3_5.md — Stage 3.5 orchestration instructions

You are the orchestrator for Stage 3.5 of the IEEE TRPMS systematic review on
"Quantum computing for medical imaging applications". The review owner is
Laszlo Papp. The screen-papers Stage-3 work is complete and immutable. Your
single task is to run the Stage 3.5 ionising-sensing sub-screen.

## Inputs you should expect on disk

- `pending_3_5/batch_NNNN.json` — JSON arrays of up to 5 input records each,
  produced by `batch_helper_3_5.py prepare`. The fields per record are:
  `record_id`, `doi`, `title`, `abstract`, `abstract_provenance`,
  `step3_primary_category` (always "QUANTUM_SENSING_BIOMED"),
  `step3_primary_quote`, `step3_confidence`, `step3_reasoning`.
- `.claude/agents/ionising-promote.md` — the subagent definition you must
  delegate each batch to. Do not inline the subagent prompt; use the subagent.
- `verdicts_3_5.jsonl` — append-only output, written by
  `batch_helper_3_5.py validate` later. Do not write to it directly.

## Your loop

For each `pending_3_5/batch_NNNN.json` file that does NOT have a sibling
`pending_3_5/batch_NNNN.verdict.json` next to it:

1. Read the batch JSON.
2. Dispatch the entire batch to the `ionising-promote` subagent in a single
   subagent call. Pass the batch content as the subagent's input. The
   subagent will return one JSON object per input record, in the same order
   as the inputs, wrapped in a fenced ```json [ ... ] ``` block.
3. Extract the JSON array from the subagent response. Verify it has the same
   length as the input batch.
4. Write the extracted array to `pending_3_5/batch_NNNN.verdict.json` as
   strict JSON, no fences, UTF-8.
5. Move on to the next pending batch.

## Parallelism

Dispatch up to 10 subagent calls in parallel per iteration of the loop. This
mirrors the Stage-3 throughput. Each subagent call handles one batch of up to
5 records. Wait for the iteration to complete before starting the next.

## What you must NOT do

- Do not invoke any LLM other than via the `ionising-promote` subagent. No
  inline classification, no shortcut for records you "already know".
- Do not modify, append to, or read `verdicts_3_5.jsonl`,
  `quarantine_3_5.jsonl`, or `audit_3_5.log`. The Python tooling owns those
  files.
- Do not move batch files into `processed_3_5/`. The Python validator does
  that after schema and quote checks.
- Do not paraphrase the subagent's quotes. Pass them through verbatim.
- Do not skip a record because its abstract is missing. The subagent has
  explicit instructions for title-only handling.
- Do not retry on subagent content-policy refusals more than once. If a
  retry also fails, write an empty placeholder verdict for that record with
  `_subagent_refused: true` so the validator will quarantine it and the
  human reviewer will see it.

## Resumability

If you are interrupted, simply restart this loop. The presence of a
`.verdict.json` sibling next to a batch file means that batch has been
processed; skip it. The user runs `python batch_helper_3_5.py validate`
periodically to ingest completed batches into `verdicts_3_5.jsonl`.

## Completion criterion

You are done when every `pending_3_5/batch_NNNN.json` has a sibling
`pending_3_5/batch_NNNN.verdict.json`. At that point, stop and report:

- Total batches processed in this run.
- Total records covered.
- Any record_ids for which the subagent refused or returned a malformed
  payload (these will be quarantined by the validator).

The user will then run `python batch_helper_3_5.py validate` to ingest, and
`python report_3_5.py` to summarise.

## Reminders

- Pro Max subscription, Claude Code interactive session, Anthropic API not
  permitted.
- Single subagent definition: `ionising-promote`. Do not invoke
  `screen-papers` or any Stage-3 subagent here.
- Quote-policy is B1 strict (subagent enforces this; validator double-checks).
