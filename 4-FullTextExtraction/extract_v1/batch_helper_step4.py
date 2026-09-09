"""
batch_helper_step4.py — Orchestration spine for the Step-4 extraction pipeline
(Model 1: Claude Code subagent delegation on Pro Max).

This script is the deterministic plumbing AROUND the subagents. It does not call
any LLM. The Claude Code orchestrator (CLAUDE.md) calls these subcommands
between rounds of subagent delegation.

Unit of work = one (paper, pass, chunk) "task". For each task the orchestrator
must invoke the matching subagent with the task's INPUT MANIFEST (text slice +
page image paths), then write the subagent's JSON reply next to the manifest.

Subcommands
-----------
  prepare   Build input manifests for a pass (A/B/C/D) from chunks/*.json.
            Pass D and E manifests additionally reference merged B/C (and A-D)
            outputs; prepare for D/E is only valid after the prerequisite
            verdicts exist.
  status    Report how many tasks are pending / done / quarantined per pass.
  next      Print the next N pending task manifests for a pass (for the
            orchestrator to pick up).
  validate  Ingest subagent replies for a pass, run schema + quote-grounding
            (+ rescue), append valid records to verdicts_pass_X.jsonl, route
            failures to quarantine_step4.jsonl. Idempotent, resumable.
  summary   End-to-end counts across all passes.

Manifest layout (one JSON per task)
-----------------------------------
  pending_step4/<pass>/<stable>__c<chunk_id>.task.json
    {
      "stable_name": "...", "doi": "...", "pass": "B", "chunk_id": 0,
      "text_path": "<abs path to FULL text>",
      "char_start": 0, "char_end": 81234,        # slice bounds within text
      "images": ["<abs path>/page_001.jpg", ...],
      "ocr_source": false,
      # for D/E only:
      "pass_b_path": "...", "pass_c_path": "...",  # merged per-paper inputs
      "reply_path": "pending_step4/<pass>/<stable>__c<chunk_id>.verdict.json"
    }

The orchestrator writes the subagent's JSON object to `reply_path`.

Python 3.8 compatible.
"""

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import paths_step4 as P
import extraction_schema as S
from validate_extraction import (
    validate_record, resolve_source_text, _ocr_lookup,
)
from rescue_extraction import rescue_record


PASSES = ["A", "B", "C", "D"]  # E handled separately (reconciliation)

VERDICT_FILE = {
    "A": P.VERDICTS_PASS_A,
    "B": P.VERDICTS_PASS_B,
    "C": P.VERDICTS_PASS_C,
    "D": P.VERDICTS_PASS_D,
    "E": P.VERDICTS_PASS_E,
}


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def _pass_dir(pass_letter):
    d = P.PENDING_DIR / pass_letter
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_chunk_plans():
    plans = {}
    for f in sorted(P.CHUNKS_DIR.glob("*.json")):
        try:
            plans[f.stem] = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    return plans


def _image_paths(image_dir, image_names):
    base = Path(image_dir)
    return [str(base / n) for n in image_names if n]


def _done_ids_for_pass(pass_letter):
    """Set of (stable, chunk_id) already appended to the verdicts file."""
    done = set()
    vf = VERDICT_FILE[pass_letter]
    if vf.exists():
        with open(vf, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    meta = rec.get("_meta", {})
                    done.add((meta.get("stable_name"), meta.get("chunk_id")))
                except Exception:
                    pass
    return done


# ----------------------------------------------------------------------------
# prepare
# ----------------------------------------------------------------------------
def cmd_prepare(args):
    pass_letter = args.pass_letter
    plans = _load_chunk_plans()
    if not plans:
        print("ERROR: no chunk plans in", P.CHUNKS_DIR, file=sys.stderr)
        return 2

    # Optional restriction to a subset of papers (smoke test / re-run a few).
    only = None
    if getattr(args, "only", None):
        only = set(s.strip() for s in args.only.split(",") if s.strip())
        missing = [s for s in only if s not in plans]
        if missing:
            print("WARNING: --only names not found in chunk plans:", missing,
                  file=sys.stderr)
        plans = {k: v for k, v in plans.items() if k in only}
        if not plans:
            print("ERROR: --only matched no papers; nothing to prepare.",
                  file=sys.stderr)
            return 2

    ocr_set = _ocr_lookup()
    pdir = _pass_dir(pass_letter)
    n_written = 0

    # For D/E, ensure prerequisite merged inputs exist
    merged_b = {}
    merged_c = {}
    if pass_letter in ("D", "E"):
        # merged per-paper B and C are produced by merge_passes.py into
        # extractions_merged-style per-pass maps; here we read the raw verdicts
        # and merge on the fly per paper (lightweight) for the manifest pointer.
        # We simply point D/E at the verdicts files; the orchestrator passes
        # the per-paper merged record. To keep this script decoupled, we write
        # the pointer paths and let merge_passes.py have produced per-paper
        # merged B/C JSONs under processed_step4/merged_inputs/.
        pass

    for stable, plan in plans.items():
        doi = plan.get("doi", "")
        text_path = plan.get("text_path", "")
        image_dir = plan.get("image_dir", "")
        ocr = stable in ocr_set
        pass_plan = plan["plans"][pass_letter] if pass_letter in plan.get("plans", {}) else plan["plans"].get("A", [])
        for chunk in pass_plan:
            cid = chunk["chunk_id"]
            task = {
                "stable_name": stable,
                "doi": doi,
                "pass": pass_letter,
                "chunk_id": cid,
                "text_path": text_path,
                "char_start": chunk["char_start"],
                "char_end": chunk["char_end"],
                "images": _image_paths(image_dir, chunk.get("images", [])),
                "ocr_source": ocr,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
            }
            if pass_letter in ("D", "E"):
                # per-paper merged B/C produced by merge_passes.py
                mi = P.PROCESSED_DIR / "merged_inputs"
                task["pass_b_path"] = str(mi / (stable + ".B.json"))
                task["pass_c_path"] = str(mi / (stable + ".C.json"))
                if pass_letter == "E":
                    task["pass_a_path"] = str(mi / (stable + ".A.json"))
                    task["pass_d_path"] = str(mi / (stable + ".D.json"))
            task_path = pdir / "{}__c{}.task.json".format(stable, cid)
            reply_path = pdir / "{}__c{}.verdict.json".format(stable, cid)
            task["reply_path"] = str(reply_path)
            task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            n_written += 1

    print("Prepared {} task manifest(s) for pass {} in {}".format(
        n_written, pass_letter, pdir))
    return 0


# ----------------------------------------------------------------------------
# status
# ----------------------------------------------------------------------------
def cmd_status(args):
    print("Pass | tasks | replies | validated(done) | pending")
    for pl in PASSES + (["E"] if (P.PENDING_DIR / "E").exists() else []):
        pdir = P.PENDING_DIR / pl
        if not pdir.exists():
            continue
        tasks = list(pdir.glob("*.task.json"))
        replies = list(pdir.glob("*.verdict.json"))
        done = _done_ids_for_pass(pl)
        n_pending = 0
        for t in tasks:
            rec = json.loads(t.read_text(encoding="utf-8"))
            if (rec["stable_name"], rec["chunk_id"]) not in done:
                # pending if no reply yet OR reply not yet validated
                rp = Path(rec["reply_path"])
                if not rp.exists() or (rec["stable_name"], rec["chunk_id"]) not in done:
                    n_pending += 1
        print(" {:<3s} | {:5d} | {:7d} | {:15d} | {:7d}".format(
            pl, len(tasks), len(replies), len(done), n_pending))
    qn = 0
    if P.QUARANTINE_FILE.exists():
        qn = sum(1 for _ in open(P.QUARANTINE_FILE, encoding="utf-8"))
    print("Quarantine records:", qn)
    return 0


# ----------------------------------------------------------------------------
# next
# ----------------------------------------------------------------------------
def cmd_next(args):
    pass_letter = args.pass_letter
    pdir = P.PENDING_DIR / pass_letter
    if not pdir.exists():
        print("ERROR: no prepared tasks for pass", pass_letter, file=sys.stderr)
        return 2
    done = _done_ids_for_pass(pass_letter)
    out = []
    for t in sorted(pdir.glob("*.task.json")):
        rec = json.loads(t.read_text(encoding="utf-8"))
        rp = Path(rec["reply_path"])
        key = (rec["stable_name"], rec["chunk_id"])
        if key in done:
            continue
        if rp.exists():
            continue  # reply present, waiting on validate
        out.append(str(t))
        if len(out) >= args.n:
            break
    # Print as a JSON list so the orchestrator can parse
    print(json.dumps(out, indent=2))
    return 0


# ----------------------------------------------------------------------------
# validate
# ----------------------------------------------------------------------------
def cmd_validate(args):
    pass_letter = args.pass_letter
    pdir = P.PENDING_DIR / pass_letter
    if not pdir.exists():
        print("ERROR: no prepared tasks for pass", pass_letter, file=sys.stderr)
        return 2

    done = _done_ids_for_pass(pass_letter)
    vf = VERDICT_FILE[pass_letter]
    vf.parent.mkdir(parents=True, exist_ok=True)

    n_accepted = 0
    n_rescued = 0
    n_quarantined = 0
    n_skipped = 0

    processed_dir = P.PROCESSED_DIR / pass_letter
    processed_dir.mkdir(parents=True, exist_ok=True)

    with open(vf, "a", encoding="utf-8") as vout, \
         open(P.QUARANTINE_FILE, "a", encoding="utf-8") as qout:
        for t in sorted(pdir.glob("*.task.json")):
            task = json.loads(t.read_text(encoding="utf-8"))
            stable = task["stable_name"]
            cid = task["chunk_id"]
            key = (stable, cid)
            if key in done:
                n_skipped += 1
                continue
            rp = Path(task["reply_path"])
            if not rp.exists():
                continue  # no reply yet
            try:
                rec = json.loads(rp.read_text(encoding="utf-8"))
            except Exception as e:
                # malformed JSON reply -> quarantine with parse flag
                qrec = {"_meta": {"stable_name": stable, "chunk_id": cid,
                                  "pass": pass_letter},
                        "_error": "json_parse_error: {}".format(e),
                        "_raw_path": str(rp)}
                qout.write(json.dumps(qrec) + "\n")
                n_quarantined += 1
                continue

            # ensure _meta present
            rec.setdefault("_meta", {})
            rec["_meta"]["stable_name"] = stable
            rec["_meta"]["chunk_id"] = cid
            rec["_meta"]["pass"] = pass_letter
            rec["_meta"]["doi"] = task.get("doi", "")
            rec["_meta"]["ocr_source"] = task.get("ocr_source", False)

            # Pass E is reconciliation; no quote grounding, just structural.
            if pass_letter == "E":
                if "contradictions" not in rec or not isinstance(rec["contradictions"], list):
                    qrec = dict(rec)
                    qrec["_error"] = "E_missing_contradictions_list"
                    qout.write(json.dumps(qrec) + "\n")
                    n_quarantined += 1
                    continue
                vout.write(json.dumps(rec) + "\n")
                n_accepted += 1
                shutil.move(str(rp), str(processed_dir / rp.name))
                continue

            # Ground quotes against the FULL paper text, not the chunk slice.
            # The subagent only saw its slice, so it cannot quote beyond it; a
            # valid in-slice quote is necessarily also present in the full
            # text. Grounding against the full text avoids false failures when
            # a quote sits at (or spans) the chunk boundary where slice
            # truncation would otherwise cut it.
            source_text = resolve_source_text(stable) or ""
            ocr_mode = bool(task.get("ocr_source", False))

            errs = validate_record(rec, source_text, ocr_mode=ocr_mode,
                                   pass_filter=pass_letter)
            if not errs:
                vout.write(json.dumps(rec) + "\n")
                n_accepted += 1
                shutil.move(str(rp), str(processed_dir / rp.name))
                continue

            # Separate structural errors (enum / type / conditional-required)
            # from quote-grounding errors. Structural errors are NEVER rescued
            # — they quarantine immediately.
            def _is_quote_err(e):
                parts = e if isinstance(e, tuple) else (e,)
                return any("quote_not_in_source" in str(x) for x in parts)

            structural = [e for e in errs if not _is_quote_err(e)]
            if structural:
                qrec = dict(rec)
                qrec["_error"] = [str(e) for e in errs]
                qout.write(json.dumps(qrec) + "\n")
                n_quarantined += 1
                continue

            # Only quote errors remain → one rescue attempt. Accept ONLY if
            # every failing quote was matched at R1/R2/R3 (none left
            # 'unverified'); otherwise quarantine. A genuinely hallucinated
            # quote that matches nothing under any relaxation is quarantined,
            # never accepted-with-flag.
            rescued, report = rescue_record(rec, source_text)
            unrescued = [r for r in report if r.get("level") == "unverified"]
            if not unrescued:
                rescued["_meta"]["_rescued"] = True
                vout.write(json.dumps(rescued) + "\n")
                n_rescued += 1
                shutil.move(str(rp), str(processed_dir / rp.name))
                continue

            # At least one quote unmatchable even under relaxation → quarantine
            qrec = dict(rescued)
            qrec["_error"] = ["unrescued_quote:{}".format(r["field"]) for r in unrescued]
            qout.write(json.dumps(qrec) + "\n")
            n_quarantined += 1

    print("Pass {} validate: accepted={}, rescued={}, quarantined={}, skipped(done)={}".format(
        pass_letter, n_accepted, n_rescued, n_quarantined, n_skipped))
    if n_quarantined >= P.QUARANTINE_PAUSE_THRESHOLD:
        print("WARNING: quarantine count {} >= threshold {}. Investigate before continuing.".format(
            n_quarantined, P.QUARANTINE_PAUSE_THRESHOLD))
    return 0


def _has_unverified_flag(record, err):
    """Check whether the field named in err carries a _quote_unverified flag."""
    field = err[0] if isinstance(err, tuple) else None
    if not field or field not in record:
        return False
    entry = record[field]
    if isinstance(entry, dict):
        return entry.get("_quote_unverified") is True
    if isinstance(entry, list):
        return any(isinstance(it, dict) and it.get("_quote_unverified") for it in entry)
    return False


# ----------------------------------------------------------------------------
# summary
# ----------------------------------------------------------------------------
def cmd_summary(args):
    print("=== Step-4 extraction summary ===")
    plans = _load_chunk_plans()
    n_papers = len(plans)
    print("Papers with chunk plans:", n_papers)
    for pl in PASSES + ["E"]:
        vf = VERDICT_FILE[pl]
        n = sum(1 for _ in open(vf, encoding="utf-8")) if vf.exists() else 0
        # distinct papers covered
        papers = set()
        if vf.exists():
            with open(vf, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        papers.add(json.loads(line).get("_meta", {}).get("stable_name"))
                    except Exception:
                        pass
        print("  Pass {}: {} verdict records, {} distinct papers".format(pl, n, len(papers)))
    qn = sum(1 for _ in open(P.QUARANTINE_FILE, encoding="utf-8")) if P.QUARANTINE_FILE.exists() else 0
    print("  Quarantine:", qn)
    return 0


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prepare")
    sp.add_argument("--pass", dest="pass_letter", required=True, choices=PASSES + ["E"])
    sp.add_argument("--only", type=str, default=None,
                    help="Comma-separated stable_names to restrict preparation to (smoke test).")

    sub.add_parser("status")

    sn = sub.add_parser("next")
    sn.add_argument("--pass", dest="pass_letter", required=True, choices=PASSES + ["E"])
    sn.add_argument("--n", type=int, default=P.BATCH_SIZE)

    sv = sub.add_parser("validate")
    sv.add_argument("--pass", dest="pass_letter", required=True, choices=PASSES + ["E"])

    sub.add_parser("summary")

    args = parser.parse_args()
    P.ensure_dirs()

    if args.cmd == "prepare":
        return cmd_prepare(args)
    if args.cmd == "status":
        return cmd_status(args)
    if args.cmd == "next":
        return cmd_next(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    if args.cmd == "summary":
        return cmd_summary(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
