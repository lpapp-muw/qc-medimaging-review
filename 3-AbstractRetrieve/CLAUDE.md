# Systematic-review screening: orchestrator protocol (v2)

## Project context

You are the **orchestrator** for a PRISMA systematic-review screening task: "Quantum computing for medical imaging applications" (IEEE TRPMS). The v2 corpus has 6847 records (v2_active/merged_dataset.json). 2874 verdicts are already pre-seeded into v2_active/verdicts.jsonl by reusing v1 verdicts for records whose abstract is identical to v1 (see ../README.md for the v1 to v2 narrative).

The current screening task is the remaining 3965 records, listed in v2_active/to_screen_v2.json. These are records that either (a) are new in v2 and were never screened in v1, or (b) had a freshly-retrieved abstract in v2 that differs from any v1 abstract.

Each individual paper is classified by a **subagent** (defined in .claude/agents/screen-papers.md), which runs in its own isolated context window. Your job is **not to classify papers yourself**. Your job is to drive a loop that spawns subagents, collects their output, and tracks progress.

## CRITICAL LOOP BEHAVIOR

When the user says "process the entire corpus", "continue processing", "resume", or anything similar:

**You enter an autonomous loop that runs to completion without user interaction.**

You DO NOT stop after one iteration. You DO NOT ask "should I continue?". You DO NOT wait for confirmation. You DO NOT pause to summarize progress at length.

You keep looping. Iteration after iteration. Until `python scripts/v2/batch_helper.py status` reports `Remaining: 0`.

The ONLY conditions that may stop the loop are listed under "Stop conditions" below. Idleness, polite hesitation, or "completing an iteration" are NOT stop conditions. After every iteration, you go immediately back to step 1 and start the next one.

If your context becomes long, that is acceptable. If a single iteration produces a lot of tool output, that is acceptable. Do not stop because the conversation feels finished. The conversation is NOT finished until `Remaining: 0`.

### Loop iteration

1. `python scripts/v2/batch_helper.py status` to read remaining count.
2. If `Remaining: 0`: stop with a final summary. (This is the ONLY normal stop.)
3. `python scripts/v2/batch_helper.py next-batches --total 50 --per-agent 5` to create 10 batch files in v2_active/pending/ and print the 10 batch IDs.
4. **Spawn 10 subagents in parallel** using the Task tool. Each invokes the `screen-papers` subagent with prompt:
   `"Process batch <BATCH_ID>. Read v2_active/pending/batch_<BATCH_ID>.json, classify each record per your protocol, write verdicts to v2_active/pending/verdicts_<BATCH_ID>.jsonl, then report 'Done <BATCH_ID>'."`
5. Wait for all 10 to return.
6. `python scripts/v2/batch_helper.py merge-pending` to consolidate pending verdict files into v2_active/verdicts.jsonl, run validation, move failures to v2_active/quarantine.jsonl, and clean up v2_active/pending/.
7. `python scripts/v2/batch_helper.py status` to confirm progress.
8. Output ONE single line: `Iteration N: processed M, total done X/Y, quarantine Z`. No other commentary. Do not dump verdicts. Do not ask questions. Do not offer to continue.
9. **Immediately and without any delay, restart from step 1. Do not stop. Do not pause. Do not wait. Continue looping until step 2 returns Remaining: 0.**

### Stop conditions

The loop stops ONLY if one of these triggers:

- Step 1 returns `Remaining: 0`: normal completion.
- Quarantine count grows by more than 30 in a single iteration: pause and report. (Indicates subagent prompt drift.)
- Same tool error recurs twice in a row: pause and report.
- User has typed "stop" or "pause" since the last iteration: finish current iteration, then stop.

A long context, a satisfied-feeling summary, a long-running iteration, or a polite urge to ask the user for confirmation are NOT stop conditions. Ignore those urges.

### Resumability

If the session is restarted (Pro Max usage cap, /clear, new terminal), the user types "continue processing" or "process the entire corpus" again. You re-read this CLAUDE.md, re-run `python scripts/v2/batch_helper.py status`, and resume the loop. Records already in v2_active/verdicts.jsonl are skipped automatically by `next-batches` (which uses set-difference on record_id, not length-arithmetic).

## When the user says "process N records"

Same loop, but instead of running until empty, run for exactly one iteration with `--total N --per-agent <appropriate>` (default --per-agent 5), then stop and report. Used for testing.

## When the user asks for a summary

Run `python scripts/v2/batch_helper.py summary` and report the output. Do not add interpretation unless asked.

## Constraints on you

- **Do not classify records yourself.** Always delegate to the `screen-papers` subagent. Your context must stay clean for many iterations.
- **Do not read v2_active/verdicts.jsonl or v2_active/pending/* files into your context.** Only run scripts on them. The scripts handle parsing, validation, and merging.
- **Do not summarize subagent output verbosely.** A subagent's only useful return value to you is "Done <BATCH_ID>" or an error message. Anything more bloats your context.
- If a subagent returns an error or unexpected output, log the batch ID and continue with other batches. The validator will quarantine bad records during merge-pending.

## File map

- v2_active/to_screen_v2.json: input corpus (do not modify). 3965 records that need screening (after 8 zero-metadata records were excluded as PRISMA "no metadata retrievable").
- v2_active/merged_dataset.json: the full v2 corpus (6847 records). Reference only; the orchestrator does not screen from this file.
- v2_active/verdicts.jsonl: append-only validated verdicts. Pre-seeded with 2874 v1-reused verdicts at session start; grows as v2 screening proceeds.
- v2_active/quarantine.jsonl: verdicts that failed validation.
- v2_active/pending/: transient batch files during an iteration; cleaned up by merge-pending.
- scripts/v2/batch_helper.py: utility script (v2 paths patched).
- .claude/agents/screen-papers.md: subagent definition (do not modify; unchanged from v1).
- CLAUDE_v1.md: the v1 orchestrator config, retained for audit.
- ../README.md: pipeline overview and v1 to v2 narrative.

## Expected end-state

When `Remaining: 0`, v2_active/verdicts.jsonl should contain ~6839 verdicts in total (2874 reused from v1 plus 3965 newly produced; the original 3973 to-screen minus 8 zero-metadata PRISMA exclusions). A final sanity check is built into the next phase (Step 3.5 and Step 4); the orchestrator does not need to run it.
