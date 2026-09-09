"""
rescue_quarantine.py
Recover verdicts that were quarantined due to overly-strict substring matching
on evidence_quote. Many subagents extracted semantically-correct quotes but
omitted or altered punctuation (e.g. dropped a parenthetical "(QUBO)"). The
loose check strips all non-alphanumeric characters before comparison, which
recovers these without re-spending LLM calls.

Behavior:
    1. Read quarantine.jsonl. Dedup by record_id, keeping the verdict that
       passes the loose check with the highest confidence (high > low).
    2. Re-validate each unique record under the LOOSE rule.
    3. Also enforce all OTHER schema requirements (categories valid, secondary
       != primary, confidence valid, reasoning present, no parse errors).
    4. Append rescued verdicts to verdicts.jsonl (skipping any record_id
       already present there).
    5. Write surviving still-quarantined records to quarantine.jsonl.bak
       (overwriting quarantine.jsonl with the residue).
    6. Print a per-category summary of rescued vs still-failing records.

Usage:
    python rescue_quarantine.py
    python rescue_quarantine.py --dry-run         # report only, no file writes

Files (relative to current directory):
    merged_dataset.json     input corpus (read-only)
    verdicts.jsonl          existing kept verdicts (appended to)
    quarantine.jsonl        input quarantine file (will be rewritten)
"""

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter, defaultdict

INPUT = "merged_dataset.json"
VERDICTS = "verdicts.jsonl"
QUARANTINE = "quarantine.jsonl"

CATEGORIES = {
    "QC_FOR_IMAGING", "QUANTUM_DOTS", "NANO_THERAPEUTICS",
    "FLUORESCENCE_PROBES", "QUANTUM_SENSING_BIOMED", "QUANTUM_CRYPTO_MED",
    "QUANTUM_INSPIRED_CLASSICAL", "CLASSICAL_AI_FOR_IMAGING",
    "CLINICAL_NON_IMAGING", "OTHER",
}

# ---------------------------------------------------------------------------
# Sanitation (must match the originals)
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

def loose(s):
    """Lowercase and collapse all non-alphanumeric to single spaces."""
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()

def quote_in_source_loose(quote, title, abstract, field):
    nq = loose(quote)
    if len(nq) < 8:                  # too short to be a meaningful quote
        return False
    if field == "title":
        return nq in loose(title)
    if field == "abstract":
        return nq in loose(abstract)
    # If field is mis-labelled, accept a hit in EITHER as long as text matches
    return nq in loose(title) or nq in loose(abstract)

# ---------------------------------------------------------------------------
# Re-validation with loose substring matching
# ---------------------------------------------------------------------------

def revalidate_one(verdict, corpus_by_id):
    """Return (ok: bool, errors: list[str])."""
    errs = []
    if not isinstance(verdict, dict):
        return False, ["not_a_dict"]
    if "_PARSE_ERROR_" in verdict:
        return False, ["parse_error_line"]

    rid = verdict.get("record_id")
    if not rid:
        return False, ["missing_record_id"]
    if rid not in corpus_by_id:
        return False, [f"record_id_not_in_corpus: {rid}"]

    src = corpus_by_id[rid]
    title = sanitize(src.get("title", ""))
    abstract = sanitize(src.get("abstract", ""))

    primary = verdict.get("primary")
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
            if not quote_in_source_loose(primary["evidence_quote"], title, abstract,
                                         primary.get("evidence_field", "")):
                errs.append("primary_evidence_quote_not_in_source_loose")

    secondary = verdict.get("secondary")
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
                if not quote_in_source_loose(secondary["evidence_quote"], title, abstract,
                                             secondary.get("evidence_field", "")):
                    errs.append("secondary_evidence_quote_not_in_source_loose")

    if verdict.get("confidence") not in {"high", "low"}:
        errs.append("confidence_invalid")
    if not isinstance(verdict.get("reasoning"), str):
        errs.append("reasoning_missing")

    return (not errs), errs

# ---------------------------------------------------------------------------
# I/O
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

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true",
                   help="Report only; do not modify any files")
    args = p.parse_args()

    print("Loading corpus and existing verdicts ...", file=sys.stderr)
    corpus = load_corpus()
    corpus_by_id = {r["id"]: r for r in corpus if r.get("id")}
    existing_verdicts = load_jsonl(VERDICTS)
    existing_ids = {v.get("record_id") for v in existing_verdicts
                    if isinstance(v, dict) and "record_id" in v}
    print(f"  Corpus: {len(corpus)}", file=sys.stderr)
    print(f"  Existing verdicts: {len(existing_verdicts)}", file=sys.stderr)

    quarantine = load_jsonl(QUARANTINE)
    print(f"  Quarantine lines: {len(quarantine)}", file=sys.stderr)

    # Dedup by record_id: keep all attempts grouped, pick best per group later
    by_rid = defaultdict(list)
    other_failures = []   # entries with no record_id (parse errors etc.)

    for entry in quarantine:
        # quarantine entries have shape {"verdict": {...}, "errors": [...]}
        verdict = entry.get("verdict") if isinstance(entry, dict) else None
        if not isinstance(verdict, dict):
            other_failures.append(entry)
            continue
        rid = verdict.get("record_id")
        if not rid:
            other_failures.append(entry)
            continue
        by_rid[rid].append(verdict)

    print(f"  Unique quarantined record_ids: {len(by_rid)}", file=sys.stderr)
    print(f"  Lines without usable record_id: {len(other_failures)}", file=sys.stderr)
    print("", file=sys.stderr)

    # For each unique record, try each attempted verdict under the loose rule.
    # Prefer: passes_loose=True, then confidence=='high' over 'low', then keep first.
    rescued = []
    still_failing = []

    for rid, attempts in by_rid.items():
        if rid in existing_ids:
            # Already in verdicts.jsonl; don't double-add. Just skip.
            continue

        ranked = []
        for v in attempts:
            ok, errs = revalidate_one(v, corpus_by_id)
            conf_rank = 0 if v.get("confidence") == "high" else 1
            ranked.append((ok, conf_rank, v, errs))
        # Sort: passing first, then high-conf first
        ranked.sort(key=lambda x: (not x[0], x[1]))
        ok, conf_rank, best_v, best_errs = ranked[0]

        if ok:
            rescued.append(best_v)
        else:
            still_failing.append({"verdict": best_v, "errors": best_errs,
                                  "n_attempts": len(attempts)})

    # Reporting
    print("=" * 60)
    print(f"Rescue results")
    print("=" * 60)
    print(f"Rescued (pass loose check):     {len(rescued)}")
    print(f"Still failing:                  {len(still_failing)}")
    print(f"Parse errors / no record_id:    {len(other_failures)}")

    if rescued:
        cat_rescued = Counter(v["primary"]["category"] for v in rescued)
        conf_rescued = Counter(v.get("confidence") for v in rescued)
        print(f"\nRescued by primary category:")
        for c, n in cat_rescued.most_common():
            print(f"  {c:30s} {n}")
        print(f"\nRescued by confidence:")
        for c, n in conf_rescued.most_common():
            print(f"  {c:10s} {n}")

    if still_failing:
        err_summary = Counter()
        cat_failing = Counter()
        for entry in still_failing:
            for e in entry["errors"]:
                err_summary[e.split(":")[0]] += 1
            v = entry["verdict"]
            if isinstance(v, dict) and "primary" in v:
                cat_failing[v["primary"].get("category", "?")] += 1
        print(f"\nStill-failing error breakdown:")
        for k, n in err_summary.most_common():
            print(f"  {k:50s} {n}")
        if cat_failing:
            print(f"\nStill-failing by intended primary category:")
            for c, n in cat_failing.most_common():
                print(f"  {c:30s} {n}")

    if args.dry_run:
        print("\n[dry-run] No files written.")
        return

    # ----- Write files -----
    # 1. Append rescued to verdicts.jsonl
    if rescued:
        with open(VERDICTS, "a", encoding="utf-8") as f:
            for v in rescued:
                # Tag the verdict so it is auditable later
                v_copy = dict(v)
                v_copy["_rescued"] = True
                f.write(json.dumps(v_copy, ensure_ascii=False) + "\n")

    # 2. Back up the original quarantine, rewrite with residue only
    if os.path.exists(QUARANTINE):
        shutil.copy(QUARANTINE, QUARANTINE + ".bak")
    with open(QUARANTINE, "w", encoding="utf-8") as f:
        for entry in still_failing:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        for entry in other_failures:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(f"\nVerdicts.jsonl appended:   +{len(rescued)} (now {len(existing_verdicts) + len(rescued)})")
    print(f"Quarantine.jsonl rewritten: {len(still_failing) + len(other_failures)} lines remaining")
    print(f"Original quarantine backed up to {QUARANTINE}.bak")


if __name__ == "__main__":
    main()
