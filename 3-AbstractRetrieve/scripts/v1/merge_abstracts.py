"""
merge_abstracts.py
Validate recovered abstracts (length, word count, title overlap, placeholder
patterns) and merge accepted ones into the original Zotero CSL-JSON dataset.

Usage:
    python merge_abstracts.py \
        --input "IEEE-QC-*.json" \
        --recovered recovered_abstracts.jsonl \
        --output merged_dataset.json \
        --report validation_report.csv

Optional thresholds:
    --min-chars 250            minimum abstract char length (default 250)
    --min-words 30             minimum abstract word count (default 30)
    --max-title-jaccard 0.7    reject if token Jaccard with title >= this
    --keep-rejected            also write rejected abstracts to a debug file

Outputs:
    merged_dataset.json     all original records, abstract filled where valid,
                            plus a new field _abstract_provenance per record:
                            original | recovered:<source> | none
    validation_report.csv   per-recovered-DOI row: doi, source, char_len,
                            word_count, title_jaccard, accepted, reason

Notes:
    - Original abstracts are never overwritten or revalidated.
    - Recovered abstracts that fail validation leave the record's abstract empty
      (provenance = none), so downstream Step 2 treats them as title-only.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import Counter

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

PLACEHOLDER_PATTERNS = [
    re.compile(r"\babstract\s+(is\s+)?not\s+available\b", re.I),
    re.compile(r"\bno\s+abstract(\s+available)?\b", re.I),
    re.compile(r"\babstract\s+unavailable\b", re.I),
    re.compile(r"^\s*n[/\\]?a\s*$", re.I),
    re.compile(r"^\s*\.+\s*$"),
    re.compile(r"^\s*-+\s*$"),
]

WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]+")


def normalize_text(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def tokens(s: str) -> set:
    return set(WORD_RE.findall(s.lower()))


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def validate(abstract: str, title: str, min_chars: int, min_words: int,
             max_title_jaccard: float):
    """Return (accepted: bool, reason: str, char_len, word_count, title_j)."""
    if not abstract:
        return False, "empty", 0, 0, 0.0

    char_len = len(abstract)
    words = WORD_RE.findall(abstract)
    word_count = len(words)

    # Placeholder check
    norm_ab = normalize_text(abstract)
    for pat in PLACEHOLDER_PATTERNS:
        if pat.search(norm_ab):
            return False, "placeholder", char_len, word_count, 0.0

    # Length check
    if char_len < min_chars:
        return False, f"chars<{min_chars}", char_len, word_count, 0.0

    # Word count check
    if word_count < min_words:
        return False, f"words<{min_words}", char_len, word_count, 0.0

    # Title overlap check
    norm_title = normalize_text(title)
    if norm_title:
        # Substring check (either direction)
        if norm_ab in norm_title or norm_title in norm_ab and char_len < 2 * len(norm_title):
            return False, "abstract_is_title", char_len, word_count, 1.0

        # Token Jaccard
        t_tok = tokens(title)
        a_tok = tokens(abstract)
        j = jaccard(t_tok, a_tok)
        if j >= max_title_jaccard:
            return False, f"title_jaccard>={max_title_jaccard:.2f}", char_len, word_count, j
    else:
        j = 0.0

    return True, "ok", char_len, word_count, j

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_originals(input_glob):
    files = sorted(glob.glob(input_glob))
    if not files and os.path.exists(input_glob):
        files = [input_glob]
    if not files:
        sys.exit(f"No input files matched: {input_glob}")
    records = []
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            records.extend(json.load(f))
    return records, files


def load_recovered(path):
    """Return dict keyed by lowercased DOI -> {source, abstract}."""
    if not os.path.exists(path):
        sys.exit(f"Recovered file not found: {path}")
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            doi = (rec.get("doi") or "").strip().lower()
            if not doi:
                continue
            ab = rec.get("abstract")
            if not ab:
                # Miss-line, no abstract, but still record so we know it was tried
                out[doi] = {"source": rec.get("source"), "abstract": None}
            else:
                out[doi] = {"source": rec.get("source"), "abstract": ab}
    return out


def main():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", required=True)
    p.add_argument("--recovered", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--min-chars", type=int, default=250)
    p.add_argument("--min-words", type=int, default=30)
    p.add_argument("--max-title-jaccard", type=float, default=0.7)
    p.add_argument("--keep-rejected", action="store_true",
                   help="Write rejected abstracts to <output>.rejected.jsonl for inspection")
    args = p.parse_args()

    print("Loading originals ...", file=sys.stderr)
    records, files = load_originals(args.input)
    print(f"  Files: {len(files)}", file=sys.stderr)
    print(f"  Records: {len(records)}", file=sys.stderr)

    print(f"\nLoading recovered abstracts from {args.recovered} ...", file=sys.stderr)
    recovered = load_recovered(args.recovered)
    print(f"  Lines (unique DOIs): {len(recovered)}", file=sys.stderr)

    # ---- Validate recovered abstracts and merge ----
    report_rows = []
    rejected_inspect = []
    counts = Counter()
    accept_by_source = Counter()
    reject_by_reason = Counter()

    enriched = []
    for r in records:
        doi = (r.get("DOI") or "").strip()
        original_ab = (r.get("abstract") or "").strip()
        out = dict(r)  # shallow copy

        if original_ab:
            out["_abstract_provenance"] = "original"
            counts["original_kept"] += 1
            enriched.append(out)
            continue

        # No original abstract. Look in recovered.
        rec = recovered.get(doi.lower()) if doi else None
        if not rec or not rec.get("abstract"):
            out["_abstract_provenance"] = "none"
            counts["no_recovered"] += 1
            enriched.append(out)
            if doi and rec:  # tried, missed at API level
                report_rows.append({
                    "doi": doi, "source": rec.get("source") or "",
                    "char_len": 0, "word_count": 0, "title_jaccard": 0.0,
                    "accepted": False, "reason": "api_miss",
                })
            continue

        ab = rec["abstract"]
        title = r.get("title", "") or ""
        accepted, reason, cl, wc, tj = validate(
            ab, title, args.min_chars, args.min_words, args.max_title_jaccard
        )
        report_rows.append({
            "doi": doi, "source": rec.get("source") or "",
            "char_len": cl, "word_count": wc, "title_jaccard": round(tj, 3),
            "accepted": accepted, "reason": reason,
        })
        if accepted:
            out["abstract"] = ab
            out["_abstract_provenance"] = f"recovered:{rec.get('source') or 'unknown'}"
            counts["recovered_accepted"] += 1
            accept_by_source[rec.get("source") or "unknown"] += 1
        else:
            out["_abstract_provenance"] = "none"
            counts["recovered_rejected"] += 1
            reject_by_reason[reason] += 1
            if args.keep_rejected:
                rejected_inspect.append({
                    "doi": doi, "source": rec.get("source"),
                    "title": title, "rejected_abstract": ab, "reason": reason,
                    "char_len": cl, "word_count": wc, "title_jaccard": tj,
                })
        enriched.append(out)

    # ---- Write outputs ----
    print(f"\nWriting merged dataset to {args.output} ...", file=sys.stderr)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(enriched, f, ensure_ascii=False, indent=2)

    print(f"Writing validation report to {args.report} ...", file=sys.stderr)
    with open(args.report, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "doi", "source", "char_len", "word_count",
            "title_jaccard", "accepted", "reason",
        ])
        w.writeheader()
        for row in report_rows:
            w.writerow(row)

    if args.keep_rejected and rejected_inspect:
        rej_path = args.output + ".rejected.jsonl"
        with open(rej_path, "w", encoding="utf-8") as f:
            for r in rejected_inspect:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"Wrote {len(rejected_inspect)} rejected abstracts to {rej_path}", file=sys.stderr)

    # ---- Summary ----
    total = len(records)
    print("\n" + "=" * 60, file=sys.stderr)
    print(f"Total records:                       {total}", file=sys.stderr)
    print(f"  Already had abstract (original):   {counts['original_kept']}",
          file=sys.stderr)
    print(f"  No recovery available:             {counts['no_recovered']}",
          file=sys.stderr)
    print(f"  Recovery accepted:                 {counts['recovered_accepted']}",
          file=sys.stderr)
    print(f"  Recovery rejected:                 {counts['recovered_rejected']}",
          file=sys.stderr)
    final_with_ab = counts["original_kept"] + counts["recovered_accepted"]
    print(f"\nFinal abstract coverage: {final_with_ab}/{total} "
          f"({100*final_with_ab/total:.1f}%)", file=sys.stderr)

    if accept_by_source:
        print("\nAccepted recoveries by source:", file=sys.stderr)
        for s, n in accept_by_source.most_common():
            print(f"  {s:12s} {n}", file=sys.stderr)
    if reject_by_reason:
        print("\nRejection reasons:", file=sys.stderr)
        for r, n in reject_by_reason.most_common():
            print(f"  {r:25s} {n}", file=sys.stderr)


if __name__ == "__main__":
    main()
