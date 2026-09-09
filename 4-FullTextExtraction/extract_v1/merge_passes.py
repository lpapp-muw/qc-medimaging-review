"""
merge_passes.py — Cross-chunk and cross-pass merge for Step-4 extraction.

Two responsibilities:

1. CROSS-CHUNK merge (within a pass): for the 8 multi-chunk papers, combine the
   per-chunk verdict records of a pass into ONE merged record per paper.
   Policy (locked with the chunker):
     - multi-value list fields  -> union, dedup by (normalised value, quote)
     - graded enum fields       -> take the MAX grade; flag _grade_disagreement
                                   if chunks disagreed
     - boolean fields           -> any-true-wins; flag _boolean_unanimity=false
                                   if chunks disagreed
     - scalar enum / verbatim   -> prefer the first NON-null-equivalent value;
                                   if multiple distinct non-null values, keep the
                                   first and record alternates under
                                   _value_alternates
     - free-text                -> prefer first non-null; alternates recorded

2. CROSS-PASS assembly: write the per-paper merged-pass inputs that downstream
   passes consume, and the final consolidated A+B+C+D record.
     - processed_step4/merged_inputs/<stable>.A.json   (Pass A merged)
     - processed_step4/merged_inputs/<stable>.B.json   (Pass B merged) -> Pass D, E
     - processed_step4/merged_inputs/<stable>.C.json   (Pass C merged) -> Pass D, E
     - processed_step4/merged_inputs/<stable>.D.json   (Pass D merged) -> Pass E
     - extractions_merged.jsonl   (one consolidated record per paper, all passes)

Staging (run between orchestrator phases):
  python3 merge_passes.py --pass A      # after Pass A validated
  python3 merge_passes.py --pass B      # after Pass B validated
  python3 merge_passes.py --pass C      # after Pass C validated
     (now Pass D can be prepared: it reads .B.json + .C.json)
  python3 merge_passes.py --pass D      # after Pass D validated
     (now Pass E can be prepared: it reads .A/.B/.C/.D.json)
  python3 merge_passes.py --pass E      # after Pass E validated
  python3 merge_passes.py --consolidate # produce extractions_merged.jsonl

Python 3.8 compatible.
"""

import argparse
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

import paths_step4 as P
import extraction_schema as S
from validate_extraction import is_null_equivalent, normalise


VERDICT_FILE = {
    "A": P.VERDICTS_PASS_A,
    "B": P.VERDICTS_PASS_B,
    "C": P.VERDICTS_PASS_C,
    "D": P.VERDICTS_PASS_D,
    "E": P.VERDICTS_PASS_E,
}

MERGED_INPUTS_DIR = P.PROCESSED_DIR / "merged_inputs"

# Graded enums where "max grade" semantics apply (ordered low->high)
GRADE_ORDER = {
    "baseline_rigour_grade": S.BASELINE_RIGOUR_ENUM,          # none..matched_data_compute_stats
    "dataset_realism_grade": S.DATASET_REALISM_ENUM,          # toy..prospective_clinical
}


# ----------------------------------------------------------------------------
# Load verdicts grouped by paper
# ----------------------------------------------------------------------------
def load_verdicts_by_paper(pass_letter):
    """Return OrderedDict stable_name -> list of chunk records (in chunk order)."""
    vf = VERDICT_FILE[pass_letter]
    by_paper = defaultdict(list)
    if not vf.exists():
        return by_paper
    with open(vf, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            stable = rec.get("_meta", {}).get("stable_name")
            if stable:
                by_paper[stable].append(rec)
    # sort each paper's records by chunk_id
    for stable in by_paper:
        by_paper[stable].sort(key=lambda r: r.get("_meta", {}).get("chunk_id", 0))
    return by_paper


# ----------------------------------------------------------------------------
# Field-level merge helpers
# ----------------------------------------------------------------------------
def _norm_key(entry):
    """Dedup key for a multi-value/scalar entry: (normalised value, quote)."""
    if isinstance(entry, dict):
        val = entry.get("value")
        q = entry.get("quote") or ""
    else:
        val, q = entry, ""
    val_key = json.dumps(val, sort_keys=True) if isinstance(val, (dict, list)) else normalise(str(val))
    return (val_key, normalise(str(q)))


def _merge_multivalue(chunk_entries):
    """Union list entries across chunks, dedup by (value, quote)."""
    seen = OrderedDict()
    for lst in chunk_entries:
        if not isinstance(lst, list):
            continue
        for item in lst:
            seen.setdefault(_norm_key(item), item)
    return list(seen.values())


def _grade_index(field, value):
    order = GRADE_ORDER[field]
    try:
        return order.index(value)
    except (ValueError, TypeError):
        return -1


def _merge_grade(field, chunk_entries):
    """Take the max grade; flag disagreement."""
    best = None
    best_idx = -1
    seen_idxs = set()
    for e in chunk_entries:
        if not isinstance(e, dict):
            continue
        v = e.get("value")
        if is_null_equivalent(v):
            continue
        idx = _grade_index(field, v)
        seen_idxs.add(idx)
        if idx > best_idx:
            best_idx = idx
            best = e
    if best is None:
        # all null-equivalent; return the first entry as-is
        return chunk_entries[0] if chunk_entries else {"value": None, "quote": None}
    merged = dict(best)
    if len([i for i in seen_idxs if i >= 0]) > 1:
        merged["_grade_disagreement"] = True
    return merged


def _merge_bool(chunk_entries):
    """Any-true-wins; flag non-unanimity."""
    vals = []
    chosen = None
    for e in chunk_entries:
        if not isinstance(e, dict):
            continue
        v = e.get("value")
        vals.append(v)
        if v is True and chosen is None:
            chosen = e
    if chosen is None:
        chosen = chunk_entries[0] if chunk_entries else {"value": False, "quote": None}
    merged = dict(chosen)
    truthy = set(v for v in vals if v in (True, False))
    if len(truthy) > 1:
        merged["_boolean_unanimity"] = False
    return merged


def _merge_scalar(chunk_entries):
    """Prefer first non-null-equivalent; record distinct alternates."""
    chosen = None
    alternates = []
    for e in chunk_entries:
        if not isinstance(e, dict):
            continue
        v = e.get("value")
        if is_null_equivalent(v):
            continue
        if chosen is None:
            chosen = e
        elif normalise(str(v)) != normalise(str(chosen.get("value"))):
            alternates.append(e)
    if chosen is None:
        return chunk_entries[0] if chunk_entries else {"value": None, "quote": None}
    merged = dict(chosen)
    if alternates:
        merged["_value_alternates"] = [a.get("value") for a in alternates]
    return merged


# ----------------------------------------------------------------------------
# Cross-chunk merge for one paper, one pass
# ----------------------------------------------------------------------------
def merge_chunks_for_paper(pass_letter, chunk_records):
    """Merge a paper's per-chunk records of one pass into one record."""
    if len(chunk_records) == 1:
        # single chunk: return as-is (drop chunk_id-specific noise later)
        merged = dict(chunk_records[0])
        merged.setdefault("_meta", {})
        merged["_meta"]["n_chunks"] = 1
        return merged

    merged = {}
    # collect the union of field names present
    fields = set()
    for r in chunk_records:
        fields.update(k for k in r.keys() if not k.startswith("_"))

    for field in fields:
        spec = S.SCHEMA.get(field)
        chunk_entries = [r[field] for r in chunk_records if field in r]
        if spec is None:
            # Pass E 'contradictions' or unknown -> take union if list else first
            if all(isinstance(e, list) for e in chunk_entries):
                merged[field] = _merge_multivalue(chunk_entries)
            else:
                merged[field] = chunk_entries[0]
            continue
        if spec.multivalue:
            merged[field] = _merge_multivalue(chunk_entries)
        elif field in GRADE_ORDER:
            merged[field] = _merge_grade(field, chunk_entries)
        elif spec.type_ == S.TYPE_BOOL:
            merged[field] = _merge_bool(chunk_entries)
        else:
            merged[field] = _merge_scalar(chunk_entries)

    # meta
    meta = dict(chunk_records[0].get("_meta", {}))
    meta["n_chunks"] = len(chunk_records)
    meta["chunk_id"] = "merged"
    # propagate any rescue flags seen in any chunk
    if any(r.get("_meta", {}).get("_rescued") for r in chunk_records):
        meta["_rescued"] = True
    merged["_meta"] = meta
    return merged


# ----------------------------------------------------------------------------
# Per-pass merge driver
# ----------------------------------------------------------------------------
def cmd_merge_pass(pass_letter):
    by_paper = load_verdicts_by_paper(pass_letter)
    if not by_paper:
        print("No verdicts found for pass {} (file: {}).".format(
            pass_letter, VERDICT_FILE[pass_letter]))
        return 1
    MERGED_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    n_multi = 0
    for stable, recs in by_paper.items():
        merged = merge_chunks_for_paper(pass_letter, recs)
        out = MERGED_INPUTS_DIR / "{}.{}.json".format(stable, pass_letter)
        out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        n += 1
        if len(recs) > 1:
            n_multi += 1
    print("Pass {} merged: {} papers ({} multi-chunk) -> {}".format(
        pass_letter, n, n_multi, MERGED_INPUTS_DIR))
    return 0


# ----------------------------------------------------------------------------
# Consolidate A+B+C+D into one record per paper
# ----------------------------------------------------------------------------
def cmd_consolidate():
    if not MERGED_INPUTS_DIR.exists():
        print("ERROR: no merged inputs. Run --pass A/B/C/D first.", file=sys.stderr)
        return 2

    # gather all papers seen across A/B/C/D
    papers = set()
    for f in MERGED_INPUTS_DIR.glob("*.A.json"):
        papers.add(f.name[:-len(".A.json")])
    for f in MERGED_INPUTS_DIR.glob("*.B.json"):
        papers.add(f.name[:-len(".B.json")])

    n = 0
    with open(P.MERGED_EXTRACTIONS, "w", encoding="utf-8") as out:
        for stable in sorted(papers):
            record = {"stable_name": stable, "passes": {}}
            doi = ""
            for pl in ("A", "B", "C", "D"):
                f = MERGED_INPUTS_DIR / "{}.{}.json".format(stable, pl)
                if f.exists():
                    rec = json.loads(f.read_text(encoding="utf-8"))
                    record["passes"][pl] = rec
                    doi = doi or rec.get("_meta", {}).get("doi", "")
            # attach reconciliation (Pass E) if present
            fE = MERGED_INPUTS_DIR / "{}.E.json".format(stable)
            if fE.exists():
                record["passes"]["E"] = json.loads(fE.read_text(encoding="utf-8"))
            record["doi"] = doi
            out.write(json.dumps(record) + "\n")
            n += 1
    print("Consolidated {} papers -> {}".format(n, P.MERGED_EXTRACTIONS))
    return 0


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pass", dest="pass_letter",
                        choices=["A", "B", "C", "D", "E"], default=None)
    parser.add_argument("--consolidate", action="store_true")
    args = parser.parse_args()
    P.ensure_dirs()

    if args.consolidate:
        return cmd_consolidate()
    if args.pass_letter:
        return cmd_merge_pass(args.pass_letter)
    print("Specify --pass X or --consolidate.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
