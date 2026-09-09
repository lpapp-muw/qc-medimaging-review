"""
screen_scope.py
Step 2A: scope screening of all records in merged_dataset.json against the
locked criterion for the systematic review "Quantum computing for medical
imaging applications" (IEEE TRPMS).

Multi-class architecture:
    - Model assigns a PRIMARY category from a fixed 10-class taxonomy.
    - Model optionally assigns a SECONDARY category for cross-cutting papers.
    - Model assigns a confidence label: "high" or "low".
    - Both primary and secondary categories carry a verbatim evidence_quote.
    - in_scope is derived deterministically after the call:
          primary == QC_FOR_IMAGING AND confidence == high  -> "yes"
          primary == QC_FOR_IMAGING AND confidence == low   -> "uncertain"
          otherwise                                          -> "no"

Hallucination controls:
    - temperature = 0
    - model and prompt hash logged with every result
    - one record per call (no batching)
    - pydantic schema validation on response
    - evidence_quote substring-verified for primary AND secondary
    - records that fail any check have validation.ok = false (not silently kept)

Resumable: re-running with the same --output skips records already processed.

Usage:
    pip install anthropic pydantic tenacity
    export ANTHROPIC_API_KEY=sk-ant-...
    python screen_scope.py --input merged_dataset.json \
                           --output screen_results.jsonl \
                           --call-log screen_calls.jsonl \
                           --model claude-sonnet-4-6 \
                           --concurrency 8 \
                           --limit 30          # PILOT FIRST
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Literal

try:
    from anthropic import AsyncAnthropic
except ImportError:
    sys.exit("Install: pip install anthropic")

try:
    from pydantic import BaseModel, ValidationError
except ImportError:
    sys.exit("Install: pip install pydantic")

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
except ImportError:
    sys.exit("Install: pip install tenacity")

# ---------------------------------------------------------------------------
# Locked taxonomy
# ---------------------------------------------------------------------------

CATEGORIES = [
    "QC_FOR_IMAGING",
    "QUANTUM_DOTS",
    "NANO_THERAPEUTICS",
    "FLUORESCENCE_PROBES",
    "QUANTUM_SENSING_BIOMED",
    "QUANTUM_CRYPTO_MED",
    "QUANTUM_INSPIRED_CLASSICAL",
    "CLASSICAL_AI_FOR_IMAGING",
    "CLINICAL_NON_IMAGING",
    "OTHER",
]

CategoryLiteral = Literal[
    "QC_FOR_IMAGING",
    "QUANTUM_DOTS",
    "NANO_THERAPEUTICS",
    "FLUORESCENCE_PROBES",
    "QUANTUM_SENSING_BIOMED",
    "QUANTUM_CRYPTO_MED",
    "QUANTUM_INSPIRED_CLASSICAL",
    "CLASSICAL_AI_FOR_IMAGING",
    "CLINICAL_NON_IMAGING",
    "OTHER",
]

# ---------------------------------------------------------------------------
# Locked prompt (changing this changes prompt_hash and invalidates prior runs)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You classify papers for a PRISMA systematic review titled "Quantum computing for medical imaging applications" for IEEE Transactions on Radiation and Plasma Medical Sciences.

You assign each paper to exactly ONE primary category from the fixed taxonomy below, and OPTIONALLY a secondary category if a second area is substantially discussed in the abstract. The primary category is the one the paper is principally ABOUT; the secondary is a clearly present additional area, not a passing mention.

TAXONOMY (mutually exclusive; pick the SINGLE best primary):

QC_FOR_IMAGING
    The paper applies quantum computing methods to a medical imaging task.
    Quantum computing methods include: quantum machine learning (QML), quantum neural networks, variational quantum algorithms (VQE, QAOA), quantum kernels, quantum support vector machines, quantum convolutional networks, quantum amplitude estimation, quantum image representation algorithms (FRQI, NEQR), or hybrid quantum-classical models.
    The methods MUST be executed on quantum hardware (IBM Quantum, IonQ, Rigetti, Quantinuum, etc.) OR on a quantum simulator (Qiskit Aer, PennyLane default.qubit, Cirq Simulator, Microsoft QDK, Xanadu Strawberry Fields, Quantum Inspire, ProjectQ, etc.).
    The medical imaging task includes: image reconstruction (CT, PET, MRI, SPECT, ultrasound, OCT, X-ray), image analysis (segmentation, classification, detection, denoising, super-resolution), radiomics, multi-modal image fusion, image quality assessment, or simulation/dosimetry of medical imaging instrumentation.

QUANTUM_DOTS
    Quantum dots, carbon dots, graphene quantum dots, perovskite quantum dots, or similar nanomaterials studied as a chemical / synthesis / photoluminescence / optoelectronic / sensor material. The word "quantum" here refers to nanocrystals, NOT to quantum computing.

NANO_THERAPEUTICS
    Drug delivery, photodynamic therapy (PDT), photothermal therapy (PTT), theranostics, or fluorescent probes whose primary purpose is therapeutic application of nanoparticles.

FLUORESCENCE_PROBES
    Fluorescent dyes, NIR / NIR-II probes, biosensors, or fluorescence microscopy used for cell, tumor, or molecular imaging, where the focus is the probe / dye / sensor, not a quantum computing algorithm.

QUANTUM_SENSING_BIOMED
    Quantum sensing technology applied to biomedicine WITHOUT a quantum computing algorithm step. Includes optically-pumped magnetometers (OPM), magnetoencephalography (MEG with OPM), nitrogen-vacancy (NV) center magnetometry, diamond quantum sensors, ghost imaging, quantum illumination, single-photon imaging, entangled-photon imaging.

QUANTUM_CRYPTO_MED
    Quantum cryptography, quantum key distribution (QKD), quantum-safe encryption, watermarking, blockchain, or secure transmission / storage of medical images.

QUANTUM_INSPIRED_CLASSICAL
    Quantum-INSPIRED classical algorithms executed only on classical hardware: tensor networks branded "quantum", "quantum-inspired" optimization, simulated annealing called "quantum annealing" while running on a classical computer. No actual quantum hardware or quantum simulator is used.

CLASSICAL_AI_FOR_IMAGING
    Classical artificial intelligence, deep learning, machine learning, CNNs, transformers, or other purely classical methods applied to medical imaging, with NO quantum computing component anywhere in the methods.

CLINICAL_NON_IMAGING
    Clinical trial, biomarker, outcomes, epidemiological, surgical, or non-imaging methodology study. Includes papers that mention imaging only descriptively (e.g. patients underwent MRI as part of standard workup) but do not contribute imaging methodology or QC.

OTHER
    None of the above. Use sparingly. Examples: condensed-matter physics with vague medical motivation; pure quantum chemistry without imaging; review articles outside the corpus scope.

DECISION GUIDANCE:
- Pick PRIMARY based on what the paper is principally about, weighted by the abstract.
- Pick SECONDARY only if a clearly-present second area in the taxonomy is substantially discussed (more than a passing mention). Otherwise leave secondary null.
- PRIMARY and SECONDARY must be different categories.
- Do NOT assign QC_FOR_IMAGING based on a single keyword like "quantum" or "qubit" if the paper is overwhelmingly about something else (e.g. quantum dot synthesis). The QC method must be a substantive part of the paper.
- Do NOT extrapolate beyond what the title and abstract state.

CONFIDENCE:
- "high" if the abstract clearly supports the primary classification.
- "low" if the abstract is missing/very short, the quantum keyword is ambiguous, or the categorization required guesswork. Low-confidence QC_FOR_IMAGING verdicts will go to human review.

EVIDENCE:
For PRIMARY and SECONDARY (when used), provide a verbatim quote from either the title or abstract that justifies that category. The quote MUST appear EXACTLY in the source text (whitespace-tolerant, case-insensitive). Set evidence_field to "title" or "abstract" accordingly.

OUTPUT:
Respond with ONLY a JSON object, no markdown, no commentary, this exact schema:
{
  "primary": {
    "category": "QC_FOR_IMAGING" | "QUANTUM_DOTS" | "NANO_THERAPEUTICS" | "FLUORESCENCE_PROBES" | "QUANTUM_SENSING_BIOMED" | "QUANTUM_CRYPTO_MED" | "QUANTUM_INSPIRED_CLASSICAL" | "CLASSICAL_AI_FOR_IMAGING" | "CLINICAL_NON_IMAGING" | "OTHER",
    "evidence_quote": string,
    "evidence_field": "title" | "abstract"
  },
  "secondary": {
    "category": <same enum>,
    "evidence_quote": string,
    "evidence_field": "title" | "abstract"
  } | null,
  "confidence": "high" | "low",
  "reasoning": string
}"""

USER_TEMPLATE = """Title: {title}

Abstract: {abstract}"""

# ---------------------------------------------------------------------------
# Response schema
# ---------------------------------------------------------------------------

class CategoryEvidence(BaseModel):
    category: CategoryLiteral
    evidence_quote: str
    evidence_field: Literal["title", "abstract"]

class ScreenResponse(BaseModel):
    primary: CategoryEvidence
    secondary: Optional[CategoryEvidence] = None
    confidence: Literal["high", "low"]
    reasoning: str

# ---------------------------------------------------------------------------
# Derivation: in_scope from primary + confidence
# ---------------------------------------------------------------------------

def derive_in_scope(primary_cat: str, confidence: str) -> str:
    if primary_cat == "QC_FOR_IMAGING" and confidence == "high":
        return "yes"
    if primary_cat == "QC_FOR_IMAGING" and confidence == "low":
        return "uncertain"
    return "no"

# ---------------------------------------------------------------------------
# Text sanitation (for prompt input only; original record never modified)
# ---------------------------------------------------------------------------

def sanitize(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"</?jats:[^>]+>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\$\$[^$]+\$\$", " [formula] ", s)
    s = re.sub(r"\\[a-zA-Z]+\{[^}]*\}", " ", s)
    s = re.sub(r"&[a-z]+;", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def normalize_for_match(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def quote_in_source(quote: str, title: str, abstract: str, field: str) -> bool:
    nq = normalize_for_match(quote)
    if not nq:
        return False
    if field == "title":
        return nq in normalize_for_match(title)
    if field == "abstract":
        return nq in normalize_for_match(abstract)
    return False

# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None

# ---------------------------------------------------------------------------
# Anthropic call with retry
# ---------------------------------------------------------------------------

class TransientError(Exception): pass

@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    retry=retry_if_exception_type(TransientError),
    reraise=True,
)
async def call_model(client, model, system, user, max_tokens=900):
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                text += block.text
        usage = {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        }
        return text, usage
    except Exception as e:
        msg = str(e).lower()
        if any(s in msg for s in ["429", "overloaded", "timeout", "connection",
                                  "rate", "503", "502", "500"]):
            raise TransientError(str(e)) from e
        raise

# ---------------------------------------------------------------------------
# Per-record screening
# ---------------------------------------------------------------------------

async def screen_one(client, model, prompt_hash, record, semaphore, call_log_fp):
    async with semaphore:
        rid = record["id"]
        doi = record.get("DOI", "") or ""
        title = sanitize(record.get("title", ""))
        abstract = sanitize(record.get("abstract", ""))
        text_source = "abstract" if abstract else "title_only"

        user = USER_TEMPLATE.format(
            title=title or "(missing)",
            abstract=abstract or "(no abstract available)",
        )

        t0 = time.time()
        raw_text = ""
        usage = {}
        api_error = None
        try:
            raw_text, usage = await call_model(client, model, SYSTEM_PROMPT, user)
        except Exception as e:
            api_error = f"{type(e).__name__}: {e}"

        latency_ms = int((time.time() - t0) * 1000)

        validation = {"ok": False, "errors": []}
        parsed = None

        if api_error:
            validation["errors"].append(f"api_error: {api_error}")
        else:
            obj = extract_json(raw_text)
            if obj is None:
                validation["errors"].append("json_parse_failed")
            else:
                try:
                    parsed_model = ScreenResponse.model_validate(obj)
                    parsed = parsed_model.model_dump()
                except ValidationError as ve:
                    validation["errors"].append(f"schema_invalid: {ve.errors()}")

        # Per-quote checks
        if parsed:
            ok_primary = quote_in_source(
                parsed["primary"]["evidence_quote"],
                title, abstract,
                parsed["primary"]["evidence_field"],
            )
            if not ok_primary:
                validation["errors"].append("primary_evidence_not_in_source")

            ok_secondary = True
            if parsed.get("secondary"):
                ok_secondary = quote_in_source(
                    parsed["secondary"]["evidence_quote"],
                    title, abstract,
                    parsed["secondary"]["evidence_field"],
                )
                if not ok_secondary:
                    validation["errors"].append("secondary_evidence_not_in_source")
                if parsed["secondary"]["category"] == parsed["primary"]["category"]:
                    validation["errors"].append("secondary_equals_primary")
                    ok_secondary = False

            if ok_primary and ok_secondary:
                validation["ok"] = True

        # Derive in_scope only if we have a parsed verdict
        in_scope = None
        if parsed and validation["ok"]:
            in_scope = derive_in_scope(parsed["primary"]["category"], parsed["confidence"])

        result = {
            "record_id": rid,
            "doi": doi,
            "title": record.get("title", ""),
            "text_source": text_source,
            "verdict": parsed,
            "in_scope": in_scope,                 # derived; null if validation failed
            "validation": validation,
            "model": model,
            "prompt_hash": prompt_hash,
            "usage": usage,
            "latency_ms": latency_ms,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

        call_log_fp.write(json.dumps({
            **result, "raw_response": raw_text, "api_error": api_error,
        }, ensure_ascii=False) + "\n")
        call_log_fp.flush()

        return result

# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def load_input(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

def load_processed(path):
    if not os.path.exists(path):
        return set()
    done = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                done.add(rec["record_id"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(args):
    client = AsyncAnthropic()
    prompt_hash = hashlib.sha256(
        (SYSTEM_PROMPT + "|" + USER_TEMPLATE).encode("utf-8")
    ).hexdigest()[:16]

    print(f"Loading {args.input} ...", file=sys.stderr)
    records = load_input(args.input)
    print(f"  Records: {len(records)}", file=sys.stderr)

    done = load_processed(args.output)
    print(f"  Already processed: {len(done)}", file=sys.stderr)

    todo = [r for r in records if r.get("id") and r["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"  To process this run: {len(todo)}", file=sys.stderr)
    print(f"  Model: {args.model}", file=sys.stderr)
    print(f"  Prompt hash: {prompt_hash}", file=sys.stderr)
    print(f"  Concurrency: {args.concurrency}", file=sys.stderr)
    if not todo:
        print("Nothing to do.", file=sys.stderr); return

    sem = asyncio.Semaphore(args.concurrency)
    cat_counts = {c: 0 for c in CATEGORIES}
    scope_counts = {"yes": 0, "no": 0, "uncertain": 0, "invalid": 0}
    conf_counts = {"high": 0, "low": 0}
    in_tokens_total = 0
    out_tokens_total = 0
    started = time.time()

    out_fp = open(args.output, "a", encoding="utf-8")
    call_fp = open(args.call_log, "a", encoding="utf-8")

    try:
        tasks = [
            asyncio.create_task(screen_one(client, args.model, prompt_hash, r, sem, call_fp))
            for r in todo
        ]
        for i, fut in enumerate(asyncio.as_completed(tasks), 1):
            result = await fut
            out_fp.write(json.dumps(result, ensure_ascii=False) + "\n")
            out_fp.flush()
            v = result["verdict"]
            if not result["validation"]["ok"] or v is None:
                scope_counts["invalid"] += 1
            else:
                scope_counts[result["in_scope"]] = scope_counts.get(result["in_scope"], 0) + 1
                cat_counts[v["primary"]["category"]] += 1
                conf_counts[v["confidence"]] += 1
            u = result["usage"]
            in_tokens_total += u.get("input_tokens", 0)
            out_tokens_total += u.get("output_tokens", 0)

            if i == 1 or i % 25 == 0 or i == len(todo):
                elapsed = time.time() - started
                rate = i / elapsed if elapsed else 0
                eta = (len(todo) - i) / rate if rate else 0
                print(
                    f"[{i}/{len(todo)}] yes={scope_counts['yes']} "
                    f"no={scope_counts['no']} unc={scope_counts['uncertain']} "
                    f"inv={scope_counts['invalid']}  "
                    f"in_tok={in_tokens_total} out_tok={out_tokens_total} "
                    f"elapsed={int(elapsed)}s eta={int(eta)}s",
                    file=sys.stderr,
                )
    finally:
        out_fp.close()
        call_fp.close()

    print("\n" + "=" * 60, file=sys.stderr)
    print(f"Done. Processed {len(todo)} records.", file=sys.stderr)
    print(f"\nDerived in_scope:", file=sys.stderr)
    for k in ["yes", "uncertain", "no", "invalid"]:
        n = scope_counts.get(k, 0)
        print(f"  {k:12s} {n}  ({100*n/max(1,len(todo)):.1f}%)", file=sys.stderr)
    print(f"\nPrimary category distribution:", file=sys.stderr)
    for c in CATEGORIES:
        n = cat_counts[c]
        print(f"  {c:30s} {n}  ({100*n/max(1,len(todo)):.1f}%)", file=sys.stderr)
    print(f"\nConfidence:", file=sys.stderr)
    for k, n in conf_counts.items():
        print(f"  {k:12s} {n}", file=sys.stderr)
    print(f"\nTotal input tokens:  {in_tokens_total}", file=sys.stderr)
    print(f"Total output tokens: {out_tokens_total}", file=sys.stderr)
    print(f"\nResults: {args.output}", file=sys.stderr)
    print(f"Call log: {args.call_log}", file=sys.stderr)


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="merged_dataset.json")
    p.add_argument("--output", default="screen_results.jsonl")
    p.add_argument("--call-log", default="screen_calls.jsonl")
    p.add_argument("--model", default="claude-sonnet-4-6")
    p.add_argument("--concurrency", type=int, default=8)
    p.add_argument("--limit", type=int, default=None,
                   help="Stop after N records (use for pilot)")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY in your environment first.")

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
