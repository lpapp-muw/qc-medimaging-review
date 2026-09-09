# -*- coding: utf-8 -*-
# Phase 0 - supplemental-enrichment target manifest (deterministic, no LLM).
# Reads the AI extraction workbook + the included-paper list, restricts to the
# included synthesis set, classifies every column, and emits, per (paper, field),
# a target cell with its current state and the verdicts the enrichment subagent
# is allowed to return. No PDF is read; raw extraction values are never mutated.
#
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
#
# Usage:
#   python phase0_build_targets.py <extractions_ai.xlsx> <included_step5_v134.csv> <out_dir>

import sys
import os
import csv
import json

from openpyxl import load_workbook
from openpyxl.utils import range_boundaries

BOOL_FIELDS = set([
    "funding_declared", "sample_size_justification_reported",
    "external_validation_used", "regulatory_pathway_addressed",
    "prospective_clinical_present", "classical_baseline_present",
    "external_test_set", "statistical_testing_present",
    "code_release", "data_release", "weights_release",
])

LIST_FIELDS = set([
    "anatomy", "dataset_type", "dataset_name", "simulator_framework",
    "image_encoding", "performance_metrics_quantum",
    "performance_metrics_classical", "reporting_standard_adherence",
])

# Fixed bibliographic identity: not enrichable from a supplemental.
EXCLUDE_BIBLIO = set(["title", "authors", "journal", "publisher"])

ABSENCE_TOKENS = set([
    "", "not_reported", "none", "none_reported", "not_applicable",
    "null", "n/a", "na", "-",
])

HIGH_PRIORITY = set([
    "code_release", "code_url", "data_release", "data_identifier",
    "weights_release", "performance_metrics_quantum",
    "performance_metrics_classical", "qubit_count", "circuit_depth",
    "gate_count", "shot_count", "parameter_count", "dataset_name",
    "dataset_size_train", "dataset_size_val", "dataset_size_test",
    "dataset_type", "baseline_rigour_grade", "dataset_realism_grade",
    "statistical_testing_present", "statistical_testing_method",
    "cross_validation_strategy", "external_test_set",
    "classical_baseline_present", "classical_baseline_identification",
    "error_mitigation_strategy", "error_mitigation_type", "hardware_vendor",
    "simulator_framework", "ansatz_family", "transpilation_level",
    "image_encoding", "explainability_mechanism", "explainability_method_name",
])

LOW_PRIORITY = set(["country_corresponding", "funding_declared", "funding_source"])

LIST_SEP = ";"


def norm(v):
    if v is None:
        return ""
    return str(v).strip()


def priority_of(field):
    if field in HIGH_PRIORITY:
        return "high"
    if field in LOW_PRIORITY:
        return "low"
    return "medium"


def arity_of(field):
    if field in BOOL_FIELDS:
        return "bool"
    if field in LIST_FIELDS:
        return "list"
    return "scalar"


def state_and_verdicts(field, raw):
    """Return (state, allowed_verdicts) from the current cell value."""
    v = norm(raw)
    low = v.lower()
    if field in BOOL_FIELDS:
        if low in ("true", "1", "yes"):
            return "BOOL_TRUE", ["confirms", "contradicts", "not_found"]
        if low in ("false", "0", "no"):
            return "BOOL_FALSE", ["confirms", "contradicts", "not_found"]
        # boolean unexpectedly empty -> treat as absence
        return "ABSENCE", ["fills_blank", "confirms_absence", "not_found"]
    if field in LIST_FIELDS:
        if low in ABSENCE_TOKENS or v == "[]":
            return "ABSENCE", ["fills_blank", "confirms_absence", "not_found"]
        return "REAL_LIST", ["appends_extra", "confirms", "contradicts", "not_found"]
    # scalar (verbatim or enum)
    if low in ABSENCE_TOKENS:
        return "ABSENCE", ["fills_blank", "confirms_absence", "not_found"]
    return "REAL_SCALAR", ["confirms", "contradicts", "not_found"]


def parse_unverified(cell):
    """Parse an audit_flags.quote_unverified cell into a set of field names.
    Tokens look like 'C.performance_metrics_quantum' possibly joined by ';' or ','."""
    out = set()
    s = norm(cell)
    if not s:
        return out
    for tok in s.replace(",", ";").split(";"):
        tok = tok.strip()
        if not tok:
            continue
        if "." in tok:
            tok = tok.split(".", 1)[1]
        out.add(tok.strip())
    return out


def main():
    if len(sys.argv) != 4:
        sys.stderr.write("usage: phase0_build_targets.py <xlsx> <included_csv> <out_dir>\n")
        sys.exit(2)
    xlsx, inc_csv, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    # included set + doi
    inc = {}
    with open(inc_csv, "r") as fh:
        rd = csv.DictReader(fh)
        for row in rd:
            sn = norm(row.get("stable_name"))
            if sn:
                inc[sn] = norm(row.get("doi")).lower()
    if not inc:
        raise SystemExit("no included stable_names parsed")

    wb = load_workbook(xlsx, data_only=False)
    ws = wb["extractions"]
    ncol = ws.max_column
    names = [norm(ws.cell(row=2, column=c).value) for c in range(1, ncol + 1)]  # 0-based list

    # band map from row-1 merges
    band = {}
    for m in ws.merged_cells.ranges:
        c1, r1, c2, r2 = range_boundaries(str(m))
        if r1 == 1:
            lbl = norm(ws.cell(row=1, column=c1).value)
            for c in range(c1, c2 + 1):
                band[c] = lbl

    def role_of(col_idx_1based, name):
        b = band.get(col_idx_1based, "")
        if b == "identifiers":
            return "identifier"
        if b == "Human assessment":
            return "human_assessment"
        if b.startswith("Derived"):
            return "derived"
        if name.endswith("_quote"):
            return "quote_companion"
        if name in EXCLUDE_BIBLIO:
            return "identity_biblio"
        return "targetable"

    # column classification + targetable list with quote-companion mapping
    classification = []
    targetable = []  # (col_idx_1based, name)
    for i in range(ncol):
        col1 = i + 1
        name = names[i]
        role = role_of(col1, name)
        ar = arity_of(name) if role == "targetable" else ""
        pr = priority_of(name) if role == "targetable" else ""
        classification.append({
            "col_index": col1, "band": band.get(col1, ""), "name": name,
            "role": role, "arity": ar, "priority": pr,
        })
        if role == "targetable":
            targetable.append((col1, name))

    # quote-companion index for each targetable value col (col+1 if it is name_quote)
    quote_idx = {}
    for col1, name in targetable:
        if col1 < ncol and names[col1] == name + "_quote":  # names[col1] is the 0-based (col1+1)th cell
            quote_idx[col1] = col1 + 1

    # locate doi/stable_name columns and audit_flags
    doi_col = names.index("doi") + 1
    sn_col = names.index("stable_name") + 1

    af = wb["audit_flags"]
    af_names = [norm(af.cell(row=1, column=c).value) for c in range(1, af.max_column + 1)]
    af_sn = af_names.index("stable_name") + 1
    af_qi = af_names.index("quote_unverified") + 1
    unverified = {}  # stable_name -> set(field)
    for r in range(2, af.max_row + 1):
        sn = norm(af.cell(row=r, column=af_sn).value)
        if sn in inc:
            fs = parse_unverified(af.cell(row=r, column=af_qi).value)
            if fs:
                unverified[sn] = fs

    # iterate included rows, emit targets + coverage
    cov = {}  # field -> counter dict
    for _, name in targetable:
        cov[name] = {"ABSENCE": 0, "REAL_SCALAR": 0, "REAL_LIST": 0,
                     "BOOL_TRUE": 0, "BOOL_FALSE": 0, "quote_unverified": 0}

    targets_path = os.path.join(out_dir, "targets.jsonl")
    per_paper = {}
    n_targets = 0
    with open(targets_path, "w") as out:
        for r in range(3, ws.max_row + 1):
            sn = norm(ws.cell(row=r, column=sn_col).value)
            if sn not in inc:
                continue
            doi = norm(ws.cell(row=r, column=doi_col).value).lower() or inc[sn]
            uset = unverified.get(sn, set())
            for col1, name in targetable:
                raw = ws.cell(row=r, column=col1).value
                qv = ""
                if col1 in quote_idx:
                    qv = norm(ws.cell(row=r, column=quote_idx[col1]).value)
                state, allowed = state_and_verdicts(name, raw)
                is_unv = name in uset
                cov[name][state] += 1
                if is_unv:
                    cov[name]["quote_unverified"] += 1
                reason = []
                if state == "ABSENCE":
                    reason.append("blank")
                elif state == "REAL_LIST":
                    reason.append("ambiguity_or_append")
                else:
                    reason.append("ambiguity")
                if is_unv:
                    reason.append("quote_unverified")
                rec = {
                    "stable_name": sn,
                    "doi": doi,
                    "field": name,
                    "arity": arity_of(name),
                    "state": state,
                    "current_value": norm(raw),
                    "current_quote": qv,
                    "quote_unverified": is_unv,
                    "priority": priority_of(name),
                    "reason": reason,
                    "allowed_verdicts": allowed,
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_targets += 1
                per_paper[sn] = per_paper.get(sn, 0) + 1

    # column classification csv
    cls_path = os.path.join(out_dir, "phase0_column_classification.csv")
    with open(cls_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["col_index", "band", "name", "role", "arity", "priority"])
        w.writeheader()
        for row in classification:
            w.writerow(row)

    # coverage baseline csv (the "before" map)
    cov_path = os.path.join(out_dir, "phase0_coverage_baseline.csv")
    n_papers = len(inc)
    with open(cov_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "arity", "priority", "n_papers", "n_filled_real",
                    "n_absence", "n_bool_true", "n_bool_false",
                    "n_quote_unverified", "pct_absence"])
        for _, name in targetable:
            c = cov[name]
            real = c["REAL_SCALAR"] + c["REAL_LIST"]
            absent = c["ABSENCE"]
            pct = round(100.0 * absent / n_papers, 1) if n_papers else 0.0
            w.writerow([name, arity_of(name), priority_of(name), n_papers, real,
                        absent, c["BOOL_TRUE"], c["BOOL_FALSE"],
                        c["quote_unverified"], pct])

    # summary to stdout
    roles = {}
    for row in classification:
        roles[row["role"]] = roles.get(row["role"], 0) + 1
    print("included papers: {}".format(n_papers))
    print("column roles: {}".format(roles))
    print("targetable fields: {}".format(len(targetable)))
    print("target cells emitted: {}".format(n_targets))
    print("targets per paper: min={} max={}".format(min(per_paper.values()), max(per_paper.values())))
    # aggregate state tally
    agg = {"ABSENCE": 0, "REAL_SCALAR": 0, "REAL_LIST": 0, "BOOL_TRUE": 0, "BOOL_FALSE": 0, "quote_unverified": 0}
    for name in cov:
        for k in agg:
            agg[k] += cov[name][k]
    print("state tally (all target cells): {}".format(agg))
    # top-10 absence fields
    rows = sorted(((cov[n]["ABSENCE"], n) for _, n in targetable), reverse=True)
    print("top absence (blank) fields:")
    for cnt, n in rows[:12]:
        print("  {:4d}/{}  {}".format(cnt, n_papers, n))
    print("wrote: {} | {} | {}".format(targets_path, cls_path, cov_path))


if __name__ == "__main__":
    main()
