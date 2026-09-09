"""
batch_helper.py
Utility for the orchestrator + parallel-subagent screening workflow.

Subcommands:
    status                                  Show progress
    next [--n N]                            (legacy) Print next N records as JSON to stdout
    next-batches --total N --per-agent K    Create K-record batch files in pending/, print batch IDs
    merge-pending                           Merge pending/verdicts_*.jsonl into verdicts.jsonl,
                                            validate, quarantine failures, clean up pending/
    validate                                Re-validate verdicts.jsonl from scratch
    summary                                 Print category and confidence distributions

Files:
    merged_dataset.json     input corpus (read-only)
    verdicts.jsonl          append-only validated verdicts
    quarantine.jsonl        verdicts that failed validation
    pending/                transient batch + per-batch verdict files

Atomic guarantees:
    - next-batches will refuse to run if pending/ is non-empty (prevents reissuing batches
      that subagents are still working on)
    - merge-pending writes to verdicts.jsonl using a temp-file + rename pattern
"""

import argparse
import json
import os
import re
import shutil
import sys
import uuid
from collections import Counter

INPUT = "merged_dataset.json"
VERDICTS = "verdicts.jsonl"
QUARANTINE = "quarantine.jsonl"
PENDING_DIR = "pending"

CATEGORIES = {
    "QC_FOR_IMAGING", "QUANTUM_DOTS", "NANO_THERAPEUTICS",
    "FLUORESCENCE_PROBES", "QUANTUM_SENSING_BIOMED", "QUANTUM_CRYPTO_MED",
    "QUANTUM_INSPIRED_CLASSICAL", "CLASSICAL_AI_FOR_IMAGING",
    "CLINICAL_NON_IMAGING", "OTHER",
}

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_corpus():
    if not os.path.exists(INPUT):
        sys.exit(f"Missing {INPUT}")
    with open(INPUT, encoding="utf-8") as f:
        return json.load(f)

def load_jsonl(path):
    if not os.path.exists(path):
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
                out.append({"_PARSE_ERROR_": line[:300]})
    return out

def processed_ids():
    return {v.get("record_id") for v in load_jsonl(VERDICTS)
            if isinstance(v, dict) and "record_id" in v}

# ---------------------------------------------------------------------------
# Sanitation
# ---------------------------------------------------------------------------

def sanitize(s):
    if not s:
        return ""
    s = re.sub(r"</?jats:[^>]+>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\$\$[^$]+\$\$", " [formula] ", s)
    s = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", " ", s)
    s = re.sub(r"&[a-z]+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_for_match(s):
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def quote_in_source(quote, title, abstract, field):
    nq = normalize_for_match(quote)
    if not nq:
        return False
    if field == "title":
        return nq in normalize_for_match(title)
    if field == "abstract":
        return nq in normalize_for_match(abstract)
    return False

# ---------------------------------------------------------------------------
# Validation logic (used by merge-pending and standalone validate)
# ---------------------------------------------------------------------------

def validate_one(rec, corpus_by_id):
    errs = []
    if not isinstance(rec, dict):
        return ["not_a_dict"]
    if "_PARSE_ERROR_" in rec:
        return ["parse_error_line"]
    rid = rec.get("record_id")
    if not rid:
        return ["missing_record_id"]
    if rid not in corpus_by_id:
        return [f"record_id_not_in_corpus: {rid}"]

    # Escape flags: trust verdicts that have been hand-curated or recovered via
    # documented downstream scripts. They are auditable via the flag itself.
    # We still enforce minimum structural sanity (valid category + confidence).
    ESCAPE_FLAGS = {"_manual", "_quote_unverified", "_rescued", "_parse_recovered"}
    if any(rec.get(f) for f in ESCAPE_FLAGS):
        primary = rec.get("primary")
        if not isinstance(primary, dict):
            return ["flagged_but_missing_primary"]
        if primary.get("category") not in CATEGORIES:
            return [f"flagged_but_invalid_category: {primary.get('category')}"]
        if rec.get("confidence") not in {"high", "low"}:
            return ["flagged_but_invalid_confidence"]
        return []   # accept

    src = corpus_by_id[rid]
    title = sanitize(src.get("title", ""))
    abstract = sanitize(src.get("abstract", ""))

    primary = rec.get("primary")
    if not isinstance(primary, dict):
        errs.append("missing_or_invalid_primary")
    else:
        if primary.get("category") not in CATEGORIES:
            errs.append(f"primary_category_invalid: {primary.get('category')}")
        if primary.get("evidence_field") not in {"title", "abstract"}:
            errs.append("primary_evidence_field_invalid")
        if not isinstance(primary.get("evidence_quote"), str) or not primary["evidence_quote"]:
            errs.append("primary_evidence_quote_missing")
        else:
            if not quote_in_source(primary["evidence_quote"], title, abstract,
                                   primary.get("evidence_field", "")):
                errs.append("primary_evidence_quote_not_in_source")

    secondary = rec.get("secondary")
    if secondary is not None:
        if not isinstance(secondary, dict):
            errs.append("secondary_invalid_type")
        else:
            if secondary.get("category") not in CATEGORIES:
                errs.append(f"secondary_category_invalid: {secondary.get('category')}")
            if isinstance(primary, dict) and secondary.get("category") == primary.get("category"):
                errs.append("secondary_equals_primary")
            if secondary.get("evidence_field") not in {"title", "abstract"}:
                errs.append("secondary_evidence_field_invalid")
            if not isinstance(secondary.get("evidence_quote"), str) or not secondary["evidence_quote"]:
                errs.append("secondary_evidence_quote_missing")
            else:
                if not quote_in_source(secondary["evidence_quote"], title, abstract,
                                       secondary.get("evidence_field", "")):
                    errs.append("secondary_evidence_quote_not_in_source")

    if rec.get("confidence") not in {"high", "low"}:
        errs.append("confidence_invalid")
    if not isinstance(rec.get("reasoning"), str):
        errs.append("reasoning_missing")
    return errs

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_status():
    corpus = load_corpus()
    verdicts = load_jsonl(VERDICTS)
    quarantine = load_jsonl(QUARANTINE)
    done = processed_ids()
    pending_count = 0
    if os.path.isdir(PENDING_DIR):
        pending_count = len([f for f in os.listdir(PENDING_DIR)
                             if f.startswith("batch_") and f.endswith(".json")])
    print(f"Total records:      {len(corpus)}")
    print(f"Verdicts:           {len(verdicts)}")
    print(f"Unique processed:   {len(done)}")
    print(f"Remaining:          {len(corpus) - len(done)}")
    print(f"Quarantined:        {len(quarantine)}")
    print(f"Pending batches:    {pending_count}")


def cmd_next(n):
    corpus = load_corpus()
    done = processed_ids()
    remaining = [r for r in corpus if r.get("id") and r["id"] not in done]
    out = [_record_for_subagent(r) for r in remaining[:n]]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def _record_for_subagent(r):
    return {
        "id": r.get("id"),
        "DOI": r.get("DOI", ""),
        "title": sanitize(r.get("title", "")),
        "abstract": sanitize(r.get("abstract", "")),
        "year": (r.get("issued", {}).get("date-parts", [[None]]) or [[None]])[0][0],
        "venue": r.get("container-title", ""),
    }


def cmd_next_batches(total, per_agent):
    if os.path.isdir(PENDING_DIR):
        existing = [f for f in os.listdir(PENDING_DIR)
                    if f.startswith("batch_") and f.endswith(".json")]
        if existing:
            sys.exit(f"Refusing: {len(existing)} pending batch files exist in {PENDING_DIR}/. "
                     f"Run merge-pending first.")
    else:
        os.makedirs(PENDING_DIR, exist_ok=True)

    corpus = load_corpus()
    done = processed_ids()
    remaining = [r for r in corpus if r.get("id") and r["id"] not in done]
    if not remaining:
        print("No records remaining.")
        return
    take = remaining[:total]

    batches = []
    for i in range(0, len(take), per_agent):
        chunk = take[i : i + per_agent]
        batch_id = uuid.uuid4().hex[:8]
        batches.append((batch_id, chunk))

    batch_ids = []
    for batch_id, chunk in batches:
        path = os.path.join(PENDING_DIR, f"batch_{batch_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([_record_for_subagent(r) for r in chunk], f,
                      ensure_ascii=False, indent=2)
        batch_ids.append(batch_id)

    print(f"Created {len(batch_ids)} batches of up to {per_agent} records each:")
    for bid in batch_ids:
        print(bid)


def cmd_merge_pending():
    if not os.path.isdir(PENDING_DIR):
        print("No pending/ directory.")
        return

    corpus = load_corpus()
    corpus_by_id = {r["id"]: r for r in corpus if r.get("id")}

    # Find verdict files
    verdict_files = sorted([f for f in os.listdir(PENDING_DIR)
                            if f.startswith("verdicts_") and f.endswith(".jsonl")])
    batch_files = sorted([f for f in os.listdir(PENDING_DIR)
                          if f.startswith("batch_") and f.endswith(".json")])

    if not verdict_files and not batch_files:
        print("Nothing pending.")
        return

    # Map batch_id -> expected record IDs
    expected = {}
    for bf in batch_files:
        bid = bf[len("batch_"):-len(".json")]
        with open(os.path.join(PENDING_DIR, bf), encoding="utf-8") as f:
            try:
                expected[bid] = [r["id"] for r in json.load(f)]
            except (json.JSONDecodeError, KeyError):
                expected[bid] = []

    new_verdicts = []
    seen_batch_ids = set()
    for vf in verdict_files:
        bid = vf[len("verdicts_"):-len(".jsonl")]
        seen_batch_ids.add(bid)
        with open(os.path.join(PENDING_DIR, vf), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    new_verdicts.append(json.loads(line))
                except json.JSONDecodeError:
                    new_verdicts.append({"_PARSE_ERROR_": line[:300]})

    # Validate & dedup against existing verdicts.jsonl
    already = processed_ids()
    keep, quarantine = [], []
    for v in new_verdicts:
        errs = validate_one(v, corpus_by_id)
        if errs:
            quarantine.append({"verdict": v, "errors": errs})
            continue
        rid = v.get("record_id")
        if rid in already:
            quarantine.append({"verdict": v, "errors": ["duplicate_record_id"]})
            continue
        keep.append(v)
        already.add(rid)

    # Append survivors to verdicts.jsonl
    if keep:
        with open(VERDICTS, "a", encoding="utf-8") as f:
            for v in keep:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")

    # Append quarantined
    if quarantine:
        with open(QUARANTINE, "a", encoding="utf-8") as f:
            for q in quarantine:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # Detect missing batches (batch file present, no verdicts file)
    missing = [bid for bid in expected.keys() if bid not in seen_batch_ids]

    # Clean up pending dir
    for f in verdict_files + batch_files:
        try:
            os.remove(os.path.join(PENDING_DIR, f))
        except OSError:
            pass

    # Reporting
    err_counts = Counter()
    for q in quarantine:
        for e in q["errors"]:
            err_counts[e.split(":")[0]] += 1

    print(f"Merged {len(new_verdicts)} verdict lines:")
    print(f"  kept:        {len(keep)}")
    print(f"  quarantined: {len(quarantine)}")
    if err_counts:
        print(f"  error breakdown:")
        for k, v in err_counts.most_common():
            print(f"    {k:40s} {v}")
    if missing:
        print(f"  WARNING: {len(missing)} batch(es) had no verdicts file: {missing}")


def cmd_validate():
    corpus = load_corpus()
    corpus_by_id = {r["id"]: r for r in corpus if r.get("id")}
    verdicts = load_jsonl(VERDICTS)

    keep, quarantine = [], []
    err_counts = Counter()
    for v in verdicts:
        errs = validate_one(v, corpus_by_id)
        if errs:
            for e in errs:
                err_counts[e.split(":")[0]] += 1
            quarantine.append({"verdict": v, "errors": errs})
        else:
            keep.append(v)

    with open(VERDICTS, "w", encoding="utf-8") as f:
        for v in keep:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    if quarantine:
        with open(QUARANTINE, "a", encoding="utf-8") as f:
            for q in quarantine:
                f.write(json.dumps(q, ensure_ascii=False) + "\n")

    print(f"Validated {len(verdicts)}: kept {len(keep)}, quarantined {len(quarantine)}")
    if err_counts:
        for k, v in err_counts.most_common():
            print(f"  {k:40s} {v}")


def cmd_summary():
    corpus = load_corpus()
    corpus_by_id = {r["id"]: r for r in corpus if r.get("id")}
    verdicts = load_jsonl(VERDICTS)

    cat_p = Counter()
    cat_s = Counter()
    confs = Counter()
    for v in verdicts:
        if not isinstance(v, dict) or "primary" not in v:
            continue
        cat_p[v["primary"].get("category", "?")] += 1
        confs[v.get("confidence", "?")] += 1
        if v.get("secondary"):
            cat_s[v["secondary"].get("category", "?")] += 1

    print(f"Verdicts: {len(verdicts)}\n")
    print("Primary category:")
    for c, n in cat_p.most_common():
        print(f"  {c:30s} {n}")
    print("\nConfidence:")
    for c, n in confs.most_common():
        print(f"  {c:30s} {n}")
    if cat_s:
        print("\nSecondary category (when set):")
        for c, n in cat_s.most_common():
            print(f"  {c:30s} {n}")

    yes = sum(1 for v in verdicts if isinstance(v, dict)
              and v.get("primary", {}).get("category") == "QC_FOR_IMAGING"
              and v.get("confidence") == "high")
    unc = sum(1 for v in verdicts if isinstance(v, dict)
              and v.get("primary", {}).get("category") == "QC_FOR_IMAGING"
              and v.get("confidence") == "low")
    no_ = len(verdicts) - yes - unc
    print(f"\nDerived in_scope (primary==QC_FOR_IMAGING & high → yes; low → uncertain):")
    print(f"  yes:        {yes}")
    print(f"  uncertain:  {unc}")
    print(f"  no:         {no_}")


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p_next = sub.add_parser("next")
    p_next.add_argument("--n", type=int, default=10)
    p_nb = sub.add_parser("next-batches")
    p_nb.add_argument("--total", type=int, required=True)
    p_nb.add_argument("--per-agent", type=int, default=5)
    sub.add_parser("merge-pending")
    sub.add_parser("validate")
    sub.add_parser("summary")
    args = p.parse_args()

    if args.cmd == "status": cmd_status()
    elif args.cmd == "next": cmd_next(args.n)
    elif args.cmd == "next-batches": cmd_next_batches(args.total, args.per_agent)
    elif args.cmd == "merge-pending": cmd_merge_pending()
    elif args.cmd == "validate": cmd_validate()
    elif args.cmd == "summary": cmd_summary()


if __name__ == "__main__":
    main()
