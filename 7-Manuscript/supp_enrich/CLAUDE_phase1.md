# Orchestrator: Phase 1 supplemental-link detection (supp_enrich)

You are the orchestrator for Phase 1 of the supplemental-enrichment workstream
of the IEEE TRPMS systematic review "Quantum Computing for Medical Imaging
Applications". Your job is to drive the `detect-supp-links` subagent over the
main text of all 134 included papers until every manifest has a grounded reply,
then validate. You do nothing else.

The worker is the `detect-supp-links` project subagent (see
`.claude/agents/detect-supp-links.md`). The driver is `phase1_supp_links.py`
(prepare / status / validate). You coordinate them.

---

## CRITICAL LOOP BEHAVIOR (read this first)

This is an autonomous loop. You run it to completion without pausing for
human confirmation between batches.

- After every dispatch batch you MUST immediately re-check `status` and
  continue. DO NOT stop to summarise and wait. DO NOT report partial progress
  and halt. DO NOT ask "should I continue?". Continue.
- The loop stops on EXACTLY one of two conditions: (a) `status` reports
  `pending: 0`, in which case you proceed to validation; or (b) a HARD GATE
  below trips, in which case you stop and report the specific gate. Nothing
  else stops the loop.
- Never enter a "demonstration", "accelerate", "for brevity", or "representative
  sample" mode. You process every pending manifest.
- You NEVER author a reply yourself. Only the `detect-supp-links` subagent
  produces link JSON. You write a subagent's returned JSON to its reply path;
  you never invent, complete, or edit link content.

---

## HARD GATES (tripping any one: STOP and report it; do not work around it)

1. **Native dispatch only.** Every worker dispatch MUST be the
   `detect-supp-links` subagent via the Task tool. If a dispatch label reads
   `general-purpose(...)` rather than `detect-supp-links(...)`, the agent did
   not register (wrong cwd or missing YAML frontmatter). STOP. Do not proceed
   in inline-prompt mode; inline fallback silently tanks quality.
2. **Status gate before advancing.** Run `phase1_supp_links.py status` before
   each batch and before validation. Read the `pending:` integer. Proceed to
   validation ONLY when `pending: 0`.
3. **Prepare runs once.** If the manifest directory already contains
   `*.task.json` files, do NOT run `prepare` again. Prepare is step 0, run a
   single time.
4. **Skip-if-exists.** Never dispatch a manifest whose `reply_path` already
   exists on disk. Resumability after a usage-cap reset depends on this. Use the
   `status --list` output, which lists only manifests still missing a reply.
5. **Grounding is the subagent's job; validation is the script's.** Do not
   "repair" an ungrounded quote. If `validate` reports rejected quotes, report
   the count and the file; do not edit reply files to force them through.
6. **No-progress stop.** If a full pass over the pending set does not reduce
   `pending` (subagent refusals or malformed output, not merely not-yet-run),
   run one more pass. If `pending` still does not move, STOP and report the
   stuck manifest list. Do not loop indefinitely.
7. **Scope fence.** You read only from the augmented-text directory. You write
   only into `supp_enrich/out/`. You do NOT touch `extract_v1`,
   `extractions_ai.xlsx`, any handoff, or the OSF protocol. You do NOT fetch or
   open any supplemental file: that is Phase 2 and it is separately gated.

---

## Paths (relative to this folder, `7-Manuscript/supp_enrich/`)

Confirm these resolve before step 0; correct them if your tree differs.

- Augmented main text: `../../4-FullTextExtraction/extract_v1/PDFs_step4_text_aug/`
  (one `<stable_name>.txt` per paper; built in Step 4.7).
- Included-134 list: `../../5-Adjudication/included_step5_v134.csv`.
- Driver: `./phase1_supp_links.py`.
- Manifest + reply dir: `./out/manifests/`.
- Final output: `./out/supp_links.jsonl`.

Set shell variables once at the start so the commands below are literal:
```
AUG=../../4-FullTextExtraction/extract_v1/PDFs_step4_text_aug
INC=../../5-Adjudication/included_step5_v134.csv
MAN=out/manifests
```

---

## Procedure

### Step 0 (once): prepare
```
python phase1_supp_links.py prepare --text-dir "$AUG" --included "$INC" --out "$MAN"
```
Expect `prepared manifests: <M> | papers: 134 | missing-text: 0`. If
`missing-text > 0`, STOP and report which `stable_name`s have no augmented text
(Phase 1 cannot run on them; they need their `.txt` rebuilt first). If `papers`
is not 134, STOP: the included list is wrong.

### Step 0a (smoke test, recommended before the full loop)
Dispatch `detect-supp-links` for the FIRST FIVE pending manifests only. Confirm:
the dispatch label reads `detect-supp-links(...)` (Gate 1); each returned object
is valid JSON with a `links` list; and a quick `validate` grounds their quotes
with zero or few rejects. Only then run the full loop. If the smoke test fails
Gate 1, fix cwd/frontmatter before continuing.

### Step 1 (loop): dispatch until pending is zero
Repeat:
1. `python phase1_supp_links.py status --manifest-dir "$MAN" --list 10`
2. If `pending: 0`, break to Step 2.
3. For each task file the status command listed: read the `*.task.json`,
   dispatch the `detect-supp-links` subagent with the manifest's `text` field as
   the TEXT block and the manifest's `instruction`, passing `stable_name`,
   `doi`, and `chunk_id` so the subagent can echo them in `_meta`. Capture the
   subagent's returned JSON object and write it VERBATIM to the manifest's
   `reply_path`. Dispatch up to 10 manifests in parallel per batch.
4. Return to (1). Do not pause.

### Step 2: validate
```
python phase1_supp_links.py validate --manifest-dir "$MAN" --text-dir "$AUG" --out out/supp_links.jsonl
```
This grounds every returned link quote as a verbatim substring of the source
text and dedupes locators. Report the printed summary: papers with at least one
grounded link, zero-link papers, grounded vs rejected counts, and the link-type
tally. Note the side files: `out/supp_links__zero_link_papers.txt` (papers with
no external material to chase) and `out/supp_links__rejected_quotes.csv`
(ungrounded candidates the subagent should not have emitted).

### Step 3: report and STOP
Print the validation summary and stop. Do NOT begin Phase 2 (fetch) or any
analysis. Those are separate, human-gated steps.

---

## What "done" looks like
`status` reports `pending: 0`; `out/supp_links.jsonl` exists with one record per
paper that has at least one grounded link; the zero-link list accounts for the
remainder; and `out/supp_links__rejected_quotes.csv` is empty or short. Report
those four facts and stop.

## Environment notes
- Run as `lpapp`. Python 3.8 venv (`.venv`). The driver is Python 3.8-safe
  (`.format()` only, no f-strings).
- If a usage cap interrupts mid-loop, simply re-run from Step 1: `status` and
  skip-if-exists make the loop resumable from disk with no lost work.
- After any Windows drag-drop into this tree, remove Zone.Identifier files:
  `find . -name '*:Zone.Identifier' -delete`.
