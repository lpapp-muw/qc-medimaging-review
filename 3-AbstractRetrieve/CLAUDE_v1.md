# Systematic-review screening: orchestrator protocol

## Project context

You are the **orchestrator** for a PRISMA systematic-review screening task: "Quantum computing for medical imaging applications" (IEEE TRPMS). Corpus in `merged_dataset.json` (~2974 records).

Each individual paper is classified by a **subagent** (defined in `.claude/agents/screen-papers.md`), which runs in its own isolated context window. Your job is **not to classify papers yourself**. Your job is to drive a loop that spawns subagents, collects their output, and tracks progress.

## When the user says "process the entire corpus" (or similar)

Run the loop below until `python batch_helper.py status` reports `Remaining: 0`. Do not pause for confirmation between iterations. Do not classify any record yourself.

### Loop iteration

1. `python batch_helper.py status` → read remaining count.
2. If remaining == 0: stop, report final summary.
3. `python batch_helper.py next-batches --total 50 --per-agent 5` → creates 10 batch files in `pending/` and prints the 10 batch IDs.
4. **Spawn 10 subagents in parallel** using the Task tool. Each invokes the `screen-papers` subagent with prompt:
   `"Process batch <BATCH_ID>. Read pending/batch_<BATCH_ID>.json, classify each record per your protocol, write verdicts to pending/verdicts_<BATCH_ID>.jsonl, then report 'Done <BATCH_ID>'."`
5. Wait for all 10 to return.
6. `python batch_helper.py merge-pending` → consolidates pending verdict files into `verdicts.jsonl`, runs validation, moves failures to `quarantine.jsonl`, cleans up `pending/`.
7. `python batch_helper.py status` → confirm progress.
8. Briefly report iteration summary (one line: `Iteration N: processed M, total done X/Y, quarantine Z`). Do not dump verdicts to the conversation.
9. Goto 1.

### Stop conditions

- `Remaining: 0` → normal completion.
- Quarantine count grows by more than 10 in a single iteration → pause, report which batch had issues, ask the user before continuing. (Indicates subagent prompt drift.)
- Any tool error that recurs twice → pause and report.
- User says "stop" or "pause" → finish current iteration, then stop.

### Resumability

If the session is restarted (Pro Max usage cap, /clear, new terminal), the user types "continue processing" or "process the entire corpus" again. You re-read this CLAUDE.md, re-run `python batch_helper.py status`, and resume the loop. Records already in `verdicts.jsonl` are skipped automatically by `next-batches`.

## When the user says "process N records"

Same loop, but instead of running until empty, run for exactly one iteration with `--total N --per-agent <appropriate>` (default --per-agent 5), then stop and report. Used for testing.

## When the user asks for a summary

Run `python batch_helper.py summary` and report the output. Do not add interpretation unless asked.

## Constraints on you

- **Do not classify records yourself.** Always delegate to the `screen-papers` subagent. Your context must stay clean for many iterations.
- **Do not read `verdicts.jsonl` or `pending/*` files into your context.** Only run scripts on them. The scripts handle parsing, validation, and merging.
- **Do not summarize subagent output verbosely.** A subagent's only useful return value to you is "Done <BATCH_ID>" or an error message. Anything more bloats your context.
- If a subagent returns an error or unexpected output, log the batch ID and continue with other batches. The validator will quarantine bad records during merge-pending.

## File map

- `merged_dataset.json` — input corpus (do not modify).
- `verdicts.jsonl` — append-only validated verdicts.
- `quarantine.jsonl` — verdicts that failed validation.
- `pending/` — transient batch files during an iteration; cleaned up by merge-pending.
- `batch_helper.py` — utility script.
- `.claude/agents/screen-papers.md` — subagent definition (do not modify).
