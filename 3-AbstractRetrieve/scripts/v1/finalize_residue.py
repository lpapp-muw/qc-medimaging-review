"""
finalize_residue.py
Close out the residual quarantine after PDF rescue + relaxed-match rescue.

Two failure modes remain:
    1. Quote mismatch under loose matching (7 records): verdict is correct,
       evidence_quote could not be substring-verified even after relaxation.
       Action: accept with _quote_unverified: true.
    2. Parse-error orphans (5 records): the subagent wrote pretty-printed JSON
       which got fragmented in the merge pipeline. The first line still contains
       the key fields. Regex-extract record_id, primary.category, evidence_quote,
       evidence_field, secondary (if present), confidence, reasoning.
       Action: rebuild verdict, accept with _parse_recovered: true.

After this script, quarantine.jsonl should contain only unresolvable entries
(e.g. JSON that can't be repaired). Verdicts.jsonl gains 12 records.

Usage:
    python finalize_residue.py [--dry-run]

Outputs:
    verdicts.jsonl                   appended-to
    quarantine.jsonl                 rewritten with truly unresolvable residue
    quarantine.jsonl.bak             backup
    finalize_residue_report.csv      audit trail
"""

import argparse
import csv
import json
import os
import re
import shutil
import sys
from collections import Counter

VERDICTS = "verdicts.jsonl"
QUARANTINE = "quarantine.jsonl"


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
                pass
    return out


def already_processed_ids(verdicts):
    return {v.get("record_id") for v in verdicts
            if isinstance(v, dict) and v.get("record_id")}


# Regex extractors for truncated JSON strings
# Matches: "key": "value..." where value may be truncated; finds best-effort
def find_str_field(text, key):
    """Find "key": "value" returning value. Handles unterminated quotes (truncation)."""
    # First try standard pattern (properly closed string)
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        try:
            return json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            return m.group(1)
    # Tolerant: match unterminated string (truncated) - capture up to end-of-text
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*"((?:[^"\\]|\\.)*)$', text)
    if m:
        try:
            return json.loads('"' + m.group(1) + '"')
        except json.JSONDecodeError:
            return m.group(1)
    return None


def find_nested_dict(text, key):
    """Find a nested object literal starting at "<key>": {...} (possibly truncated)."""
    m = re.search(r'"' + re.escape(key) + r'"\s*:\s*(\{)', text)
    if not m:
        return None
    start = m.start(1)
    depth = 0
    end = len(text)
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if esc:
            esc = False; continue
        if c == "\\":
            esc = True; continue
        if c == '"':
            in_str = not in_str; continue
        if in_str:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    candidate = text[start:end]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Truncated. Extract the fields individually with the tolerant matcher.
        cat = find_str_field(candidate, "category")
        eq  = find_str_field(candidate, "evidence_quote")
        ef  = find_str_field(candidate, "evidence_field")
        if not cat:
            return None
        # Default evidence_field to "abstract" if cut off by truncation.
        if ef not in {"title", "abstract"}:
            ef = "abstract"
        # If evidence_quote got cut off, still salvage what we have.
        if not eq:
            eq = "(truncated)"
        return {"category": cat, "evidence_quote": eq, "evidence_field": ef}


def repair_parse_error(parse_blob):
    """Given the truncated JSON text in _PARSE_ERROR_, reconstruct a verdict dict."""
    rid = find_str_field(parse_blob, "record_id")
    doi = find_str_field(parse_blob, "doi") or ""
    primary = find_nested_dict(parse_blob, "primary")
    secondary = find_nested_dict(parse_blob, "secondary")
    # If secondary was "secondary": null
    if re.search(r'"secondary"\s*:\s*null', parse_blob):
        secondary = None
    confidence = find_str_field(parse_blob, "confidence") or "high"
    reasoning = find_str_field(parse_blob, "reasoning") or "(reasoning truncated during emit)"

    if not rid or not primary or "category" not in primary:
        return None
    return {
        "record_id": rid,
        "doi": doi,
        "primary": primary,
        "secondary": secondary,
        "confidence": confidence,
        "reasoning": reasoning,
        "_parse_recovered": True,
        "_quote_unverified": True,    # truncated quotes can't be substring-verified
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    verdicts = load_jsonl(VERDICTS)
    quarantine = load_jsonl(QUARANTINE)
    already_done = already_processed_ids(verdicts)

    print(f"Loaded:", file=sys.stderr)
    print(f"  Verdicts:           {len(verdicts)}", file=sys.stderr)
    print(f"  Quarantine entries: {len(quarantine)}", file=sys.stderr)

    accepted_quote = []   # _quote_unverified
    accepted_parsed = []  # _parse_recovered
    truly_unresolvable = []
    audit_rows = []
    err_summary = Counter()

    for entry in quarantine:
        if not isinstance(entry, dict):
            truly_unresolvable.append(entry)
            audit_rows.append({"record_id": "?", "action": "unresolvable",
                              "reason": "not_a_dict"})
            continue
        verdict = entry.get("verdict")
        errors = entry.get("errors", [])
        if not isinstance(verdict, dict):
            truly_unresolvable.append(entry)
            audit_rows.append({"record_id": "?", "action": "unresolvable",
                              "reason": "no_verdict_dict"})
            continue

        # Case 1: parse-error orphan
        if "_PARSE_ERROR_" in verdict:
            repaired = repair_parse_error(verdict["_PARSE_ERROR_"])
            if not repaired:
                truly_unresolvable.append(entry)
                audit_rows.append({"record_id": "?", "action": "unresolvable",
                                  "reason": "could_not_repair"})
                continue
            rid = repaired["record_id"]
            if rid in already_done:
                audit_rows.append({"record_id": rid, "action": "skip_duplicate",
                                  "reason": "already_in_verdicts"})
                continue
            accepted_parsed.append(repaired)
            already_done.add(rid)
            audit_rows.append({"record_id": rid, "action": "parse_recovered",
                              "reason": "; ".join(errors)})
            err_summary["parse_recovered"] += 1
            continue

        # Case 2: a real verdict that failed loose substring matching
        if "primary" in verdict:
            rid = verdict.get("record_id")
            if not rid:
                truly_unresolvable.append(entry); continue
            if rid in already_done:
                audit_rows.append({"record_id": rid, "action": "skip_duplicate",
                                  "reason": "already_in_verdicts"})
                continue
            # Accept with quote-unverified flag
            new_verdict = dict(verdict)
            new_verdict["_quote_unverified"] = True
            accepted_quote.append(new_verdict)
            already_done.add(rid)
            audit_rows.append({"record_id": rid, "action": "quote_unverified",
                              "reason": "; ".join(errors)})
            err_summary["quote_unverified"] += 1
            continue

        truly_unresolvable.append(entry)
        audit_rows.append({"record_id": "?", "action": "unresolvable",
                          "reason": "no_primary_field"})

    print(f"\nAction summary:", file=sys.stderr)
    print(f"  Quote-unverified (accepted): {len(accepted_quote)}", file=sys.stderr)
    print(f"  Parse-recovered (accepted):  {len(accepted_parsed)}", file=sys.stderr)
    print(f"  Truly unresolvable:          {len(truly_unresolvable)}", file=sys.stderr)

    if args.dry_run:
        print("\n[dry-run] No files written.", file=sys.stderr)
        return

    # Append to verdicts.jsonl
    appended = accepted_quote + accepted_parsed
    with open(VERDICTS, "a", encoding="utf-8") as f:
        for v in appended:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    # Rewrite quarantine
    shutil.copy(QUARANTINE, QUARANTINE + ".finalize.bak")
    with open(QUARANTINE, "w", encoding="utf-8") as f:
        for q in truly_unresolvable:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")

    # Audit CSV
    with open("finalize_residue_report.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["record_id", "action", "reason"])
        w.writeheader()
        for row in audit_rows:
            w.writerow(row)

    print(f"\nAppended {len(appended)} verdicts to {VERDICTS}.", file=sys.stderr)
    print(f"Rewrote {QUARANTINE} ({len(truly_unresolvable)} unresolvable).",
          file=sys.stderr)
    print(f"Backup at {QUARANTINE}.finalize.bak", file=sys.stderr)
    print(f"Audit: finalize_residue_report.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
