"""
rescue_extraction.py — Relaxed-match rescue pass for quote-grounding
near-misses, run before a record is quarantined.

Mirrors the Stage-3.5 rescue_3_5.py pattern. When a subagent's quote fails
strict B1 substring matching, the failure is most often cosmetic:
  - smart quotes / apostrophe variants the normaliser already handles, but
    also ligatures, non-breaking spaces, soft hyphens
  - a trailing period or parenthesis the model appended
  - hyphenation across a line break in the source ("quantum-\nclassical")
    that the model rendered as "quantum-classical" or "quantumclassical"
  - whitespace runs / tab vs space

Rescue strategy, applied per failing quote in escalating order:
  R1. Hyphenation repair on the SOURCE: collapse "word-\nword" -> "wordword"
      and also -> "word-word", then retry strict match against both repaired
      sources.
  R2. Punctuation-insensitive match: strip ASCII punctuation from both quote
      and source (keep alphanumerics + spaces), retry.
  R3. Alphanumeric-collapse (same as OCR-relaxed): strip everything but
      [a-z0-9], retry. Most aggressive.

A quote rescued at R1 is flagged `_quote_rescued` (clean). A quote rescued at
R2/R3 is flagged `_quote_rescued_relaxed` (looser; surfaced for Step-5
spot-check). A quote that survives none is left failing; the record stays
quarantined with `_quote_unverified` for the specific field, and the field is
surfaced for human review rather than silently dropped.

This module does NOT call any LLM. It only re-tests existing quotes against
the source under progressively looser equivalence.

Public API:
  rescue_record(record, source_text) -> (rescued_record, rescue_report)
      rescue_report: list of {field, index, level} entries describing what was
      rescued and at which level.

CLI:
  python3 rescue_extraction.py --infile quarantine_step4.jsonl \
        --outfile rescued_step4.jsonl
  python3 rescue_extraction.py --selftest

Python 3.8 compatible.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import paths_step4 as P
import extraction_schema as S
from validate_extraction import (
    normalise, normalise_ocr, is_null_equivalent, validate_quote,
    resolve_source_text, _entry_value_quote,
)


# ----------------------------------------------------------------------------
# Source repairs
# ----------------------------------------------------------------------------
_HYPHEN_LINEBREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_ASCII_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


def repair_hyphenation(source_text):
    """Return two repaired variants of the source:
       (a) collapse 'word-\\nword' -> 'wordword'
       (b) collapse 'word-\\nword' -> 'word-word'
    """
    collapsed = _HYPHEN_LINEBREAK.sub(r"\1\2", source_text)
    joined = _HYPHEN_LINEBREAK.sub(r"\1-\2", source_text)
    return collapsed, joined


def strip_punct(text):
    t = normalise(text)
    t = _ASCII_PUNCT.sub("", t)
    t = _WS.sub(" ", t)
    return t.strip()


# ----------------------------------------------------------------------------
# Single-quote rescue
# ----------------------------------------------------------------------------
def rescue_quote(quote, source_text):
    """Try to rescue a failing quote. Returns (rescued_bool, level) where
    level in {None, 'R1', 'R2', 'R3'}.

    Assumes the quote already FAILED strict matching (caller checks).
    """
    if is_null_equivalent(quote):
        return True, None  # nothing to rescue

    # R1: hyphenation repair on source
    src_collapsed, src_joined = repair_hyphenation(source_text)
    nq = normalise(quote)
    if nq and (nq in normalise(src_collapsed) or nq in normalise(src_joined)):
        return True, "R1"

    # R2: punctuation-insensitive
    pq = strip_punct(quote)
    if pq and pq in strip_punct(source_text):
        return True, "R2"
    # also try R2 against hyphen-repaired sources
    if pq and (pq in strip_punct(src_collapsed) or pq in strip_punct(src_joined)):
        return True, "R2"

    # R3: alphanumeric-collapse
    aq = normalise_ocr(quote)
    if aq and (aq in normalise_ocr(source_text)
               or aq in normalise_ocr(src_collapsed)
               or aq in normalise_ocr(src_joined)):
        return True, "R3"

    return False, None


# ----------------------------------------------------------------------------
# Record rescue
# ----------------------------------------------------------------------------
def _set_flag(entry, flag):
    """Attach a boolean flag to a field entry dict (in place)."""
    if isinstance(entry, dict):
        entry[flag] = True


def rescue_record(record, source_text):
    """Re-test every requires_quote field's quote that fails strict matching,
    attempting rescue. Mutates a copy of the record and returns
    (rescued_record, rescue_report).

    Fields that cannot be rescued are flagged `_quote_unverified` on the entry.
    """
    rescued = json.loads(json.dumps(record))  # deep copy
    report = []

    for field, spec in S.SCHEMA.items():
        if spec.derived or spec.human_only or spec.pass_ == S.PASS_META:
            continue
        if not spec.requires_quote:
            continue
        if field not in rescued:
            continue

        entry = rescued[field]

        def _process(item, idx):
            val, quote = _entry_value_quote(item)
            if is_null_equivalent(val):
                return
            ok, _ = validate_quote(quote, source_text, ocr_mode=False)
            if ok:
                return  # already valid under strict
            r_ok, level = rescue_quote(quote, source_text)
            if r_ok and level == "R1":
                _set_flag(item, "_quote_rescued")
                report.append({"field": field, "index": idx, "level": "R1"})
            elif r_ok and level in ("R2", "R3"):
                _set_flag(item, "_quote_rescued_relaxed")
                report.append({"field": field, "index": idx, "level": level})
            else:
                _set_flag(item, "_quote_unverified")
                report.append({"field": field, "index": idx, "level": "unverified"})

        if spec.multivalue and isinstance(entry, list):
            for i, item in enumerate(entry):
                _process(item, i)
        else:
            _process(entry, None)

    return rescued, report


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    # Source with a hyphenated line break
    source = ("We propose a hybrid quantum-\nclassical model. "
              "The circuit uses 4 qubits (with depth 3). "
              "Trained in PennyLane and evaluated by 5-fold cross-validation.")

    # R1: quote rendered without the line break
    q_r1 = "hybrid quantum-classical model"
    ok_strict, _ = validate_quote(q_r1, source, ocr_mode=False)
    r_ok, lvl = rescue_quote(q_r1, source)
    print("Test R1 (hyphen linebreak): strict={} rescued={} level={}".format(
        ok_strict, r_ok, lvl), "PASS" if (not ok_strict and r_ok and lvl == "R1") else "FAIL")

    # R2: quote with an appended period the source lacks at that spot
    q_r2 = "4 qubits (with depth 3)!!!"
    ok_strict, _ = validate_quote(q_r2, source, ocr_mode=False)
    r_ok, lvl = rescue_quote(q_r2, source)
    print("Test R2 (punctuation): strict={} rescued={} level={}".format(
        ok_strict, r_ok, lvl), "PASS" if (not ok_strict and r_ok and lvl in ("R2", "R3")) else "FAIL")

    # R3: alphanumeric-only survivor
    q_r3 = "quantum / classical / model"
    r_ok, lvl = rescue_quote(q_r3, source)
    print("Test R3 (alnum collapse): rescued={} level={}".format(r_ok, lvl),
          "PASS" if r_ok else "FAIL")

    # Unrescuable: genuinely absent text
    q_bad = "this phrase never appears in the source at all"
    r_ok, lvl = rescue_quote(q_bad, source)
    print("Test unrescuable: rescued={} level={}".format(r_ok, lvl),
          "PASS" if not r_ok else "FAIL")

    # Record-level
    rec = {
        "title": {"value": "T", "quote": "hybrid quantum-classical model"},  # R1
        "qubit_count": {"value": "4 qubits", "quote": "4 qubits (with depth 3)!!!"},  # R2
        "circuit_depth": {"value": "depth 3", "quote": "this phrase never appears"},  # unverified
    }
    rescued, report = rescue_record(rec, source)
    levels = {r["field"]: r["level"] for r in report}
    print("Test record-level:", "PASS" if (
        levels.get("title") == "R1"
        and levels.get("qubit_count") in ("R2", "R3")
        and levels.get("circuit_depth") == "unverified"
        and rescued["circuit_depth"].get("_quote_unverified") is True
    ) else "FAIL " + str(report))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--infile", type=str, default="")
    parser.add_argument("--outfile", type=str, default="")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not args.infile or not args.outfile:
        print("Need --infile and --outfile (or --selftest).", file=sys.stderr)
        return 2

    n = 0
    n_r1 = n_relaxed = n_unverified = 0
    with open(args.infile, encoding="utf-8") as fin, \
         open(args.outfile, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            stable = rec.get("_meta", {}).get("stable_name", "")
            src = resolve_source_text(stable) or ""
            rescued, report = rescue_record(rec, src)
            for r in report:
                if r["level"] == "R1":
                    n_r1 += 1
                elif r["level"] in ("R2", "R3"):
                    n_relaxed += 1
                else:
                    n_unverified += 1
            fout.write(json.dumps(rescued) + "\n")
            n += 1
    print("Rescue pass over {} record(s):".format(n))
    print("  R1 clean rescues     :", n_r1)
    print("  R2/R3 relaxed rescues:", n_relaxed)
    print("  unverified (flagged) :", n_unverified)
    print("Output:", args.outfile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
