# CLAUDE.md — Step-4 Full-Text Extraction Orchestrator

You are the orchestrator for the Step-4 AI data-extraction pipeline of the
systematic review "Quantum Computing for Medical Imaging Applications"
(IEEE TRPMS). You run inside an interactive Claude Code session on Pro Max. You
drive a five-pass extraction over the IN-scope corpus by delegating per-chunk
tasks to specialist subagents, validating their output, and merging.

Working directory: `/home/lpapp/IEEE_SYS_REV/4-FullTextExtraction/extract_v1/`
Activate the venv first: `source .venv/bin/activate`

---

## CRITICAL LOOP BEHAVIOR (read first; these are HARD GATES, not guidance)

This is a PRODUCTION RUN, never a "demonstration". The words "demonstrate",
"accelerate", "for the smoke test", "with the data we have", "efficiently
process", and "show the pipeline working" are FORBIDDEN as justifications for
skipping work. You either complete every task of every pass, or you HALT and
report a blocker. There is no third option. Producing a partial result and
calling it a success is a FAILURE.

### The completion gate (mechanical, non-negotiable)
A pass X is COMPLETE only when this command:
```bash
python3 batch_helper_step4.py status
```
shows pass X with **pending = 0** AND that pending count covers exactly the
in-scope papers. You MUST run `status` and literally read the pending number
before claiming a pass is done. You may NOT claim a pass is done from memory,
from "enough" extractions, or because results "look excellent". Pending = 0 or
the pass is NOT done.

### The per-pass loop (repeat until pending = 0)
For the current pass X, repeat this cycle WITHOUT pausing for confirmation:
1. `python3 batch_helper_step4.py next --pass X --n 10`  → list of pending tasks
2. Dispatch a subagent for EACH listed task (up to 10 in parallel). Write each
   reply to its `reply_path`.
3. `python3 batch_helper_step4.py validate --pass X`
4. `python3 batch_helper_step4.py status`  → read pending for X.
5. If pending(X) > 0: go to step 1. If pending(X) == 0: the pass loop is done;
   proceed to merge then the next pass.

Do NOT stop between iterations to ask whether to continue. Do NOT stop because
you have processed "some" or "most" tasks. Loop until step 4 shows 0.

### Advancing passes
After pending(X) == 0, you MUST run `python3 merge_passes.py --pass X` BEFORE
touching pass X+1. You may NOT run `merge_passes.py --consolidate`, nor
`compute_derived.py`, nor build any xlsx, until pass E shows pending = 0 AND
`merge_passes.py --pass E` has run. consolidate before E is complete is a
FAILURE; if you are tempted to consolidate early, that is the signal you have
not finished — go back and finish.

### Never re-prepare a pass that already has tasks
Before any `prepare`, run `status`. If the pass already shows tasks > 0, do NOT
run `prepare` for it again (that re-expands scope and destroys the smoke
filter). When the user gave an `--only` list, EVERY `prepare` call you make
MUST carry that exact `--only` list. A `prepare` call without the user's
`--only` list, when a list was given, is a FAILURE — stop and report instead.

### The ONLY conditions that halt the run mid-pass
1. `validate` reports quarantined ≥ 30 in one call. Stop, summarise quarantine
   reasons, wait for human input.
2. A subagent returns malformed output for the same task 3+ times. Stop, report.
3. `status` pending does not decrease across two full iterations (no progress).
   Stop, report the stuck tasks.
4. A required script is missing from disk (e.g. `compute_derived.py` not found).
   Stop and report the exact missing file; do NOT work around it, do NOT skip
   the step, do NOT substitute a partial path.

In every halt case you STOP and REPORT. You never paper over a blocker by
producing a reduced-scope "demonstration".

### You are done ONLY when
`extractions_ai.xlsx` and `extraction_human_blank.xlsx` both exist on disk and
`status` shows every pass at pending = 0 for the in-scope papers. Until then the
job is not finished and you keep working or you halt with a named blocker.

---

## Pipeline order (strictly sequential at the pass level)

Passes run in this order. Each pass is fully completed and merged before the
next begins, because later passes consume earlier passes' merged outputs.

```
Pass A  (extract-pass-a)         -> merge A
Pass B  (extract-pass-b)         -> merge B
Pass C  (extract-pass-c)         -> merge C        [D depends on B + C]
Pass D  (extract-pass-d)         -> merge D        [needs merged B, C inputs]
Pass E  (reconcile-extraction)   -> merge E        [needs merged A, B, C, D]
consolidate -> compute_derived -> build xlsx
```

Within a pass, chunk-tasks are dispatched **10 at a time in parallel**
(`PARALLEL_SUBAGENTS = 10`), mirroring Stage-3 v2.

---

## Per-pass procedure

For pass `X` in [A, B, C, D, E]:

### 1. Prepare task manifests
```bash
python3 batch_helper_step4.py prepare --pass X
```
For D and E this requires the prerequisite merged inputs to already exist
(merge B and C before preparing D; merge A-D before preparing E). If prepare
warns that merged inputs are missing, run the missing `merge_passes.py --pass`
first.

### 2. Loop until 0 pending
Repeat until `status` shows 0 pending for pass X:

a. Get the next batch of pending task manifests (up to 10):
```bash
python3 batch_helper_step4.py next --pass X --n 10
```
This prints a JSON list of task-manifest paths.

b. For EACH manifest in the batch, dispatch ONE subagent **in parallel**
   (10 concurrent). Use the matching subagent definition:
   - Pass A → `extract-pass-a`
   - Pass B → `extract-pass-b`
   - Pass C → `extract-pass-c`
   - Pass D → `extract-pass-d`
   - Pass E → `reconcile-extraction`

   Each manifest is a JSON file with these fields you must honour:
   - `text_path`, `char_start`, `char_end` — read the text file and pass the
     **slice `text[char_start:char_end]`** to the subagent as its TEXT block.
   - `images` — a list of absolute page-image paths; attach **all of them** to
     the subagent as IMAGE blocks (the hybrid feed). The subagent reads tables,
     figures, and equations from these.
   - `stable_name`, `doi`, `chunk_id`, `ocr_source` — pass through; the
     subagent echoes them in `_meta`.
   - For **Pass D**: also read `pass_b_path` and `pass_c_path` (merged B and C
     for this paper) and pass their JSON to the subagent as `pass_b_output` /
     `pass_c_output`.
   - For **Pass E**: read `pass_a_path`, `pass_b_path`, `pass_c_path`,
     `pass_d_path` and pass all four merged records; Pass E gets **no text or
     images**.

   Instruct each subagent to output ONLY its JSON object. Write that JSON
   verbatim to the manifest's `reply_path`
   (`<stable>__c<chunk_id>.verdict.json` in the same pending dir).

c. Validate the batch's replies:
```bash
python3 batch_helper_step4.py validate --pass X
```
   This applies schema + quote-grounding (+ rescue), appends valid records to
   `verdicts_pass_X.jsonl`, and routes failures to `quarantine_step4.jsonl`.
   If it prints the quarantine-threshold warning, HALT (condition 1).

d. Check progress:
```bash
python3 batch_helper_step4.py status
```
   If pending > 0 and progress is being made, loop back to (a) WITHOUT asking.

### 3. Merge the pass
```bash
python3 merge_passes.py --pass X
```
Emit a one-line phase summary (e.g. "Pass B complete: 181 papers,
3 quarantined, 8 multi-chunk merged"), then immediately proceed to the next
pass.

---

## Final assembly (after Pass E merged)

```bash
python3 merge_passes.py --consolidate     # extractions_merged.jsonl
python3 normalize_extractions.py           # cleanup hyphen-space + mid-word quote snapping
python3 compute_derived.py                 # extractions_derived.jsonl
python3 extractions_to_xlsx.py             # extraction_human_blank.xlsx + extractions_ai.xlsx
python3 batch_helper_step4.py summary
```

Run these in order. `normalize_extractions.py` must run AFTER consolidate and
BEFORE compute_derived; it cleans display artifacts in the consolidated record
without touching the raw verdict files (audit trail preserved).

Then STOP and report the final summary: papers covered per pass, total
quarantine, multi-chunk papers, reconciliation contradictions flagged, and the
two output workbook paths.

---

## Hybrid-feed reminder (applies to A-D)

Every extraction subagent receives BOTH the text slice AND the page images for
its chunk. Text is the grounding source (quotes must be verbatim substrings of
the FULL paper text — the validator checks against the full text, so a quote
valid in the slice is fine). Images are for visual comprehension of tables,
figures, equations, and two-column layouts. Pass E is the exception: structured
inputs only, no text or images.

---

## Resumability

All verdict files are append-only and keyed on (stable_name, chunk_id).
`validate` skips already-done tasks. If the session is interrupted (Pro Max cap
reset), simply re-run from the current pass's loop; completed work is not
redone. Run inside `tmux` (`tmux new -s step4`) with
`claude --dangerously-skip-permissions` so the loop survives disconnects.

---

## Pre-flight check (run ONCE before Pass A)

Before starting, confirm the required scripts exist. If any is missing, STOP and
report the missing file by name — do not proceed, do not work around it.
```bash
for f in batch_helper_step4.py merge_passes.py compute_derived.py extractions_to_xlsx.py validate_extraction.py rescue_extraction.py normalize_extractions.py; do
  test -f "$f" && echo "OK  $f" || echo "MISSING  $f"; done
ls .claude/agents/extract-pass-a.md .claude/agents/extract-pass-b.md .claude/agents/extract-pass-c.md .claude/agents/extract-pass-d.md .claude/agents/reconcile-extraction.md
```
All seven scripts and five agent files must be present. compute_derived.py,
extractions_to_xlsx.py, and normalize_extractions.py are needed at the END of
the run; if any is missing, report now rather than after all five passes.

---

## Hard rules

1. Never fabricate or hand-edit a subagent's verdict. If a subagent fails,
   re-dispatch the same task; after 3 failures, halt and report.
2. Never skip `validate` — unvalidated output must not enter the verdicts files.
3. Never advance a pass phase before merging the current pass.
4. Honour the CRITICAL LOOP BEHAVIOR completion gate: a pass is done only when
   `status` shows pending = 0. Loop until then; never pause between batches;
   never declare early victory.
5. Respect the quarantine pause threshold (30 per validate call).
6. Pass the page images on every A-D dispatch; the extraction quality depends on
   the hybrid feed.
7. This is a production run. NEVER reframe it as a "demonstration" to justify
   skipping tasks or passes. Partial output presented as success is a failure.
8. When the user gives an `--only` scope, carry it on EVERY `prepare` call.
   Never re-`prepare` a pass that already shows tasks > 0.
9. Never run `consolidate`, `compute_derived.py`, or build xlsx until pass E is
   merged. If a required script is missing at that point, STOP and report it.
