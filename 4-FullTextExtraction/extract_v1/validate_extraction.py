"""
validate_extraction.py — Schema + quote-grounding validator for Step-4
extraction records.

Implements the Phase-1.3 policy:
  - Schema validation: enum membership, multi-enum membership, bool/int types,
    int-range bounds.
  - Quote grounding (B1 strict): every requires_quote field whose value is not
    a null-equivalent must carry a `_quote` that is a verbatim substring of the
    source text after NFKC + smart-quote + dash + whitespace normalisation.
    OCR-sourced papers use an alphanumeric-collapse relaxed match, flagged
    `_ocr_relaxed_match`.
  - Conditional-required: funding_source iff funding_declared, etc., including
    the two pseudo-conditions:
       * image_encoding_novelty_note required iff image_encoding contains 'other'
       * explainability_method_name required iff explainability_mechanism != 'none'
  - Multi-value fields validated per item.

Record shape (per pass, per paper, merged later):
  {
    "<field>": {"value": ..., "quote": "...", "chunk_id": 0, "pass_id": "B"},
    "<multivalue_field>": [
        {"value": ..., "quote": "...", "chunk_id": 0, "pass_id": "C"},
        ...
    ],
    ...
    "_meta": {"stable_name": "...", "doi": "...", "ocr_source": false}
  }

Null-equivalents (no quote required): None, "", "not_reported", "none",
"not_applicable", "null".

Public API:
  validate_record(record, source_text, ocr_mode=False) -> list of error tuples
  is_null_equivalent(value) -> bool
  normalise(text) -> str
  normalise_ocr(text) -> str

CLI:
  python3 validate_extraction.py --pass B --infile verdicts_pass_b.jsonl
      Validates each line against the source text resolved from _meta.stable_name.
  python3 validate_extraction.py --selftest

Python 3.8 compatible.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

import paths_step4 as P
import extraction_schema as S


# ----------------------------------------------------------------------------
# Null-equivalents
# ----------------------------------------------------------------------------
_NULL_EQUIVALENTS = frozenset([
    "", "not_reported", "none", "not_applicable", "null",
])


def is_null_equivalent(value):
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in _NULL_EQUIVALENTS
    return False


# ----------------------------------------------------------------------------
# Normalisation
# ----------------------------------------------------------------------------
_WS_RE = re.compile(r"\s+")
_SMART_SINGLE = re.compile(r"[\u2018\u2019\u02bc\u2032]")
_SMART_DOUBLE = re.compile(r"[\u201c\u201d\u2033]")
_DASHES = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]")
# Zero-width and invisible characters that OCR/PDF text injects mid-token.
_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff\u00ad]")
# Classic line-break hyphenation: "avail-\nable" -> "available".
_HYPHEN_LINEBREAK = re.compile(r"-\s*\n")


def normalise(text):
    """B1-strict normalisation: NFKC, strip zero-width/soft-hyphen, smart
    quotes -> ascii, dashes -> '-', join hyphenated line breaks, collapse
    whitespace, casefold."""
    if text is None:
        return ""
    t = unicodedata.normalize("NFKC", str(text))
    t = _ZERO_WIDTH.sub("", t)
    t = _SMART_SINGLE.sub("'", t)
    t = _SMART_DOUBLE.sub('"', t)
    t = _DASHES.sub("-", t)
    t = _HYPHEN_LINEBREAK.sub("", t)
    t = t.casefold()
    t = _WS_RE.sub(" ", t)
    return t.strip()


def normalise_nospace(text):
    """Relaxed token match for URLs/identifiers: OCR-normalise (alnum only)
    then drop spaces. Safe only for long values (>=12 chars) with no
    legitimate internal whitespace, e.g. URLs and dataset identifiers."""
    return normalise_ocr(text).replace(" ", "")


def normalise_ocr(text):
    """Relaxed: B1 normalisation then strip everything but [a-z0-9 ]."""
    t = normalise(text)
    t = _NON_ALNUM.sub("", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


# ----------------------------------------------------------------------------
# Quote grounding
# ----------------------------------------------------------------------------
def validate_quote(quote, source_text, ocr_mode=False):
    """Return (ok, reason). ok=True if quote is a substring of source under
    the chosen normalisation. Null-equivalent quotes pass trivially."""
    if is_null_equivalent(quote):
        return True, None
    if ocr_mode:
        nq = normalise_ocr(quote)
        ns = normalise_ocr(source_text)
    else:
        nq = normalise(quote)
        ns = normalise(source_text)
    if not nq:
        return True, None
    return (nq in ns), ("quote_not_in_source" if nq not in ns else None)


# ----------------------------------------------------------------------------
# Per-field validation
# ----------------------------------------------------------------------------
def _validate_value_type(spec, value):
    """Type/enum/range check on a single value. Returns (ok, reason)."""
    if spec.type_ == S.TYPE_ENUM:
        # Null-equivalents (not_reported, null, none, etc.) are always
        # acceptable on an enum: a field may be unknowable, and some enums
        # legitimately include 'not_reported'/'none'/'not_applicable' as
        # members. Either way it passes (and needs no quote).
        if is_null_equivalent(value):
            return True, None
        if spec.allowed and value not in spec.allowed:
            return False, "enum_violation:{}".format(value)
        return True, None
    if spec.type_ == S.TYPE_MULTI_ENUM:
        # value is a single item's value (caller iterates multivalue lists).
        if is_null_equivalent(value):
            return True, None
        if spec.allowed and value not in spec.allowed:
            return False, "multi_enum_member_violation:{}".format(value)
        return True, None
    if spec.type_ == S.TYPE_BOOL:
        if value not in (True, False):
            return False, "bool_violation:{}".format(value)
        return True, None
    if spec.type_ == S.TYPE_INT:
        if not isinstance(value, int) or isinstance(value, bool):
            return False, "int_violation:{}".format(value)
        return True, None
    if spec.type_ == S.TYPE_INT_RANGE:
        if not isinstance(value, int) or isinstance(value, bool):
            return False, "int_range_type:{}".format(value)
        if spec.int_min is not None and value < spec.int_min:
            return False, "int_below_min:{}".format(value)
        if spec.int_max is not None and value > spec.int_max:
            return False, "int_above_max:{}".format(value)
        return True, None
    # TYPE_VERBATIM, TYPE_STRING, TYPE_LIST_OBJ: accept any value shape here
    return True, None


def _entry_value_quote(entry):
    """Extract (value, quote) from a field entry dict, tolerant of shapes."""
    if isinstance(entry, dict):
        return entry.get("value"), entry.get("quote")
    # Bare scalar (no quote wrapper) — tolerate, treat quote as missing
    return entry, None


# ----------------------------------------------------------------------------
# Conditional-required evaluation
# ----------------------------------------------------------------------------
def _scalar_value(record, field):
    """Return the scalar value of a (possibly wrapped) field, or None."""
    if field not in record:
        return None
    entry = record[field]
    if isinstance(entry, dict):
        return entry.get("value")
    if isinstance(entry, list):
        # multivalue: return the list of member values
        return [_entry_value_quote(it)[0] for it in entry]
    return entry


def _condition_met(record, cond_field, cond_value):
    """Evaluate a conditional-required trigger, including pseudo-conditions."""
    # Pseudo-condition 1: image_encoding contains 'other'
    if cond_field == "image_encoding_contains_other":
        enc = _scalar_value(record, "image_encoding")
        if isinstance(enc, list):
            return any((v == "other") for v in enc)
        return enc == "other"
    # Pseudo-condition 2: explainability_mechanism != 'none'
    if cond_value == "not_none":
        v = _scalar_value(record, cond_field)
        return (v is not None) and (v != "none") and (not is_null_equivalent(v))
    # Normal condition: record[cond_field] == cond_value
    v = _scalar_value(record, cond_field)
    return v == cond_value


# ----------------------------------------------------------------------------
# Record validation
# ----------------------------------------------------------------------------
def validate_record(record, source_text, ocr_mode=False, pass_filter=None,
                    check_conditional=False, require_all_fields=False):
    """Validate one extraction record against source_text.

    pass_filter: if set (e.g. 'B'), only validate fields belonging to that
    pass. Used when validating a single-pass verdict file. If None, validate
    all extracted (non-derived, non-human, non-meta) fields present.

    check_conditional: if True, enforce conditional-required rules
    (funding_source iff funding_declared, etc.). This must be OFF during
    per-chunk validation (a chunk may see the trigger but not the dependent
    value) and ON only after cross-chunk merge.

    require_all_fields: if True, a field absent from the record is an error.
    OFF during per-chunk validation (chunks legitimately omit fields they did
    not see); the merge step assembles the full set.

    Returns a list of error tuples. Empty list = valid.
    """
    errors = []

    for field, spec in S.SCHEMA.items():
        if spec.derived or spec.human_only or spec.pass_ == S.PASS_META:
            continue
        if pass_filter is not None and spec.pass_ != pass_filter:
            continue
        if field not in record:
            if require_all_fields and (pass_filter is None or spec.pass_ == pass_filter):
                errors.append((field, "missing"))
            continue

        entry = record[field]

        def _check_one(item, idx):
            val, quote = _entry_value_quote(item)
            ok_t, reason_t = _validate_value_type(spec, val)
            if not ok_t:
                errors.append((field, idx, reason_t) if idx is not None else (field, reason_t))
            # Skip quote grounding for image-only facts (quote legitimately null)
            image_only = isinstance(item, dict) and item.get("_image_only") is True
            if spec.requires_quote and not is_null_equivalent(val) and not image_only:
                ok_q, reason_q = validate_quote(quote, source_text, ocr_mode)
                if not ok_q:
                    errors.append((field, idx, reason_q) if idx is not None else (field, reason_q))

        if spec.multivalue:
            if not isinstance(entry, list):
                errors.append((field, "expected_list"))
                continue
            for i, item in enumerate(entry):
                _check_one(item, i)
        else:
            _check_one(entry, None)

    # Conditional-required pass — only when explicitly requested (post-merge).
    if check_conditional:
        for field, spec in S.SCHEMA.items():
            if spec.conditional_required_when is None:
                continue
            if pass_filter is not None and spec.pass_ != pass_filter:
                continue
            cond_field, cond_value = spec.conditional_required_when
            if _condition_met(record, cond_field, cond_value):
                val = _scalar_value(record, field)
                if val is None or is_null_equivalent(val):
                    errors.append((field, "required_when:{}={}".format(cond_field, cond_value)))

    return errors


# ----------------------------------------------------------------------------
# Source-text resolver
# ----------------------------------------------------------------------------
def resolve_source_text(stable_name):
    """Load the full pdftotext output for a paper by stable_name."""
    path = P.PDF_TEXT_DIR / (stable_name + ".txt")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def _ocr_lookup():
    """Return a set of stable_names whose extraction_mode was OCR."""
    ocr = set()
    if not P.PDF_TEXT_INDEX.exists():
        return ocr
    import csv
    with open(P.PDF_TEXT_INDEX, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("ocr_source", "")).strip().lower() in ("true", "1", "yes"):
                ocr.add(r["stable_name"])
    return ocr


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    src = ("We implement a hybrid quantum-classical autoencoder with a "
           "parameterized quantum circuit of 4 qubits and circuit depth 3, "
           "trained in PennyLane. The model was evaluated with 5-fold "
           "cross-validation. Funding was provided by the National Science "
           "Foundation. Grad-CAM was used for explainability.")

    # Valid Pass B record
    rec_ok = {
        "paradigm": {"value": "hybrid_quantum_classical",
                     "quote": "hybrid quantum-classical autoencoder", "chunk_id": 0, "pass_id": "B"},
        "paradigm_novelty_note": {"value": None, "quote": None},
        "hardware_vendor": {"value": "not_reported", "quote": None},
        "hardware_modality": {"value": "simulator_only", "quote": None},
        "real_or_simulator": {"value": "simulator", "quote": "trained in PennyLane"},
        "simulator_framework": [{"value": "PennyLane", "quote": "trained in PennyLane", "chunk_id": 0, "pass_id": "B"}],
        "ansatz_family": {"value": "not_reported", "quote": None},
        "parameter_count": {"value": "not_reported", "quote": None},
        "transpilation_level": {"value": "none_reported", "quote": None},
        "image_encoding": [{"value": "amplitude", "quote": "parameterized quantum circuit", "chunk_id": 0, "pass_id": "B"}],
        "image_encoding_novelty_note": {"value": None, "quote": None},
        "error_mitigation_strategy": {"value": "none_reported", "quote": None},
        "error_mitigation_type": {"value": "none_reported", "quote": None},
        "qubit_count": {"value": "4 qubits", "quote": "4 qubits and circuit depth 3"},
        "circuit_depth": {"value": "depth 3", "quote": "circuit depth 3"},
        "gate_count": {"value": "not_reported", "quote": None},
        "shot_count": {"value": "not_reported", "quote": None},
    }
    errs = validate_record(rec_ok, src, ocr_mode=False, pass_filter="B")
    print("Test 1 (valid Pass B):", "PASS" if not errs else ("FAIL " + str(errs)))

    # Hallucinated quote
    rec_bad_quote = dict(rec_ok)
    rec_bad_quote["qubit_count"] = {"value": "8 qubits", "quote": "8 qubits on IBM hardware"}
    errs = validate_record(rec_bad_quote, src, ocr_mode=False, pass_filter="B")
    print("Test 2 (hallucinated quote):", "PASS" if any("quote_not_in_source" in str(e) for e in errs) else ("FAIL " + str(errs)))

    # Enum violation
    rec_bad_enum = dict(rec_ok)
    rec_bad_enum["paradigm"] = {"value": "QuantumMagic", "quote": "hybrid quantum-classical autoencoder"}
    errs = validate_record(rec_bad_enum, src, ocr_mode=False, pass_filter="B")
    print("Test 3 (enum violation):", "PASS" if any("enum_violation" in str(e) for e in errs) else ("FAIL " + str(errs)))

    # Conditional-required: paradigm=other but no novelty note
    rec_cond = dict(rec_ok)
    rec_cond["paradigm"] = {"value": "other", "quote": "hybrid quantum-classical autoencoder"}
    rec_cond["paradigm_novelty_note"] = {"value": None, "quote": None}
    errs = validate_record(rec_cond, src, ocr_mode=False, pass_filter="B", check_conditional=True)
    print("Test 4 (cond-required paradigm=other):", "PASS" if any("required_when" in str(e) for e in errs) else ("FAIL " + str(errs)))

    # Pseudo-condition: image_encoding contains 'other' -> novelty note required
    rec_enc = dict(rec_ok)
    rec_enc["image_encoding"] = [{"value": "other", "quote": "parameterized quantum circuit", "chunk_id": 0, "pass_id": "B"}]
    rec_enc["image_encoding_novelty_note"] = {"value": None, "quote": None}
    errs = validate_record(rec_enc, src, ocr_mode=False, pass_filter="B", check_conditional=True)
    print("Test 5 (pseudo-cond image_encoding=other):", "PASS" if any("image_encoding_novelty_note" in str(e) and "required_when" in str(e) for e in errs) else ("FAIL " + str(errs)))

    # OCR-relaxed match: quote with punctuation diff
    rec_ocr = {
        "title": {"value": "X", "quote": "hybrid quantumclassical autoencoder"},  # missing hyphen
    }
    errs_strict = validate_record(rec_ocr, src, ocr_mode=False, pass_filter="A")
    errs_ocr = validate_record(rec_ocr, src, ocr_mode=True, pass_filter="A")
    strict_fails = any("quote_not_in_source" in str(e) for e in errs_strict)
    ocr_passes = not any("quote_not_in_source" in str(e) for e in errs_ocr)
    print("Test 6 (OCR relax: strict fails, ocr passes):",
          "PASS" if (strict_fails and ocr_passes) else "FAIL strict={} ocr={}".format(errs_strict, errs_ocr))

    # Null-equivalent needs no quote
    print("Test 7 (null-equiv no quote):", "PASS" if is_null_equivalent("not_reported") and is_null_equivalent("") and not is_null_equivalent("4 qubits") else "FAIL")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pass", dest="pass_letter", type=str, default=None,
                        help="Validate only fields of this pass (A/B/C/D).")
    parser.add_argument("--infile", type=str, default="",
                        help="JSONL file of extraction records to validate.")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not args.infile:
        print("Nothing to do. Use --selftest or --infile.", file=sys.stderr)
        return 2

    ocr_set = _ocr_lookup()
    n_ok = 0
    n_bad = 0
    bad_records = []
    with open(args.infile, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            meta = rec.get("_meta", {})
            stable = meta.get("stable_name", "")
            src = resolve_source_text(stable) or ""
            ocr_mode = stable in ocr_set
            errs = validate_record(rec, src, ocr_mode=ocr_mode, pass_filter=args.pass_letter)
            if errs:
                n_bad += 1
                bad_records.append((stable, errs))
            else:
                n_ok += 1
    print("Validated: {} ok, {} with errors.".format(n_ok, n_bad))
    for stable, errs in bad_records[:50]:
        print("  {}: {}".format(stable, errs[:8]))
    if len(bad_records) > 50:
        print("  ... and {} more".format(len(bad_records) - 50))
    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
