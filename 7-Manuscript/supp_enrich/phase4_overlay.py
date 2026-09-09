# -*- coding: utf-8 -*-
# Phase 4.5 overlay - apply accepted enrichment verdicts to the extraction table.
#
# Joins each paper's verify decisions (out/verify_tasks/<sn>.verify.json) to its
# adjudication records (out/verify_tasks/<sn>.task.json) positionally, and applies
# ONLY decision=="accept":
#   fills_blank   -> write si_value to the field cell IFF it is currently blank.
#   appends_extra -> append si_value to the list field cell (dedup).
#   contradicts   -> ANNOTATE ONLY: extraction value untouched; disagreement
#                    recorded as a cell comment + provenance row.
#   confirms      -> no-op, logged.
# Every applied cell is colour-filled and commented; every action is logged to an
# 'enrichment_provenance' sheet and a CSV. The original extraction values are
# never overwritten except a blank cell by fills_blank, so the table is reversible
# from the provenance log.
#
# Deterministic, no LLM. Python 3.8 strict.
#
# Usage:
#   python phase4_overlay.py --xlsx ../../7-Manuscript/extractions_ai_included_v134.xlsx \
#       --verify-dir out/verify_tasks --out out
#   python phase4_overlay.py --selftest

import sys
import os
import re
import csv
import json
import argparse
from collections import Counter

from openpyxl import load_workbook
from openpyxl.styles import PatternFill
from openpyxl.comments import Comment

ABSENCE = set(["", "not_reported", "none", "none_reported", "not_applicable", "null", "n/a", "na", "-"])
FILL_COLOR = {"fills_blank": "C6EFCE", "appends_extra": "BDD7EE", "contradicts": "FFEB9C"}
LIST_SEP = "; "


def norm(v):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v).strip()


def is_absence(v):
    return norm(v).lower() in ABSENCE


def value_columns(ws):
    """field name -> 1-based value column index (exclude _quote companions)."""
    names = [norm(ws.cell(row=2, column=c).value) for c in range(1, ws.max_column + 1)]
    m = {}
    for i, nm in enumerate(names):
        if nm and not nm.endswith("_quote") and nm not in m:
            m[nm] = i + 1
    return m


def find_row(ws, sn_col, sn):
    for r in range(3, ws.max_row + 1):
        if norm(ws.cell(row=r, column=sn_col).value) == sn:
            return r
    return None


def run(a):
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    wb = load_workbook(a.xlsx)
    ws = wb["extractions"]
    vcol = value_columns(ws)
    sn_col = vcol.get("stable_name")
    if not sn_col:
        raise SystemExit("no stable_name column in extractions sheet")

    prov = []
    counts = Counter()
    skipped = []

    vfiles = [f for f in os.listdir(a.verify_dir) if f.endswith(".verify.json")]
    for vf in sorted(vfiles):
        sn = vf.replace(".verify.json", "")
        try:
            vrep = json.load(open(os.path.join(a.verify_dir, vf)))
            task = json.load(open(os.path.join(a.verify_dir, sn + ".task.json")))
        except Exception as e:
            skipped.append((sn, "load_error:" + type(e).__name__))
            continue
        recs = task.get("records", [])
        decs = vrep.get("decisions", [])
        if len(recs) != len(decs):
            skipped.append((sn, "count_mismatch recs={} decs={}".format(len(recs), len(decs))))
            counts["paper_skipped_mismatch"] += 1
            continue
        row = find_row(ws, sn_col, sn)
        if row is None:
            skipped.append((sn, "paper_not_in_table"))
            continue
        for rec, dec in zip(recs, decs):
            field = rec.get("field")
            verdict = rec.get("verdict")
            decision = dec.get("decision")
            counts["decision_" + str(decision)] += 1
            if decision != "accept":
                prov.append([sn, field, verdict, "not_applied:" + str(decision), "", "",
                             norm(rec.get("quote")), norm(rec.get("supp_source")),
                             decision, norm(dec.get("reason"))])
                continue
            val = dec.get("corrected_value")
            if val is None:
                val = rec.get("si_value")
            val = norm(val)
            col = vcol.get(field)
            if not col:
                skipped.append((sn, "field_not_in_table:" + str(field)))
                prov.append([sn, field, verdict, "skip:field_absent", "", val,
                             norm(rec.get("quote")), norm(rec.get("supp_source")),
                             decision, norm(dec.get("reason"))])
                continue
            cell = ws.cell(row=row, column=col)
            old = norm(cell.value)

            if verdict == "fills_blank":
                if not is_absence(old):
                    prov.append([sn, field, verdict, "skip:cell_not_blank", old, val,
                                 norm(rec.get("quote")), norm(rec.get("supp_source")),
                                 decision, "cell already had a value; fill skipped to protect extraction"])
                    counts["skip_not_blank"] += 1
                    continue
                cell.value = val
                cell.fill = PatternFill("solid", fgColor=FILL_COLOR["fills_blank"])
                cell.comment = Comment("ENRICHMENT fill from SI: {}\nsource: {}\nverify: {}".format(
                    val, norm(rec.get("supp_source")), norm(dec.get("reason")))[:2000], "enrichment")
                prov.append([sn, field, verdict, "applied:fill", old, val,
                             norm(rec.get("quote")), norm(rec.get("supp_source")), decision, norm(dec.get("reason"))])
                counts["applied_fill"] += 1

            elif verdict == "appends_extra":
                items = [x.strip() for x in old.split(";") if x.strip()] if not is_absence(old) else []
                if val.lower() in [i.lower() for i in items]:
                    prov.append([sn, field, verdict, "skip:already_present", old, val,
                                 norm(rec.get("quote")), norm(rec.get("supp_source")), decision, "value already in list"])
                    counts["skip_dup_append"] += 1
                    continue
                newv = LIST_SEP.join(items + [val]) if items else val
                cell.value = newv
                cell.fill = PatternFill("solid", fgColor=FILL_COLOR["appends_extra"])
                cell.comment = Comment("ENRICHMENT append from SI: +{}\nsource: {}\nverify: {}".format(
                    val, norm(rec.get("supp_source")), norm(dec.get("reason")))[:2000], "enrichment")
                prov.append([sn, field, verdict, "applied:append", old, newv,
                             norm(rec.get("quote")), norm(rec.get("supp_source")), decision, norm(dec.get("reason"))])
                counts["applied_append"] += 1

            elif verdict == "contradicts":
                # annotate only; do NOT change the extraction value
                cell.fill = PatternFill("solid", fgColor=FILL_COLOR["contradicts"])
                cell.comment = Comment("ENRICHMENT contradiction (NOT applied). extraction={} | SI={}\nsource: {}\nverify: {}".format(
                    old, val, norm(rec.get("supp_source")), norm(dec.get("reason")))[:2000], "enrichment")
                prov.append([sn, field, verdict, "annotated:contradiction", old, val,
                             norm(rec.get("quote")), norm(rec.get("supp_source")), decision, norm(dec.get("reason"))])
                counts["annotated_contradiction"] += 1
            else:
                prov.append([sn, field, verdict, "noop:" + str(verdict), old, val,
                             norm(rec.get("quote")), norm(rec.get("supp_source")), decision, norm(dec.get("reason"))])
                counts["noop"] += 1

    # provenance sheet
    if "enrichment_provenance" in wb.sheetnames:
        del wb["enrichment_provenance"]
    ps = wb.create_sheet("enrichment_provenance")
    hdr = ["stable_name", "field", "verdict", "action", "old_value", "new_or_si_value",
           "si_quote", "supp_source", "verify_decision", "verify_reason"]
    ps.append(hdr)
    for row in prov:
        ps.append([norm(x)[:1000] for x in row])

    out_xlsx = os.path.join(a.out, "extractions_ai_enriched_v134.xlsx")
    wb.save(out_xlsx)
    prov_csv = os.path.join(a.out, "enrichment_provenance.csv")
    with open(prov_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(hdr)
        for row in prov:
            w.writerow([norm(x)[:1000] for x in row])

    print("applied: fills={} appends={} | annotated contradictions={}".format(
        counts.get("applied_fill", 0), counts.get("applied_append", 0), counts.get("annotated_contradiction", 0)))
    print("skips: not_blank={} dup_append={} field_absent/other={}".format(
        counts.get("skip_not_blank", 0), counts.get("skip_dup_append", 0),
        sum(1 for s in skipped if "field_not_in_table" in s[1])))
    print("decisions seen: {}".format({k: v for k, v in counts.items() if k.startswith("decision_")}))
    if skipped:
        print("SKIPPED papers/fields ({}):".format(len(skipped)))
        for s in skipped:
            print("  ", s)
    print("wrote: {} | {} | provenance sheet in workbook".format(out_xlsx, prov_csv))


def selftest():
    import tempfile
    from openpyxl import Workbook
    d = tempfile.mkdtemp()
    wb = Workbook()
    ws = wb.active
    ws.title = "extractions"
    # row1 band, row2 names
    ws.append(["identifiers", None, None, "QC"])  # row1 (cosmetic)
    ws.cell(row=2, column=1, value="doi")
    ws.cell(row=2, column=2, value="stable_name")
    ws.cell(row=2, column=3, value="qubit_count")
    ws.cell(row=2, column=4, value="qubit_count_quote")
    ws.cell(row=2, column=5, value="dataset_name")
    ws.cell(row=2, column=6, value="simulator_framework")
    # data row
    ws.cell(row=3, column=1, value="10/x")
    ws.cell(row=3, column=2, value="p1")
    ws.cell(row=3, column=3, value="not_reported")   # blank -> fill
    ws.cell(row=3, column=5, value="OASIS-2")         # list -> append
    ws.cell(row=3, column=6, value="Qiskit_Aer")      # contradicts -> annotate only
    xp = os.path.join(d, "t.xlsx")
    wb.save(xp)

    vt = os.path.join(d, "vt")
    os.makedirs(vt)
    task = {"stable_name": "p1", "doi": "10/x", "records": [
        {"field": "qubit_count", "verdict": "fills_blank", "si_value": "8 qubits", "quote": "8 qubits", "supp_source": "cfg", "flag": "review"},
        {"field": "dataset_name", "verdict": "appends_extra", "si_value": "ADNI", "quote": "ADNI", "supp_source": "si", "flag": "review"},
        {"field": "simulator_framework", "verdict": "contradicts", "si_value": "StatevectorEstimator", "quote": "StatevectorEstimator", "supp_source": "nb", "flag": "review"},
        {"field": "dataset_name", "verdict": "appends_extra", "si_value": "MNIST", "quote": "mnist", "supp_source": "nb", "flag": "hold:suspected_control_dataset"},
    ]}
    vrep = {"stable_name": "p1", "decisions": [
        {"field": "qubit_count", "verdict": "fills_blank", "decision": "accept", "reason": "cfg n_qubits 8", "evidence_quote": "8 qubits"},
        {"field": "dataset_name", "verdict": "appends_extra", "decision": "accept", "reason": "ADNI used", "evidence_quote": "ADNI"},
        {"field": "simulator_framework", "verdict": "contradicts", "decision": "accept", "reason": "uses statevector primitive not Aer", "evidence_quote": "StatevectorEstimator"},
        {"field": "dataset_name", "verdict": "appends_extra", "decision": "reject", "reason": "MNIST is a control", "evidence_quote": "mnist"},
    ]}
    json.dump(task, open(os.path.join(vt, "p1.task.json"), "w"))
    json.dump(vrep, open(os.path.join(vt, "p1.verify.json"), "w"))

    class A:
        xlsx = xp
        verify_dir = vt
        out = os.path.join(d, "out")
    run(A)
    e = load_workbook(os.path.join(A.out, "extractions_ai_enriched_v134.xlsx"))["extractions"]
    assert norm(e.cell(row=3, column=3).value) == "8 qubits", "fill applied to blank"
    assert norm(e.cell(row=3, column=5).value) == "OASIS-2; ADNI", "accepted append added, control MNIST not"
    assert norm(e.cell(row=3, column=6).value) == "Qiskit_Aer", "contradiction annotate-only, value unchanged"
    assert e.cell(row=3, column=6).comment is not None, "contradiction comment present"
    prov = list(csv.reader(open(os.path.join(A.out, "enrichment_provenance.csv"))))
    actions = [r[3] for r in prov[1:]]
    assert "applied:fill" in actions and "applied:append" in actions and "annotated:contradiction" in actions
    assert any(a.startswith("not_applied:reject") for a in actions), "rejected MNIST logged as not applied"
    print("selftest OK: fill to blank, accepted append, control rejected, contradiction annotate-only, provenance logged")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx")
    ap.add_argument("--verify-dir", dest="verify_dir")
    ap.add_argument("--out", default="out")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.xlsx or not a.verify_dir:
        ap.error("--xlsx and --verify-dir are required")
    run(a)


if __name__ == "__main__":
    main()
