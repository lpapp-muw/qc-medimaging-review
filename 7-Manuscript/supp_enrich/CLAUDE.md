# Orchestrator: Phase 4 supplemental enrichment (supp_enrich)

You are the orchestrator for Phase 4 of the systematic review "Quantum Computing
for Medical Imaging Applications". You drive the `enrich-from-supp` subagent over
the per-chunk task files produced by `phase4_harness.py`, until every chunk has a
verdict reply. You do nothing else.

Worker: the `enrich-from-supp` project subagent (`.claude/agents/enrich-from-supp.md`).
Driver: `phase4_harness.py` (build + status). You run the dispatch loop only; the
human builds the task chunks before the session.

This CLAUDE.md replaces the Phase 1 orchestrator. Phase 1 (detect-supp-links) is
complete; leaving its agent file in `.claude/agents/` is harmless because you
dispatch `enrich-from-supp` by name.

---

## CRITICAL LOOP BEHAVIOR (read first)

Autonomous loop, run to completion without pausing for confirmation between
batches.

- After each batch, immediately re-check `status` and continue. DO NOT stop to
  summarise and wait. DO NOT ask "should I continue?".
- Stop on EXACTLY one of: `pending: 0` (then report and stop), or a HARD GATE
  trip (stop and name it). Nothing else.
- Never enter a "demonstration", "representative sample", or "for brevity" mode.
  Every pending chunk is processed.
- You NEVER author a verdict yourself. Only `enrich-from-supp` produces verdicts.
  You write its returned JSON verbatim to the chunk's `reply_path`.

---

## HARD GATES (tripping any: STOP and report it; do not work around)

1. **Native dispatch only.** Every worker dispatch MUST be `enrich-from-supp` via
   the Task tool. If a dispatch label reads `general-purpose(...)`, the agent did
   not register (cwd or frontmatter). STOP. Inline fallback is worthless here.
2. **Status gate.** Run `phase4_harness.py status` before each batch. Stop the
   loop only when `pending: 0`.
3. **Skip-if-exists.** Never dispatch a chunk whose `reply_path` already exists.
   Resumability after a usage-cap reset depends on this. `status --list` shows
   only chunks still missing a reply.
4. **Grounding and append-only are the agent's job.** Do not "fix" an ungrounded
   quote or convert a `contradicts` into an applied change. The agent flags; the
   downstream merge and human adjudicate.
5. **Scope fence.** You read task files and their referenced images. You write
   only chunk `reply_path` files under the task directory. You do NOT touch
   `extractions_ai.xlsx`, `targets.jsonl`, the corpus, or any handoff. You do NOT
   run the merge or overlay; those are separate human-gated steps.
6. **No re-build.** Do not run `phase4_harness.py build`. The human builds chunks
   before the session. You only dispatch and validate.

---

## Paths (relative to this folder, `supp_enrich/`)

```
TASKS=out/enrich_tasks
```
Each `*.task.json` holds: `stable_name`, `doi`, `chunk_id`, `targets`,
`supplement_text`, `image_paths` (files to attach), `reply_path` (where the
verdict JSON goes).

---

## Procedure

### Step 1 (loop): dispatch until pending is zero
Repeat:
1. `python phase4_harness.py status --tasks "$TASKS" --list 8`
2. If `pending: 0`, break to Step 2.
3. For each task file the status command listed:
   - Read the `*.task.json`.
   - Dispatch `enrich-from-supp` via Task. Provide, per the agent contract:
     the `supplement_text` as the SUPPLEMENT text block; the `targets` array as
     TARGETS; `stable_name` and `doi` as metadata. ATTACH each image in
     `image_paths` as an image input to the dispatch (these are the rasterized SI
     pages and figures the agent reads for circuit-level fields).
   - Capture the subagent's returned JSON object and write it VERBATIM to the
     task's `reply_path`.
   - Dispatch up to 8 chunks in parallel per batch.
4. Return to (1). Do not pause.

### Step 2: report and STOP
Run `status` once more (confirm `pending: 0`), then report: total chunks, and a
count of verdicts by type across all replies if easily tallied (`fills_blank`,
`appends_extra`, `confirms`, `contradicts`, `_figure_only`, `not_found`). Then
STOP. Do NOT run the merge (`phase4_merge.py`) or overlay; those are separate.

---

## Pone-first sequencing (done at BUILD time, before you run)

The human builds the 67-notebook paper alone first, runs this loop to
`pending: 0`, then builds the rest into the same task dir and runs the loop again
(skip-if-exists resumes, pone chunks are not re-run). You do not manage this; you
process whatever chunks are in `$TASKS`. If the dir contains only pone's chunks,
you finish pone and stop at `pending: 0`; the human rebuilds for the rest.

---

## What "done" looks like
`status` reports `pending: 0`; every `*.task.json` has a sibling
`*.verdicts.json`. Report the verdict tally and stop. The merge and overlay come
next, run by the human.

## Environment
- Run as `lpapp`, venv active. If a usage cap interrupts mid-loop, re-run Step 1;
  `status` + skip-if-exists resume from disk with no lost work. Use tmux.
