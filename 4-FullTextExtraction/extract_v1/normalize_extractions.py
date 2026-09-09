"""
normalize_extractions.py — Post-merge text cleanup on extractions_merged.jsonl.

Two specific cleanups observed in the smoke run:

1. Hyphenation-space artifacts in verbatim text fields (titles, quotes,
   journal names, etc.): pdftotext renders `dual-\\npositron` as `dual- positron`
   (hyphen + space). Collapse `\\w-\\s+\\w` -> `\\w-\\w` when the surrounding
   characters are letters.

2. Mid-word quote boundaries: a `quote` value that starts or ends mid-word
   (e.g. "tivity of a novel PET system" from "sensitivity") is snapped to the
   nearest preceding/following word boundary in the source text. This is
   cosmetic only — the value is unaffected, and substring grounding still
   works (the snapped quote remains a verbatim substring of the source).

The script is idempotent and pure. It reads extractions_merged.jsonl, applies
normalization to the consolidated record (NOT to the raw verdict files), and
writes a new extractions_merged.jsonl (in-place after backup). Raw subagent
verdicts in verdicts_pass_*.jsonl are NEVER altered.

Place in the pipeline BETWEEN `merge_passes.py --consolidate` AND
`compute_derived.py`:

    python3 merge_passes.py --consolidate
    python3 normalize_extractions.py        # <-- here
    python3 compute_derived.py
    python3 extractions_to_xlsx.py

CLI:
  python3 normalize_extractions.py            # cleanup extractions_merged.jsonl
  python3 normalize_extractions.py --selftest # run unit tests

Python 3.8 compatible.
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import paths_step4 as P
from validate_extraction import resolve_source_text, normalise


# ----------------------------------------------------------------------------
# Cleanup 1: hyphenation-space collapse
# ----------------------------------------------------------------------------
# Matches: letter, hyphen, one-or-more whitespace, letter -> letter-letter
_HYPH_SPACE = re.compile(r"([A-Za-z])-\s+([A-Za-z])")


def collapse_hyphen_space(text):
    if text is None:
        return text
    if not isinstance(text, str):
        return text
    prev = None
    cur = text
    # Iterate because a single pass may leave nested cases
    while cur != prev:
        prev = cur
        cur = _HYPH_SPACE.sub(r"\1-\2", cur)
    return cur


# ----------------------------------------------------------------------------
# Cleanup 2: mid-word quote boundary snapping
# ----------------------------------------------------------------------------
def snap_quote_to_word_boundaries(quote, source):
    """Find `quote` (under normalised matching) in `source`; if its
    span begins or ends mid-word, extend to the nearest word boundary
    OUTWARD (so the snapped quote remains a substring covering the original).

    Returns the snapped quote string, or the original if no change.
    """
    if not quote or not isinstance(quote, str) or not source:
        return quote

    # Find the quote's span in the source by normalised matching, then map
    # back to the raw source bytes. Cheapest correct approach: find the raw
    # quote literally first; if not found, return original (matches via
    # validate.normalise — already valid for grounding).
    idx = source.find(quote)
    if idx < 0:
        # Try case-insensitive simple match
        low_src = source.lower()
        idx = low_src.find(quote.lower())
        if idx < 0:
            return quote  # can't locate -> leave alone

    end = idx + len(quote)

    # Snap start: walk left while preceding char is a word char
    start = idx
    while start > 0 and source[start - 1].isalnum():
        start -= 1

    # Snap end: walk right while next char is a word char
    while end < len(source) and source[end].isalnum():
        end += 1

    snapped = source[start:end]
    # If snapping changed the boundaries, return snapped; else original.
    if (start, end) == (idx, idx + len(quote)):
        return quote
    return snapped


# ----------------------------------------------------------------------------
# Apply to one record entry (scalar or list)
# ----------------------------------------------------------------------------
def normalize_entry(entry, source_text):
    """Apply cleanups in-place on a field entry. Returns the entry."""
    if entry is None:
        return entry
    if isinstance(entry, list):
        for item in entry:
            normalize_entry(item, source_text)
        return entry
    if not isinstance(entry, dict):
        return entry

    # value field
    v = entry.get("value")
    if isinstance(v, str):
        entry["value"] = collapse_hyphen_space(v)

    # quote field
    q = entry.get("quote")
    if isinstance(q, str):
        # Clean hyphenation first
        q2 = collapse_hyphen_space(q)
        # Then snap word boundaries against the source (use the cleaned source)
        if source_text:
            cleaned_src = collapse_hyphen_space(source_text)
            q2 = snap_quote_to_word_boundaries(q2, cleaned_src)
        entry["quote"] = q2

    return entry


# ----------------------------------------------------------------------------
# Record-level
# ----------------------------------------------------------------------------
def normalize_record(record):
    """Walk the consolidated record's passes and clean text fields in-place."""
    stable = record.get("stable_name") or record.get("_meta", {}).get("stable_name")
    source_text = resolve_source_text(stable) if stable else ""

    for pl in ("A", "B", "C", "D"):
        prec = record.get("passes", {}).get(pl)
        if not isinstance(prec, dict):
            continue
        for field, entry in prec.items():
            if field.startswith("_"):
                continue
            normalize_entry(entry, source_text)

    return record


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    # Hyphen-space
    assert collapse_hyphen_space("imaging dual- positron and prompt") == "imaging dual-positron and prompt"
    assert collapse_hyphen_space("end-to-end") == "end-to-end"   # untouched
    assert collapse_hyphen_space("multi-\nlevel") == "multi-level"
    assert collapse_hyphen_space(None) is None
    print("Test 1 (hyphen-space): PASS")

    # Word-boundary snap
    src = "We measured the sensitivity of a novel PET system at 11.29%."
    snapped = snap_quote_to_word_boundaries("tivity of a novel PET system", src)
    assert snapped == "sensitivity of a novel PET system", "got: {}".format(snapped)
    print("Test 2 (snap leading): PASS  ->", repr(snapped))

    src2 = "achieved high accuracy on the benchmark."
    snapped2 = snap_quote_to_word_boundaries("high accur", src2)
    assert snapped2 == "high accuracy", "got: {}".format(snapped2)
    print("Test 3 (snap trailing): PASS  ->", repr(snapped2))

    # No change when already on word boundaries
    src3 = "These two groups account for 6.81% of detected coincidences."
    snapped3 = snap_quote_to_word_boundaries("6.81% of detected", src3)
    # The "6.81%" start is at a word boundary; "detected" is also on a boundary.
    # No outward snap needed.
    assert snapped3 == "6.81% of detected", "got: {}".format(snapped3)
    print("Test 4 (no change needed): PASS")

    # Entry-level: hyphen-space in value AND quote
    entry = {"value": "Quantum imaging dual- positron study",
             "quote": "tivity of a novel PET system at 11.29%"}
    src_e = "sensitivity of a novel PET system at 11.29%"
    normalize_entry(entry, src_e)
    assert entry["value"] == "Quantum imaging dual-positron study", entry["value"]
    assert entry["quote"] == "sensitivity of a novel PET system at 11.29%", entry["quote"]
    print("Test 5 (entry-level): PASS")

    # List entry
    lst = [
        {"value": "Multi- modal", "quote": "Multi- modal imaging"},
        {"value": "fine", "quote": "fine"},
    ]
    normalize_entry(lst, "Multi-modal imaging is fine")
    assert lst[0]["value"] == "Multi-modal"
    assert lst[0]["quote"] == "Multi-modal imaging"
    print("Test 6 (list): PASS")

    print("SELFTEST: ALL PASS")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not P.MERGED_EXTRACTIONS.exists():
        print("ERROR: {} missing. Run merge_passes.py --consolidate first.".format(
            P.MERGED_EXTRACTIONS), file=sys.stderr)
        return 2

    # Back up the file before mutating
    bak = P.MERGED_EXTRACTIONS.with_suffix(P.MERGED_EXTRACTIONS.suffix + ".pre_normalize.bak")
    shutil.copy2(P.MERGED_EXTRACTIONS, bak)

    n = 0
    n_changes = 0
    out_path = P.MERGED_EXTRACTIONS
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    with open(bak, encoding="utf-8") as fin, open(tmp_path, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            before = json.dumps(rec, sort_keys=True)
            normalize_record(rec)
            after = json.dumps(rec, sort_keys=True)
            if before != after:
                n_changes += 1
            fout.write(json.dumps(rec) + "\n")
            n += 1
    tmp_path.replace(out_path)
    print("Normalized {} records ({} changed) -> {}".format(n, n_changes, out_path))
    print("Backup retained at:", bak)
    return 0


if __name__ == "__main__":
    sys.exit(main())
