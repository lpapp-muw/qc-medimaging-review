"""
retry_quarantined.py — Build retry task manifests for fields quarantined in
the Step-4 production run.

Reads quarantine_step4.jsonl, groups quarantined fields by (stable_name,
pass_id), and writes one task manifest per group under
pending_step4_retry/<pass>/<stable>__c<chunk>.retry.task.json.

Each retry manifest mirrors the structure of the original task manifest from
pending_step4/ but adds:
  - "_retry_context": a structured block listing the specific fields that
    previously quarantined, with the error reasons.
  - "_retry_instruction": a verbatim string the subagent will read at the
    top of its task, telling it to default to _image_only:true,quote:null
    aggressively for the listed fields.
  - "_is_retry": true (flag for downstream validation logic).

The subagents on disk (extract-pass-a..d, reconcile-extraction) are NOT
modified. They process the retry manifest like a normal task; the retry
context lives in the manifest payload.

CLI:
  python3 retry_quarantined.py                # build manifests
  python3 retry_quarantined.py --dry-run      # preview, write nothing
  python3 retry_quarantined.py --selftest

Python 3.8 compatible. Stdlib only.
"""

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import paths_step4 as P


QUARANTINE_JSONL = P.EXTRACT_DIR / "quarantine_step4.jsonl"
ORIG_PENDING = P.EXTRACT_DIR / "pending_step4"
RETRY_PENDING = P.EXTRACT_DIR / "pending_step4_retry"


# ----------------------------------------------------------------------------
# Retry instruction (verbatim string injected into every retry manifest)
# ----------------------------------------------------------------------------
RETRY_INSTRUCTION = (
    "THIS IS A RETRY TASK. The fields listed in _retry_context.failed_fields "
    "FAILED quote-grounding validation on the previous run because you "
    "emitted a text-form `quote` for facts that exist only in page-image "
    "regions (acknowledgements / dataset tables / figure captions / "
    "two-column layouts that pdftotext dropped). For EVERY field listed in "
    "_retry_context.failed_fields, you MUST either: "
    "(a) confirm a verbatim substring of the TEXT block exists (mentally "
    "search for a distinctive phrase before emitting), OR "
    "(b) set `quote: null` AND add `_image_only: true` to that field's "
    "wrapper. Do not invent a quote. Do not approximate. Do not paraphrase. "
    "Other fields not listed in _retry_context.failed_fields: extract them "
    "as you would on a normal task. Output schema and chunk_id / pass_id / "
    "_meta echoing rules are UNCHANGED from the original prompt."
)


# ----------------------------------------------------------------------------
# Stable-name parsing
# ----------------------------------------------------------------------------
_TASK_FN_RE = re.compile(r"^(?P<stable>.+?)__c(?P<chunk>\d+)\.task\.json$")


def parse_task_filename(name):
    m = _TASK_FN_RE.match(name)
    if not m:
        return None, None
    return m.group("stable"), int(m.group("chunk"))


# ----------------------------------------------------------------------------
# Quarantine parsing
# ----------------------------------------------------------------------------
def _stable_name_from_record(rec):
    """Read stable_name from various possible record shapes."""
    meta = rec.get("_meta") or {}
    if isinstance(meta, dict) and meta.get("stable_name"):
        return meta["stable_name"]
    return rec.get("stable_name") or rec.get("_stable_name") or ""


def _pass_id_from_record(rec):
    """Pass ID may live in _meta or in any field wrapper's pass_id."""
    meta = rec.get("_meta") or {}
    if isinstance(meta, dict) and meta.get("pass_id"):
        return meta["pass_id"]
    if rec.get("pass_id"):
        return rec["pass_id"]
    # Scan field wrappers for a pass_id
    for v in rec.values():
        if isinstance(v, dict) and v.get("pass_id"):
            return v["pass_id"]
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict) and item.get("pass_id"):
                    return item["pass_id"]
    return ""


def _chunk_id_from_record(rec):
    meta = rec.get("_meta") or {}
    if isinstance(meta, dict) and meta.get("chunk_id") is not None:
        return int(meta["chunk_id"])
    for v in rec.values():
        if isinstance(v, dict) and v.get("chunk_id") is not None:
            return int(v["chunk_id"])
    return 0


def _failed_fields_from_record(rec):
    """Parse _error list. Each entry is like 'unrescued_quote:field_name' or
    'enum_violation:field_name:value'. Return list of {field, reason}."""
    out = []
    errs = rec.get("_error") or []
    if not isinstance(errs, list):
        errs = [errs]
    for e in errs:
        s = str(e)
        # split on first ':' to get error type
        if ":" in s:
            etype, rest = s.split(":", 1)
            # field name is whatever appears before any further ':' (for
            # multi-part errors like enum_violation:field:value)
            field = rest.split(":", 1)[0]
            out.append({"field": field.strip(), "reason": etype.strip()})
        else:
            out.append({"field": "", "reason": s.strip()})
    return out


def load_quarantine_groups():
    """Return {(stable, pass_id, chunk): [{field, reason}, ...]}."""
    if not QUARANTINE_JSONL.exists():
        raise FileNotFoundError("Not found: {}".format(QUARANTINE_JSONL))
    groups = defaultdict(list)
    n_records = 0
    n_unparsed_stable = 0
    n_unparsed_pass = 0
    with open(QUARANTINE_JSONL, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            n_records += 1
            stable = _stable_name_from_record(rec)
            pass_id = _pass_id_from_record(rec)
            chunk = _chunk_id_from_record(rec)
            if not stable:
                n_unparsed_stable += 1
                continue
            if not pass_id:
                n_unparsed_pass += 1
                continue
            for f in _failed_fields_from_record(rec):
                groups[(stable, pass_id, chunk)].append(f)
    return groups, {
        "n_records": n_records,
        "n_unparsed_stable": n_unparsed_stable,
        "n_unparsed_pass": n_unparsed_pass,
    }


# ----------------------------------------------------------------------------
# Find originating task manifest
# ----------------------------------------------------------------------------
def find_original_task(stable, pass_id, chunk):
    """Locate the original pending_step4/<pass>/<stable>__c<chunk>.task.json.
    Returns (path, dict) or (None, None) if missing."""
    fn = "{}__c{}.task.json".format(stable, chunk)
    fp = ORIG_PENDING / pass_id / fn
    if not fp.exists():
        return None, None
    try:
        return fp, json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        return fp, None


# ----------------------------------------------------------------------------
# Build retry manifest
# ----------------------------------------------------------------------------
def build_retry_manifest(orig_manifest, failed_fields, stable, pass_id, chunk):
    """Return a new manifest dict mirroring orig with retry-context block.
    The `reply_path` is rewritten to point at the retry verdict location so
    the retry write does NOT overwrite the original quarantined verdict."""
    retry = dict(orig_manifest) if orig_manifest else {}
    # Override reply_path so subagents write replies to the retry dir, not
    # the original pending_step4 dir.
    retry_reply = RETRY_PENDING / pass_id / "{}__c{}.retry.verdict.json".format(stable, chunk)
    retry["reply_path"] = str(retry_reply)
    # Deduplicate failed fields by name (multiple errors on same field collapse)
    seen = set()
    dedup = []
    for f in failed_fields:
        key = f.get("field", "")
        if key and key not in seen:
            seen.add(key)
            dedup.append(f)
    retry["_is_retry"] = True
    retry["_retry_instruction"] = RETRY_INSTRUCTION
    retry["_retry_context"] = {
        "stable_name": stable,
        "pass_id": pass_id,
        "chunk_id": chunk,
        "failed_fields": dedup,
        "n_failed": len(dedup),
    }
    return retry


# ----------------------------------------------------------------------------
# Write manifests
# ----------------------------------------------------------------------------
def write_retry_manifests(groups, dry_run=False):
    """Write one retry manifest per group. Returns counts dict."""
    n_written = 0
    n_missing_orig = 0
    n_missing_pass = defaultdict(int)
    missing = []

    if not dry_run:
        # Build clean retry dir; preserve existing if present? No — fresh build.
        if RETRY_PENDING.exists():
            shutil.rmtree(RETRY_PENDING)
        RETRY_PENDING.mkdir(parents=True, exist_ok=True)
        for p in ("A", "B", "C", "D"):
            (RETRY_PENDING / p).mkdir(parents=True, exist_ok=True)

    for (stable, pass_id, chunk), fields in sorted(groups.items()):
        orig_path, orig_manifest = find_original_task(stable, pass_id, chunk)
        if orig_manifest is None:
            n_missing_orig += 1
            n_missing_pass[pass_id] += 1
            missing.append((stable, pass_id, chunk))
            continue
        retry = build_retry_manifest(orig_manifest, fields, stable, pass_id, chunk)
        out_fn = "{}__c{}.retry.task.json".format(stable, chunk)
        out_path = RETRY_PENDING / pass_id / out_fn
        if not dry_run:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(retry, indent=2), encoding="utf-8")
        n_written += 1
    return {
        "n_written": n_written,
        "n_missing_orig": n_missing_orig,
        "n_missing_pass": dict(n_missing_pass),
        "missing_examples": missing[:10],
    }


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        _selftest()
        return 0

    if not QUARANTINE_JSONL.exists():
        print("ERROR: {} not found.".format(QUARANTINE_JSONL), file=sys.stderr)
        return 2
    if not ORIG_PENDING.exists():
        print("ERROR: {} not found. Cannot reconstruct retry manifests "
              "without the originals.".format(ORIG_PENDING), file=sys.stderr)
        return 2

    print("Reading quarantine:", QUARANTINE_JSONL)
    groups, stats = load_quarantine_groups()
    print("  Quarantine lines:           {}".format(stats["n_records"]))
    print("  Distinct (stable, pass, chunk) groups: {}".format(len(groups)))
    if stats["n_unparsed_stable"]:
        print("  Could not parse stable_name: {}".format(stats["n_unparsed_stable"]))
    if stats["n_unparsed_pass"]:
        print("  Could not parse pass_id:     {}".format(stats["n_unparsed_pass"]))

    # Per-pass breakdown
    per_pass = defaultdict(int)
    n_total_fields = 0
    for (stable, pass_id, chunk), fields in groups.items():
        per_pass[pass_id] += 1
        n_total_fields += len(fields)
    print()
    print("Retry tasks per pass:")
    for p in sorted(per_pass):
        print("  {}: {} task(s)".format(p, per_pass[p]))
    print("Total failed-field entries (pre-dedup): {}".format(n_total_fields))
    print()

    print("Building retry manifests under:", RETRY_PENDING)
    if args.dry_run:
        print("  (DRY RUN — nothing written)")
    out = write_retry_manifests(groups, dry_run=args.dry_run)
    print("  Manifests written:           {}".format(out["n_written"]))
    if out["n_missing_orig"]:
        print("  Missing original task file:  {}".format(out["n_missing_orig"]))
        for pass_id, n in sorted(out["n_missing_pass"].items()):
            print("    pass {}: {}".format(pass_id, n))
        print("  First missing groups:")
        for stable, pass_id, chunk in out["missing_examples"]:
            print("    {} pass={} chunk={}".format(stable, pass_id, chunk))
    print()
    if not args.dry_run:
        print("Retry manifest dirs:")
        for p in ("A", "B", "C", "D"):
            d = RETRY_PENDING / p
            if d.exists():
                n = len(list(d.glob("*.retry.task.json")))
                print("  {}: {} task(s)".format(d, n))
    print()
    print("Next: in Claude Code, dispatch the extract-pass-{a,b,c,d} subagent")
    print("against each retry manifest under pending_step4_retry/<pass>/.")
    print("Validation logic (batch_helper_step4.py validate --retry) is the")
    print("next script to build — not part of this script.")
    return 0


# ----------------------------------------------------------------------------
# Self-test
# ----------------------------------------------------------------------------
def _selftest():
    """Build a minimal fixture in a tempdir, exercise group/parse/write."""
    td = Path(tempfile.mkdtemp())
    # Fixture: quarantine + originating pending_step4 layout
    qjson = td / "quarantine_step4.jsonl"
    quar_records = [
        # Record 1: A pass, stable=p1, chunk=0, 3 failed fields
        {
            "_meta": {"stable_name": "p1", "pass_id": "A", "chunk_id": 0},
            "_error": [
                "unrescued_quote:funding_declared",
                "unrescued_quote:dataset_name",
                "enum_violation:translational_level:UNKNOWN",
            ],
            "title": {"value": "Test 1", "quote": "Test 1", "chunk_id": 0, "pass_id": "A"},
        },
        # Record 2: A pass, stable=p2, chunk=0, 1 failed field, multiple
        # references to same field in errors (dedup test)
        {
            "_meta": {"stable_name": "p2", "pass_id": "A", "chunk_id": 0},
            "_error": [
                "unrescued_quote:funding_source",
                "unrescued_quote:funding_source",
            ],
        },
        # Record 3: B pass, stable=p1, chunk=0
        {
            "_meta": {"stable_name": "p1", "pass_id": "B", "chunk_id": 0},
            "_error": ["unrescued_quote:qubit_count"],
        },
        # Record 4: missing stable_name (should be skipped)
        {
            "_meta": {"pass_id": "A"},
            "_error": ["unrescued_quote:foo"],
        },
    ]
    with open(qjson, "w", encoding="utf-8") as fh:
        for r in quar_records:
            fh.write(json.dumps(r) + "\n")

    # Fixture pending_step4 with originating manifests for p1.A.0, p2.A.0, p1.B.0
    pending = td / "pending_step4"
    for p in ("A", "B"):
        (pending / p).mkdir(parents=True, exist_ok=True)
    (pending / "A" / "p1__c0.task.json").write_text(json.dumps(
        {"stable_name": "p1", "pass_id": "A", "chunk_id": 0, "doi": "10.x/p1",
         "text_path": "p1.txt",
         "reply_path": str(pending / "A" / "p1__c0.verdict.json")}), encoding="utf-8")
    (pending / "A" / "p2__c0.task.json").write_text(json.dumps(
        {"stable_name": "p2", "pass_id": "A", "chunk_id": 0, "doi": "10.x/p2"}),
        encoding="utf-8")
    (pending / "B" / "p1__c0.task.json").write_text(json.dumps(
        {"stable_name": "p1", "pass_id": "B", "chunk_id": 0, "doi": "10.x/p1"}),
        encoding="utf-8")

    # Monkey-patch module paths for test
    global QUARANTINE_JSONL, ORIG_PENDING, RETRY_PENDING
    saved = (QUARANTINE_JSONL, ORIG_PENDING, RETRY_PENDING)
    QUARANTINE_JSONL = qjson
    ORIG_PENDING = pending
    RETRY_PENDING = td / "pending_step4_retry"

    try:
        groups, stats = load_quarantine_groups()
        # Expect 3 valid groups (p1.A.0, p2.A.0, p1.B.0); record 4 dropped
        assert len(groups) == 3, "expected 3 groups, got {}: {}".format(len(groups), groups)
        assert stats["n_records"] == 4
        assert stats["n_unparsed_stable"] == 1
        # p1.A.0 should have 3 fields
        p1a = groups[("p1", "A", 0)]
        assert len(p1a) == 3, "expected 3 fields in p1.A.0, got {}: {}".format(len(p1a), p1a)
        # p2.A.0 should dedup to 1 field
        out = write_retry_manifests(groups, dry_run=False)
        assert out["n_written"] == 3
        assert out["n_missing_orig"] == 0
        # Verify p2 manifest deduped funding_source
        p2_manifest = json.loads((RETRY_PENDING / "A" / "p2__c0.retry.task.json").read_text(encoding="utf-8"))
        assert p2_manifest["_retry_context"]["n_failed"] == 1
        assert p2_manifest["_retry_context"]["failed_fields"][0]["field"] == "funding_source"
        assert p2_manifest["_is_retry"] is True
        assert p2_manifest["_retry_instruction"] == RETRY_INSTRUCTION
        # Verify p1.A manifest preserves originating fields
        p1a_manifest = json.loads((RETRY_PENDING / "A" / "p1__c0.retry.task.json").read_text(encoding="utf-8"))
        assert p1a_manifest["doi"] == "10.x/p1"
        assert p1a_manifest["text_path"] == "p1.txt"
        assert len(p1a_manifest["_retry_context"]["failed_fields"]) == 3
        # Verify reply_path was rewritten to retry location, not original
        assert p1a_manifest["reply_path"].endswith("pending_step4_retry/A/p1__c0.retry.verdict.json"), \
            "reply_path not rewritten: {}".format(p1a_manifest["reply_path"])
        assert "pending_step4/A/p1__c0.verdict.json" not in p1a_manifest["reply_path"], \
            "reply_path still points to original verdict location"
        # Test missing-original case
        groups_missing = {("p_missing", "A", 0): [{"field": "foo", "reason": "x"}]}
        out2 = write_retry_manifests(groups_missing, dry_run=False)
        assert out2["n_missing_orig"] == 1
        assert out2["n_written"] == 0
        print("SELFTEST: ALL PASS")
        print("  Groups parsed: 3 (1 record dropped for missing stable_name as expected)")
        print("  Manifests written: 3")
        print("  Dedup verified on p2 (2 funding_source -> 1)")
        print("  Manifests preserve original fields (doi, text_path)")
        print("  Missing-original case detected correctly")
    finally:
        QUARANTINE_JSONL, ORIG_PENDING, RETRY_PENDING = saved


if __name__ == "__main__":
    sys.exit(main())
