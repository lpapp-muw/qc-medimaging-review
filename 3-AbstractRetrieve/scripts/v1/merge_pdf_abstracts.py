"""
merge_pdf_abstracts.py
1. Merge validated abstracts from rescued_abstracts_pdf.jsonl into merged_dataset.json.
2. Move the old verdicts for those records from verdicts.jsonl into
   verdicts_pre_pdf_rescue.jsonl (backup) so the next subagent re-screen produces
   fresh verdicts to be merged later.
3. Write verdicts_manual_template.jsonl with empty stubs for records that had a
   PDF but failed validation (e.g. Springer Nature OPEN format), so the user can
   hand-author verdicts for them.

Outputs:
    merged_dataset.json                      (in-place update; backup written)
    merged_dataset.json.pre_pdf_rescue.bak   (backup of the original)
    verdicts.jsonl                           (in-place: rescued records removed)
    verdicts_pre_pdf_rescue.jsonl            (the removed verdicts; for diff tracking)
    verdicts_manual_template.jsonl           (one stub per failed-validation rescue)
    pdf_rescue_summary.csv                   (audit trail of which abstracts changed)

Usage:
    python merge_pdf_abstracts.py \\
        --corpus merged_dataset.json \\
        --rescued rescued_abstracts_pdf.jsonl \\
        --verdicts verdicts.jsonl \\
        [--dry-run]
"""

import argparse
import csv
import json
import os
import shutil
import sys
from collections import Counter

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


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", default="merged_dataset.json")
    p.add_argument("--rescued", default="rescued_abstracts_pdf.jsonl")
    p.add_argument("--verdicts", default="verdicts.jsonl")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not os.path.exists(args.corpus):
        sys.exit(f"Missing {args.corpus}")
    if not os.path.exists(args.rescued):
        sys.exit(f"Missing {args.rescued}")

    print("Loading inputs ...", file=sys.stderr)
    with open(args.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    rescued = load_jsonl(args.rescued)
    verdicts = load_jsonl(args.verdicts)

    print(f"  Corpus records:    {len(corpus)}", file=sys.stderr)
    print(f"  Rescued entries:   {len(rescued)}", file=sys.stderr)
    print(f"  Verdicts:          {len(verdicts)}", file=sys.stderr)

    # Index corpus by record_id and by lowercased DOI for matching
    by_rid = {r.get("id"): r for r in corpus if r.get("id")}
    by_doi = {(r.get("DOI") or "").lower().strip(): r for r in corpus
              if (r.get("DOI") or "").strip()}

    # Categorize rescued entries
    rescued_accepted = []        # to merge into corpus
    rescued_rejected = []        # title-only validation fail; need manual verdict
    counts = Counter()
    rejection_reasons = Counter()

    for r in rescued:
        if r.get("accepted"):
            rescued_accepted.append(r)
            counts["accepted"] += 1
        else:
            rescued_rejected.append(r)
            counts["rejected"] += 1
            rejection_reasons[r.get("reason", "unknown")] += 1

    print(f"\nRescued breakdown:", file=sys.stderr)
    print(f"  Validated:   {counts['accepted']}", file=sys.stderr)
    print(f"  Rejected:    {counts['rejected']}", file=sys.stderr)
    if rejection_reasons:
        for k, n in rejection_reasons.most_common():
            print(f"    {k:30s} {n}", file=sys.stderr)

    # Merge abstracts into corpus
    merged_rids = []           # record_ids that got an abstract upgrade
    audit_rows = []            # for the CSV summary

    for r in rescued_accepted:
        doi = (r.get("doi") or "").lower().strip()
        rid = r.get("record_id")
        # Prefer record_id lookup; fall back to DOI
        rec = by_rid.get(rid) or by_doi.get(doi)
        if not rec:
            audit_rows.append({
                "doi": doi, "record_id": rid, "action": "skip",
                "reason": "record_not_in_corpus", "old_chars": 0,
                "new_chars": len(r.get("abstract") or ""),
            })
            continue
        old_ab = rec.get("abstract") or ""
        new_ab = r["abstract"]
        action = "added" if not old_ab.strip() else "replaced"
        if not args.dry_run:
            rec["abstract"] = new_ab
            rec["_abstract_provenance"] = f"recovered:{r.get('source', 'pdf')}"
            rec["_pdf_rescued"] = True
        merged_rids.append(rec["id"])
        audit_rows.append({
            "doi": doi, "record_id": rec["id"], "action": action,
            "reason": "ok", "old_chars": len(old_ab), "new_chars": len(new_ab),
        })

    print(f"\nAbstracts merged into corpus: {len(merged_rids)}", file=sys.stderr)

    # Remove the corresponding verdicts (back them up first)
    merged_rid_set = set(merged_rids)
    keep_verdicts = []
    removed_verdicts = []
    for v in verdicts:
        if v.get("record_id") in merged_rid_set:
            removed_verdicts.append(v)
        else:
            keep_verdicts.append(v)
    print(f"Verdicts to remove (old, title-only based): {len(removed_verdicts)}",
          file=sys.stderr)
    print(f"Verdicts remaining:                          {len(keep_verdicts)}",
          file=sys.stderr)

    # Manual-template stubs for rejected PDFs
    manual_stubs = []
    for r in rescued_rejected:
        rid = r.get("record_id")
        rec = by_rid.get(rid)
        if not rec:
            continue
        manual_stubs.append({
            "record_id": rid,
            "doi": r.get("doi", ""),
            "title": rec.get("title", ""),
            "pdf_abs_path": r.get("pdf_abs_path", ""),
            "_failed_extraction_reason": r.get("reason", ""),
            "_extracted_text_preview": (r.get("abstract") or "")[:400],
            "primary": {
                "category": "FILL_ME_IN_one_of_10_categories",
                "evidence_quote": "FILL_ME_IN_verbatim_quote_from_title_or_abstract",
                "evidence_field": "title",
            },
            "secondary": None,
            "confidence": "high",
            "reasoning": "FILL_ME_IN_one_sentence_justification",
            "_manual": True,
        })

    if args.dry_run:
        print("\n[dry-run] No files written.", file=sys.stderr)
        # Still print where things would land
        return

    # ---- Write outputs ----
    backup_corpus = args.corpus + ".pre_pdf_rescue.bak"
    shutil.copy(args.corpus, backup_corpus)
    with open(args.corpus, "w", encoding="utf-8") as f:
        json.dump(corpus, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {args.corpus} (backup at {backup_corpus})", file=sys.stderr)

    # Backup verdicts and write the surviving subset
    backup_verdicts = "verdicts_pre_pdf_rescue.jsonl"
    with open(backup_verdicts, "w", encoding="utf-8") as f:
        for v in removed_verdicts:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    with open(args.verdicts, "w", encoding="utf-8") as f:
        for v in keep_verdicts:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")
    print(f"Wrote {args.verdicts} ({len(keep_verdicts)} kept)", file=sys.stderr)
    print(f"Wrote {backup_verdicts} ({len(removed_verdicts)} backed up)",
          file=sys.stderr)

    # Manual stubs
    if manual_stubs:
        with open("verdicts_manual_template.jsonl", "w", encoding="utf-8") as f:
            for s in manual_stubs:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"Wrote verdicts_manual_template.jsonl ({len(manual_stubs)} stubs)",
              file=sys.stderr)
        print(f"  Fill these in by hand, then concatenate into verdicts.jsonl.",
              file=sys.stderr)

    # CSV summary
    with open("pdf_rescue_summary.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "doi", "record_id", "action", "reason", "old_chars", "new_chars",
        ])
        w.writeheader()
        for row in audit_rows:
            w.writerow(row)
    print(f"Wrote pdf_rescue_summary.csv", file=sys.stderr)


if __name__ == "__main__":
    main()
