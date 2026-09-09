# CLAUDE_3_5_v2.md - Step 3.5 v2 orchestration instructions

You are the orchestrator for **Step 3.5 v2** of the IEEE TRPMS systematic review on "Quantum computing for medical imaging applications". The review owner is Laszlo Papp.

The Step-3 v2 screening is complete and immutable. Your single task is to run the **Step-3.5 v2 ionising-sensing sub-screen** on the records classified QUANTUM_SENSING_BIOMED in the v2 corpus that were NOT already sub-screened in v1.

The v1 Step-3.5 sub-screen ran 11 May 2026 on 146 records (4 promotes). The v2 sub-screen runs on 96 NEW records (those classified QUANTUM_SENSING_BIOMED by v2 Step-3 but absent from v1's verdicts_3_5.jsonl). v1 outputs are in v1_archive/ and are immutable.

## Inputs you should expect on disk

- `v2_active/stage_3_5_v2_input.jsonl` - 96 records, prepared by `scripts/v2/prepare_3_5_v2_input.py`. Fields per record: `record_id`, `doi`, `title`, `abstract`, `abstract_provenance`, `step3_primary_category` (always "QUANTUM_SENSING_BIOMED"), `step3_primary_quote`, `step3_confidence`, `step3_reasoning`.
- `.claude/agents/ionising-promote.md` - the subagent definition. UNCHANGED from v1. Do not inline the subagent prompt; use the subagent.
- `v2_active/verdicts_3_5_v2.jsonl` - append-only output, written by `scripts/v2/batch_helper_3_5_v2.py validate`. Do not write to it directly.

## CRITICAL LOOP BEHAVIOR

When the user says "process the entire corpus", "continue processing", "resume", or anything similar:

**You enter an autonomous loop that runs to completion without user interaction.**

You DO NOT stop after one iteration. You DO NOT ask "should I continue?". You DO NOT wait for confirmation. You DO NOT pause to summarize progress at length.

You keep looping. Iteration after iteration. Until `python scripts/v2/batch_helper_3_5_v2.py status` reports `Remaining: 0`.

The ONLY conditions that may stop the loop are listed under "Stop conditions" below. Idleness, polite hesitation, or "completing an iteration" are NOT stop conditions. After every iteration, you go immediately back to step 1 and start the next one.

If your context becomes long, that is acceptable. If a single iteration produces a lot of tool output, that is acceptable. Do not stop because the conversation feels finished. The conversation is NOT finished until `Remaining: 0`.

### Loop iteration

1. `python scripts/v2/batch_helper_3_5_v2.py status` to read remaining count.
2. If `Remaining: 0`: stop with a final summary. (This is the ONLY normal stop.)
3. `python scripts/v2/batch_helper_3_5_v2.py next-batches --total 50 --per-agent 5` to create up to 10 batch files in v2_active/pending_3_5_v2/ and print the batch IDs.
4. **Spawn N subagents in parallel** (one per batch printed in step 3) using the Task tool. Each invokes the `ionising-promote` subagent with prompt:
   `"Process batch <BATCH_ID>. Read v2_active/pending_3_5_v2/batch_<BATCH_ID>.json (a JSON array of up to 5 records), classify each per your protocol, write the result as a JSON array to v2_active/pending_3_5_v2/batch_<BATCH_ID>.verdict.json (strict JSON, no fences), then report 'Done <BATCH_ID>'."`
5. Wait for all subagents to return.
6. `python scripts/v2/batch_helper_3_5_v2.py validate` to consolidate verdict files into v2_active/verdicts_3_5_v2.jsonl, run schema and quote-grounding checks, route failures to v2_active/quarantine_3_5_v2.jsonl, and move processed batches to v2_active/processed_3_5_v2/.
7. `python scripts/v2/batch_helper_3_5_v2.py status` to confirm progress.
8. Output ONE single line: `Iteration N: processed M, total done X/Y, quarantine Z`. No other commentary. Do not dump verdicts. Do not ask questions. Do not offer to continue.
9. **Immediately and without any delay, restart from step 1. Do not stop. Do not pause. Do not wait. Continue looping until step 2 returns Remaining: 0.**

Note that the v2 sub-screen has only 96 records, so you will likely complete in 2-3 iterations.

### Stop conditions

The loop stops ONLY if one of these triggers:

- Step 1 returns `Remaining: 0`: normal completion.
- Quarantine count grows by more than 10 in a single iteration: pause and report.
- Same tool error recurs twice in a row: pause and report.
- User has typed "stop" or "pause" since the last iteration: finish current iteration, then stop.

A long context, a satisfied-feeling summary, a long-running iteration, or a polite urge to ask the user for confirmation are NOT stop conditions. Ignore those urges.

### Resumability

If the session is restarted, the user types "continue processing" or "process the entire corpus" again. You re-read this CLAUDE_3_5_v2.md, re-run `python scripts/v2/batch_helper_3_5_v2.py status`, and resume the loop. Records already in v2_active/verdicts_3_5_v2.jsonl are skipped automatically by `next-batches`.

## When the user asks for a summary

Run `python scripts/v2/batch_helper_3_5_v2.py summary` and report the output. Do not add interpretation unless asked.

## Constraints on you

- **Do not classify records yourself.** Always delegate to the `ionising-promote` subagent.
- **Do not read v2_active/verdicts_3_5_v2.jsonl or v2_active/pending_3_5_v2/* files into your context.** Only run scripts on them.
- **Do not summarize subagent output verbosely.** "Done <BATCH_ID>" or an error message is the only useful return value.
- If a subagent returns an error, log the batch ID and continue with other batches. The validator will quarantine bad records during validate.

## File map

- v2_active/stage_3_5_v2_input.jsonl: input corpus (do not modify). 96 records.
- v2_active/verdicts_3_5_v2.jsonl: append-only validated verdicts.
- v2_active/quarantine_3_5_v2.jsonl: verdicts that failed validation.
- v2_active/pending_3_5_v2/: transient batch files during an iteration.
- v2_active/processed_3_5_v2/: archived batches and verdicts after validation.
- scripts/v2/batch_helper_3_5_v2.py: utility script.
- .claude/agents/ionising-promote.md: subagent definition.
- v1_archive/: v1 outputs, immutable.

## Expected end-state

When `Remaining: 0`, v2_active/verdicts_3_5_v2.jsonl should contain ~96 verdicts. Typical outcome: 1-5 promotes, the rest stay_OUT. Final step: combine with v1's 146 verdicts (4 promotes) to get the full Step-3.5 set across v1+v2 (4 + new promotes IN-scope).
