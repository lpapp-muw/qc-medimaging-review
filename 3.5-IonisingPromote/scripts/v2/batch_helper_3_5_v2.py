#!/usr/bin/env python3
"""
batch_helper_3_5_v2.py
======================

Utility for the Step-3.5 v2 ionising sub-screen pipeline.

Subcommands:
    prepare     Slice stage_3_5_v2_input.jsonl into batches of N records,
                writing them as pending_3_5_v2/batch_<id>.json files.
    status      Report total/processed/remaining counts.
    validate    Ingest pending_3_5_v2/batch_<id>.verdict.json files produced
                by the orchestrator. For each, run schema and quote-grounding
                validation, then append validated verdicts to verdicts_3_5_v2.jsonl
                and quarantined ones to quarantine_3_5_v2.jsonl. Move processed
                batch+verdict pairs to processed_3_5_v2/.
    next-batches --total N --per-agent K
                Create K-record batches from the remaining input records and
                print the batch IDs. Refuses if pending_3_5_v2/ is non-empty.
    summary     Verdict count by primary verdict + modality.

Schema (from ionising-promote.md):
    record_id, doi,
    ionising_modality_present (bool), ionising_modality_quote, ionising_modality_name,
    quantum_sensing_in_ionising_context (bool), context_quote,
    verdict ('promote_to_IN' | 'stay_OUT'),
    confidence ('high' | 'low'),
    reasoning

Quotes are verified against title + " " + abstract, normalized (whitespace, case).

Author: Laszlo Papp (reconstructed scaffolding for v2)
"""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve()
STEP_DIR = HERE.parent.parent.parent  # scripts/v2/ -> scripts/ -> STEP_DIR
sys.path.insert(0, str(STEP_DIR))
import paths_v2 as P  # noqa: E402

INPUT       = P.INPUT_PATH
VERDICTS    = P.VERDICTS_3_5_PATH
QUARANTINE  = P.QUARANTINE_PATH
PENDING_DIR = P.PENDING_DIR
PROCESSED_DIR = P.PROCESSED_DIR

VALID_VERDICTS = {"promote_to_IN", "stay_OUT"}
VALID_CONFS    = {"high", "low"}

REQUIRED_KEYS = {
    "record_id", "doi",
    "ionising_modality_present", "ionising_modality_quote", "ionising_modality_name",
    "quantum_sensing_in_ionising_context", "context_quote",
    "verdict", "confidence", "reasoning",
}


def load_jsonl(path):
    if not Path(path).exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def load_input():
    if not INPUT.exists():
        sys.exit(f"Missing input: {INPUT}. Run prepare_3_5_v2_input.py first.")
    return load_jsonl(INPUT)


def processed_record_ids():
    return {v.get("record_id") for v in load_jsonl(VERDICTS)
            if isinstance(v, dict) and v.get("record_id")}


def normalize_for_match(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


# --------- prepare ---------

def cmd_prepare(per_agent):
    corpus = load_input()
    done = processed_record_ids()
    remaining = [r for r in corpus if r.get("record_id") and r["record_id"] not in done]
    if not remaining:
        print("Nothing to prepare; all records already processed.")
        return
    if PENDING_DIR.exists():
        existing = list(PENDING_DIR.glob("batch_*.json"))
        if existing:
            sys.exit(f"Refusing: {len(existing)} batches already exist in {PENDING_DIR}/. "
                     "Validate them first.")
    else:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)

    n_batches = 0
    for i in range(0, len(remaining), per_agent):
        chunk = remaining[i:i + per_agent]
        bid = uuid.uuid4().hex[:8]
        out_path = PENDING_DIR / f"batch_{bid}.json"
        out_path.write_text(json.dumps(chunk, ensure_ascii=False, indent=2))
        n_batches += 1
    print(f"Prepared {n_batches} batches in {PENDING_DIR}/ (per_agent={per_agent}, "
          f"total records={len(remaining)})")


def cmd_next_batches(total, per_agent):
    """Like prepare, but limited to `total` records, intended for orchestrator iteration."""
    if PENDING_DIR.exists():
        existing = list(PENDING_DIR.glob("batch_*.json"))
        if existing:
            sys.exit(f"Refusing: {len(existing)} pending batch files exist in {PENDING_DIR}/. "
                     "Run validate first.")
    else:
        PENDING_DIR.mkdir(parents=True, exist_ok=True)

    corpus = load_input()
    done = processed_record_ids()
    remaining = [r for r in corpus if r.get("record_id") and r["record_id"] not in done]
    if not remaining:
        print("No records remaining.")
        return
    take = remaining[:total]

    batch_ids = []
    for i in range(0, len(take), per_agent):
        chunk = take[i:i + per_agent]
        bid = uuid.uuid4().hex[:8]
        out_path = PENDING_DIR / f"batch_{bid}.json"
        out_path.write_text(json.dumps(chunk, ensure_ascii=False, indent=2))
        batch_ids.append(bid)
    for bid in batch_ids:
        print(bid)


# --------- validate ---------

def validate_one(v, batch_records_by_rid):
    """Return (ok, errors_list). The verdict v is a dict."""
    if not isinstance(v, dict):
        return False, ["not_a_json_object"]
    errors = []

    missing = REQUIRED_KEYS - set(v.keys())
    if missing:
        errors.append(f"missing_keys:{','.join(sorted(missing))}")

    if v.get("verdict") not in VALID_VERDICTS:
        errors.append("verdict_invalid")
    if v.get("confidence") not in VALID_CONFS:
        errors.append("confidence_invalid")

    if not isinstance(v.get("ionising_modality_present"), bool):
        errors.append("ionising_modality_present_not_bool")
    if not isinstance(v.get("quantum_sensing_in_ionising_context"), bool):
        errors.append("quantum_sensing_in_ionising_context_not_bool")

    # Consistency: verdict must match the boolean pair
    if (isinstance(v.get("ionising_modality_present"), bool) and
        isinstance(v.get("quantum_sensing_in_ionising_context"), bool) and
        v.get("verdict") in VALID_VERDICTS):
        should_promote = (v["ionising_modality_present"] and v["quantum_sensing_in_ionising_context"])
        if should_promote and v["verdict"] != "promote_to_IN":
            errors.append("verdict_inconsistent_with_booleans_should_promote")
        elif not should_promote and v["verdict"] != "stay_OUT":
            errors.append("verdict_inconsistent_with_booleans_should_stay")

    # Quote grounding (only meaningful if booleans true)
    rid = v.get("record_id")
    rec = batch_records_by_rid.get(rid)
    if rec is not None:
        title_abs = normalize_for_match(
            (rec.get("title") or "") + " " + (rec.get("abstract") or ""))
        if v.get("ionising_modality_present") is True:
            q = normalize_for_match(v.get("ionising_modality_quote", ""))
            if not q or q not in title_abs:
                errors.append("ionising_modality_quote_not_in_source")
        if v.get("quantum_sensing_in_ionising_context") is True:
            q = normalize_for_match(v.get("context_quote", ""))
            if not q or q not in title_abs:
                errors.append("context_quote_not_in_source")
    else:
        errors.append("record_id_not_in_batch")

    return (len(errors) == 0), errors


def cmd_validate():
    if not PENDING_DIR.exists():
        print("No pending dir; nothing to validate.")
        return
    batch_files = sorted(PENDING_DIR.glob("batch_*.json"))
    if not batch_files:
        print("No batches in pending; nothing to validate.")
        return
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    accepted_total = 0
    quarantined_total = 0
    skipped_total = 0

    with VERDICTS.open("a", encoding="utf-8") as vf, \
         QUARANTINE.open("a", encoding="utf-8") as qf:
        for batch_path in batch_files:
            bid = batch_path.stem.replace("batch_", "")
            verdict_path = PENDING_DIR / f"batch_{bid}.verdict.json"
            if not verdict_path.exists():
                # Subagent has not yet produced output for this batch
                continue

            batch_records = json.loads(batch_path.read_text())
            batch_records_by_rid = {r.get("record_id"): r for r in batch_records}

            try:
                verdicts_raw = json.loads(verdict_path.read_text())
            except json.JSONDecodeError as e:
                qf.write(json.dumps({"batch_id": bid, "errors": [f"parse_error:{e}"]}) + "\n")
                quarantined_total += 1
                # Move out anyway
                shutil.move(str(batch_path), str(PROCESSED_DIR / batch_path.name))
                shutil.move(str(verdict_path), str(PROCESSED_DIR / verdict_path.name))
                continue

            if not isinstance(verdicts_raw, list):
                qf.write(json.dumps({"batch_id": bid, "errors": ["verdict_not_list"]}) + "\n")
                quarantined_total += 1
                shutil.move(str(batch_path), str(PROCESSED_DIR / batch_path.name))
                shutil.move(str(verdict_path), str(PROCESSED_DIR / verdict_path.name))
                continue

            for v in verdicts_raw:
                ok, errs = validate_one(v, batch_records_by_rid)
                if ok:
                    vf.write(json.dumps(v, ensure_ascii=False) + "\n")
                    accepted_total += 1
                else:
                    qf.write(json.dumps({"verdict": v, "errors": errs}) + "\n")
                    quarantined_total += 1

            shutil.move(str(batch_path), str(PROCESSED_DIR / batch_path.name))
            shutil.move(str(verdict_path), str(PROCESSED_DIR / verdict_path.name))

    print(f"Validation complete: accepted {accepted_total}, "
          f"quarantined {quarantined_total}, skipped {skipped_total}")


# --------- status ---------

def cmd_status():
    corpus = load_input()
    verdicts = load_jsonl(VERDICTS)
    quarantine = load_jsonl(QUARANTINE)
    done = processed_record_ids()
    pending_count = 0
    if PENDING_DIR.exists():
        pending_count = len(list(PENDING_DIR.glob("batch_*.json")))
    remaining_count = len([r for r in corpus if r.get("record_id") and r["record_id"] not in done])
    print(f"Total records:      {len(corpus)}")
    print(f"Verdicts:           {len(verdicts)}")
    print(f"Unique processed:   {len(done)}")
    print(f"Remaining:          {remaining_count}")
    print(f"Quarantined:        {len(quarantine)}")
    print(f"Pending batches:    {pending_count}")


# --------- summary ---------

def cmd_summary():
    verdicts = load_jsonl(VERDICTS)
    n = len(verdicts)
    print(f"Total verdicts: {n}")
    if n == 0:
        return
    by_verdict   = Counter(v.get("verdict") for v in verdicts)
    by_conf      = Counter(v.get("confidence") for v in verdicts)
    by_modality  = Counter(v.get("ionising_modality_name") for v in verdicts if v.get("verdict") == "promote_to_IN")
    print("\nVerdict:")
    for k, c in by_verdict.most_common():
        print(f"  {c:5d}  {k}")
    print("\nConfidence:")
    for k, c in by_conf.most_common():
        print(f"  {c:5d}  {k}")
    if by_modality:
        print("\nModality of promotes:")
        for k, c in by_modality.most_common():
            print(f"  {c:5d}  {k}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("prepare")
    sp.add_argument("--per-agent", type=int, default=5)

    sub.add_parser("status")
    sub.add_parser("validate")
    sub.add_parser("summary")

    sp = sub.add_parser("next-batches")
    sp.add_argument("--total", type=int, required=True)
    sp.add_argument("--per-agent", type=int, default=5)

    args = ap.parse_args()

    if args.cmd == "prepare":
        cmd_prepare(per_agent=args.per_agent)
    elif args.cmd == "status":
        cmd_status()
    elif args.cmd == "validate":
        cmd_validate()
    elif args.cmd == "summary":
        cmd_summary()
    elif args.cmd == "next-batches":
        cmd_next_batches(args.total, args.per_agent)


if __name__ == "__main__":
    main()
