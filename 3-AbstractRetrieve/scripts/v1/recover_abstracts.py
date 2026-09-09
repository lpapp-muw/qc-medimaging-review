"""
recover_abstracts.py
Recover missing abstracts for Zotero CSL-JSON records via Crossref, OpenAlex,
and EuropePMC. Resumable, idempotent, append-only output.

Usage:
    pip install requests
    python recover_abstracts.py \
        --input "/path/to/IEEE-QC-*.json" \
        --output recovered_abstracts.jsonl \
        --email you@example.com

Optional:
    --sources crossref,openalex,europepmc   # subset / reorder
    --strategy first-hit | longest          # default first-hit
    --limit N                               # stop after N records (testing)
    --sleep 0.5                             # seconds between queries
    --verbose

Output: JSONL, one line per processed DOI. Re-running with the same --output
skips DOIs already present in the file.

No API keys required. Email is sent in User-Agent (Crossref / OpenAlex polite
pool) and as ?mailto= for OpenAlex. Rate limits respected via --sleep.
"""

import argparse, glob, html, json, os, re, sys, time
from datetime import datetime, timezone
from urllib.parse import quote
import requests

# ---------------------------------------------------------------------------
# Source-specific fetchers
# ---------------------------------------------------------------------------

def strip_jats(s):
    """Crossref returns abstracts wrapped in JATS XML. Strip tags + decode."""
    if not s:
        return ""
    s = re.sub(r"</?jats:[^>]+>", " ", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def fetch_crossref(doi, ua, timeout):
    url = f"https://api.crossref.org/works/{quote(doi, safe='/.')}"
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        ab = r.json().get("message", {}).get("abstract", "")
        cleaned = strip_jats(ab)
        if cleaned:
            return cleaned, None
        return None, "no abstract field"
    except requests.exceptions.RequestException as e:
        return None, f"{type(e).__name__}: {e}"
    except ValueError as e:
        return None, f"json: {e}"


def fetch_openalex(doi, ua, email, timeout):
    url = f"https://api.openalex.org/works/doi:{quote(doi, safe='/.')}"
    try:
        r = requests.get(url, headers={"User-Agent": ua},
                         params={"mailto": email}, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        inv = r.json().get("abstract_inverted_index")
        if not inv:
            return None, "no abstract_inverted_index"
        positions = []
        for word, idxs in inv.items():
            for i in idxs:
                positions.append((i, word))
        positions.sort()
        text = " ".join(w for _, w in positions).strip()
        return (text, None) if text else (None, "empty after reconstruction")
    except requests.exceptions.RequestException as e:
        return None, f"{type(e).__name__}: {e}"
    except ValueError as e:
        return None, f"json: {e}"


def fetch_europepmc(doi, ua, timeout):
    url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    params = {"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"}
    try:
        r = requests.get(url, headers={"User-Agent": ua},
                         params=params, timeout=timeout)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}"
        results = r.json().get("resultList", {}).get("result", [])
        if not results:
            return None, "no result"
        ab = (results[0].get("abstractText") or "").strip()
        return (ab, None) if ab else (None, "no abstractText")
    except requests.exceptions.RequestException as e:
        return None, f"{type(e).__name__}: {e}"
    except ValueError as e:
        return None, f"json: {e}"


SOURCE_FUNCS = {
    "crossref": lambda doi, ctx: fetch_crossref(doi, ctx["ua"], ctx["timeout"]),
    "openalex": lambda doi, ctx: fetch_openalex(doi, ctx["ua"], ctx["email"], ctx["timeout"]),
    "europepmc": lambda doi, ctx: fetch_europepmc(doi, ctx["ua"], ctx["timeout"]),
}

# ---------------------------------------------------------------------------
# Input / output
# ---------------------------------------------------------------------------

def load_targets(input_glob):
    """Return list of {doi, title, venue} for records with DOI but no abstract."""
    files = sorted(glob.glob(input_glob))
    if not files:
        # Maybe user passed a single file path that glob doesn't expand
        if os.path.exists(input_glob):
            files = [input_glob]
        else:
            sys.exit(f"No files matched: {input_glob}")
    targets = []
    seen_dois = set()
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            try:
                records = json.load(f)
            except json.JSONDecodeError as e:
                print(f"WARN: {fp} is not valid JSON: {e}", file=sys.stderr)
                continue
        for r in records:
            doi = (r.get("DOI") or "").strip()
            ab = (r.get("abstract") or "").strip()
            if not doi or ab:
                continue
            key = doi.lower()
            if key in seen_dois:
                continue
            seen_dois.add(key)
            targets.append({
                "doi": doi,
                "title": r.get("title", "") or "",
                "venue": r.get("container-title", "") or "",
                "zotero_id": r.get("id", "") or "",
            })
    return targets, files


def load_processed(output_path):
    """Return set of DOIs (lowercased) already in output JSONL."""
    if not os.path.exists(output_path):
        return set()
    done = set()
    with open(output_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if "doi" in rec:
                    done.add(rec["doi"].lower())
            except json.JSONDecodeError:
                continue
    return done

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def process_one(target, sources, strategy, ctx, sleep_s, verbose):
    doi = target["doi"]
    errors = {}
    candidates = []   # list of (source, abstract)
    for src in sources:
        ab, err = SOURCE_FUNCS[src](doi, ctx)
        if ab:
            candidates.append((src, ab))
            if strategy == "first-hit":
                break
        else:
            errors[src] = err
        time.sleep(sleep_s)
    if candidates:
        if strategy == "longest":
            src, ab = max(candidates, key=lambda x: len(x[1]))
        else:
            src, ab = candidates[0]
        result = {
            "doi": doi,
            "source": src,
            "abstract": ab,
            "char_len": len(ab),
            "errors": errors,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if verbose:
            print(f"  HIT  [{src}] {doi}  ({len(ab)} chars)", file=sys.stderr)
    else:
        result = {
            "doi": doi,
            "source": None,
            "abstract": None,
            "char_len": 0,
            "errors": errors,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        if verbose:
            print(f"  miss     {doi}  ({errors})", file=sys.stderr)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, help="Glob or path to CSL JSON file(s)")
    p.add_argument("--output", required=True, help="Output JSONL (append-only, resumable)")
    p.add_argument("--email", required=True, help="Contact email for Crossref/OpenAlex polite pool")
    p.add_argument("--sources", default="crossref,openalex,europepmc",
                   help="Comma-separated source order (default: crossref,openalex,europepmc)")
    p.add_argument("--strategy", choices=["first-hit", "longest"], default="first-hit",
                   help="first-hit stops on first source that returns; longest queries all and picks longest")
    p.add_argument("--limit", type=int, default=None, help="Stop after N records (for testing)")
    p.add_argument("--sleep", type=float, default=0.5, help="Seconds between API calls (default 0.5)")
    p.add_argument("--timeout", type=float, default=20.0, help="Per-request timeout in seconds")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    sources = [s.strip().lower() for s in args.sources.split(",") if s.strip()]
    for s in sources:
        if s not in SOURCE_FUNCS:
            sys.exit(f"Unknown source: {s}. Valid: {list(SOURCE_FUNCS)}")

    ua = f"abstract-recovery/0.1 (mailto:{args.email})"
    ctx = {"ua": ua, "email": args.email, "timeout": args.timeout}

    print("Loading input ...", file=sys.stderr)
    targets, files = load_targets(args.input)
    print(f"  Files: {len(files)}", file=sys.stderr)
    print(f"  Records missing abstracts (with DOI): {len(targets)}", file=sys.stderr)

    done = load_processed(args.output)
    print(f"  Already processed in {args.output}: {len(done)}", file=sys.stderr)

    todo = [t for t in targets if t["doi"].lower() not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"  To process this run: {len(todo)}", file=sys.stderr)
    print(f"  Sources: {sources}  Strategy: {args.strategy}  Sleep: {args.sleep}s",
          file=sys.stderr)

    if not todo:
        print("Nothing to do.", file=sys.stderr)
        return

    counts = {"hit": 0, "miss": 0}
    per_source = {s: 0 for s in sources}
    started = time.time()

    with open(args.output, "a", encoding="utf-8") as out:
        for i, target in enumerate(todo, 1):
            if i == 1 or i % 25 == 0 or i == len(todo):
                elapsed = time.time() - started
                rate = i / elapsed if elapsed > 0 else 0
                eta = (len(todo) - i) / rate if rate > 0 else 0
                print(f"[{i}/{len(todo)}]  hits={counts['hit']}  misses={counts['miss']}  "
                      f"elapsed={int(elapsed)}s  eta={int(eta)}s",
                      file=sys.stderr)
            try:
                result = process_one(target, sources, args.strategy, ctx,
                                     args.sleep, args.verbose)
            except KeyboardInterrupt:
                print("\nInterrupted. Output is safe; rerun to resume.", file=sys.stderr)
                return
            except Exception as e:
                # Never lose the queue. Log and continue.
                result = {
                    "doi": target["doi"],
                    "source": None,
                    "abstract": None,
                    "char_len": 0,
                    "errors": {"unhandled": f"{type(e).__name__}: {e}"},
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                print(f"  UNHANDLED {target['doi']}: {e}", file=sys.stderr)
            out.write(json.dumps(result, ensure_ascii=False) + "\n")
            out.flush()
            if result["abstract"]:
                counts["hit"] += 1
                per_source[result["source"]] = per_source.get(result["source"], 0) + 1
            else:
                counts["miss"] += 1

    print("\n" + "=" * 60, file=sys.stderr)
    print(f"Done. Processed {len(todo)} records.", file=sys.stderr)
    print(f"  Hits:   {counts['hit']:5d}  ({100*counts['hit']/len(todo):.1f}%)",
          file=sys.stderr)
    print(f"  Misses: {counts['miss']:5d}  ({100*counts['miss']/len(todo):.1f}%)",
          file=sys.stderr)
    print("  Per source (hit count):", file=sys.stderr)
    for s, n in per_source.items():
        print(f"    {s:12s} {n}", file=sys.stderr)
    print(f"\nOutput: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
