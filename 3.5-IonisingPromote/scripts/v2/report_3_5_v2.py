#!/usr/bin/env python3
"""
report_3_5_v2.py
================

Generate text and JSON summary reports of the Step-3.5 v2 ionising sub-screen.

Matches the structure of v1's report_3_5.{txt,json} for direct comparison.

Outputs:
    v2_active/report_3_5_v2.txt
    v2_active/report_3_5_v2.json

Usage:
    python3 scripts/v2/report_3_5_v2.py
"""

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve()
STEP_DIR = HERE.parent.parent.parent
sys.path.insert(0, str(STEP_DIR))
import paths_v2 as P  # noqa: E402


def load_jsonl(path):
    if not Path(path).exists():
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
                continue
    return out


def main():
    verdicts = load_jsonl(P.VERDICTS_3_5_PATH)
    input_records = load_jsonl(P.INPUT_PATH)
    n_input = len(input_records)
    n_verdicts = len(verdicts)

    by_verdict = Counter(v.get("verdict") for v in verdicts)
    by_conf    = Counter(v.get("confidence") for v in verdicts)
    promotes = [v for v in verdicts if v.get("verdict") == "promote_to_IN"]
    stays    = [v for v in verdicts if v.get("verdict") == "stay_OUT"]

    by_modality = Counter(p.get("ionising_modality_name") for p in promotes)
    by_conf_for_promotes = Counter(p.get("confidence") for p in promotes)
    by_conf_for_stays    = Counter(s.get("confidence") for s in stays)

    # Provenance distribution (interesting: where did v2-only promotes' abstracts come from?)
    input_by_rid = {r.get("record_id"): r for r in input_records}
    promote_provenance = Counter(
        input_by_rid.get(p.get("record_id"), {}).get("abstract_provenance", "unknown")
        for p in promotes
    )

    # Build JSON
    report_json = {
        "step": "3.5_v2",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_path": str(P.INPUT_PATH),
        "verdicts_path": str(P.VERDICTS_3_5_PATH),
        "n_input": n_input,
        "n_verdicts": n_verdicts,
        "promotes": len(promotes),
        "stay_OUT": len(stays),
        "confidence": dict(by_conf),
        "modality_of_promotes": dict(by_modality),
        "confidence_for_promotes": dict(by_conf_for_promotes),
        "confidence_for_stays": dict(by_conf_for_stays),
        "promote_abstract_provenance": dict(promote_provenance),
        "promotes_detail": [
            {
                "record_id": p.get("record_id"),
                "doi": p.get("doi"),
                "modality": p.get("ionising_modality_name"),
                "confidence": p.get("confidence"),
                "reasoning": p.get("reasoning"),
            }
            for p in promotes
        ],
    }

    P.REPORT_JSON.write_text(json.dumps(report_json, indent=2, ensure_ascii=False))

    # Build text report
    lines = [
        "Step 3.5 v2 - Ionising Sub-Screen Report",
        "=" * 60,
        f"Generated: {report_json['generated_at']}",
        f"Input:     {P.INPUT_PATH}",
        f"Verdicts:  {P.VERDICTS_3_5_PATH}",
        "",
        f"Records in input set:       {n_input}",
        f"Verdicts produced:          {n_verdicts}",
        "",
        "Verdict distribution:",
    ]
    for k, n in by_verdict.most_common():
        lines.append(f"  {n:4d}  {k}")
    lines += ["", "Overall confidence:"]
    for k, n in by_conf.most_common():
        lines.append(f"  {n:4d}  {k}")

    if promotes:
        lines += [
            "",
            f"Promotes to IN-scope (n={len(promotes)}):",
            "",
            "  Modality distribution:",
        ]
        for k, n in by_modality.most_common():
            lines.append(f"    {n:3d}  {k}")
        lines += ["", "  Confidence distribution:"]
        for k, n in by_conf_for_promotes.most_common():
            lines.append(f"    {n:3d}  {k}")
        lines += ["", "  Abstract provenance of promoted records:"]
        for k, n in promote_provenance.most_common():
            lines.append(f"    {n:3d}  {k}")
        lines += ["", "  Per-record detail:", ""]
        for i, p in enumerate(promotes, 1):
            lines.append(f"  {i}. DOI: {p.get('doi')}")
            lines.append(f"     Modality: {p.get('ionising_modality_name')}")
            lines.append(f"     Confidence: {p.get('confidence')}")
            lines.append(f"     Reasoning: {p.get('reasoning')}")
            lines.append("")

    lines += [
        "",
        f"Stay_OUT confidence distribution:",
    ]
    for k, n in by_conf_for_stays.most_common():
        lines.append(f"  {n:3d}  {k}")

    P.REPORT_TXT.write_text("\n".join(lines) + "\n")

    # Print to stdout too
    print("\n".join(lines))
    print()
    print(f"Files written:")
    print(f"  {P.REPORT_TXT}")
    print(f"  {P.REPORT_JSON}")


if __name__ == "__main__":
    main()
