"""
refill_bc.py - Prepare isolated re-extraction manifests for Pass B and Pass C
over the OCR-augmented grounding corpus (Phase 3 of the quote-grounding fix).

This is the analogue of batch_helper_step4.py `prepare`, but:
  - It reads AUGMENTED text (PDFs_step4_text_aug/<stable>.txt) and AUGMENTED
    offsets (PDFs_step4_images/<stable>/page_offsets_aug.json).
  - It writes manifests into pending_step4_bc/<B|C>/ so production
    pending_step4/ and verdicts_pass_*.jsonl are never touched.
  - Each manifest carries a verbatim `_refill_instruction` telling the
    subagent the TEXT now contains OCR of all tables/figures, so a quote
    exists for essentially every reported fact; the `_image_only` escape is
    withdrawn.

Chunking mirrors the production token-budget model (paths_step4): pages are
the unit; a paper is single-chunk when estimated input tokens fit under
CHUNK_TOKEN_CEILING, otherwise split by page ranges with 1-page overlap.

Manifest shape (compatible with the production task shape the subagents read):
  {
    "stable_name", "doi", "pass", "chunk_id",
    "text_path",            # AUGMENTED text
    "char_start", "char_end",
    "images": ["<abs>/page_001.jpg", ...],
    "page_start", "page_end",
    "ocr_source": true,     # augmented corpus is OCR-bearing -> ocr_mode
    "_is_refill": true,
    "_refill_instruction": "<verbatim>",
    "reply_path": "<abs>/pending_step4_bc/<pass>/<stable>__c<cid>.refill.verdict.json"
  }

Usage
-----
  python3 refill_bc.py --pass B
  python3 refill_bc.py --pass C
  python3 refill_bc.py --pass B --only 10_1002_ima_23015     # smoke
  python3 refill_bc.py --pass B --limit 5
  python3 refill_bc.py --pass B --status                      # count manifests

Idempotent: rebuilds the pass directory from scratch (rmtree) unless --keep.

Python 3.8 compatible.
"""

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import paths_step4 as P


REFILL_PENDING = P.EXTRACT_DIR / "pending_step4_bc"
AUG_TEXT_DIR = P.EXTRACT_DIR / "PDFs_step4_text_aug"
IMAGES_ROOT = P.PDF_TEXT_DIR.parent / "PDFs_step4_images"
OFFSETS_AUG_NAME = "page_offsets_aug.json"

PASSES = ("B", "C")


REFILL_INSTRUCTION = (
    "THIS IS AN OCR-AUGMENTED RE-EXTRACTION. The TEXT block now contains, for "
    "every page, the original pdftotext content FOLLOWED BY an [OCR-PAGE-N] "
    "block holding 300-DPI OCR of that page's tables, figures, charts, and "
    "two-column panels. A verbatim substring therefore exists in TEXT for "
    "essentially every value reported anywhere on the page, including results "
    "tables and code/data-availability boxes. RULES: (1) For every field whose "
    "value is a POSITIVE assertion, you MUST locate and copy a verbatim quote "
    "from the TEXT block (search the [OCR-PAGE-N] blocks too). (2) The "
    "`_image_only` escape is WITHDRAWN; do not emit `_image_only` and do not "
    "emit a positive value with `quote: null`. (3) If a fact is genuinely not "
    "reported in the paper, emit the absence value (false / not_reported / "
    "none / none_reported / not_applicable, or an empty list) WITHOUT a quote; "
    "absence is a real, accepted datum. (4) Never invent or paraphrase a quote; "
    "a non-substring quote fails validation. (5) For graded fields "
    "(baseline_rigour_grade, dataset_realism_grade) quote the sentence the "
    "grade rests on. Output schema, chunk_id / pass_id / _meta echoing rules "
    "are UNCHANGED from your normal prompt."
)


# ----------------------------------------------------------------------------
# Loaders
# ----------------------------------------------------------------------------
def load_text_index():
    rows = {}
    if not P.PDF_TEXT_INDEX.exists():
        print("ERROR: missing", P.PDF_TEXT_INDEX, file=sys.stderr)
        return rows
    with open(P.PDF_TEXT_INDEX, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[r["stable_name"]] = r
    return rows


def load_aug_offsets(stable):
    path = IMAGES_ROOT / stable / OFFSETS_AUG_NAME
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


# ----------------------------------------------------------------------------
# Page-range chunk planner (mirrors paths_step4 token-budget model)
# ----------------------------------------------------------------------------
def plan_chunks(pages, ceiling):
    """Greedy page-accumulator under `ceiling` input tokens, 1-page overlap.

    pages: list of {page, char_start, char_end, image}.
    Returns list of chunk dicts:
      {chunk_id, char_start, char_end, page_start, page_end, images}
    """
    if not pages:
        return []

    # Single-chunk fast path
    total_chars = pages[-1]["char_end"] - pages[0]["char_start"]
    if P.estimate_input_tokens(len(pages), total_chars) <= ceiling:
        return [{
            "chunk_id": 0,
            "char_start": pages[0]["char_start"],
            "char_end": pages[-1]["char_end"],
            "page_start": pages[0]["page"],
            "page_end": pages[-1]["page"],
            "images": [p.get("image", "") for p in pages if p.get("image")],
        }]

    chunks = []
    cid = 0
    i = 0
    n = len(pages)
    while i < n:
        group = []
        j = i
        while j < n:
            trial = group + [pages[j]]
            chars = trial[-1]["char_end"] - trial[0]["char_start"]
            if group and P.estimate_input_tokens(len(trial), chars) > ceiling:
                break
            group = trial
            j += 1
        if not group:  # a single page already exceeds ceiling; take it alone
            group = [pages[i]]
            j = i + 1
        chunks.append({
            "chunk_id": cid,
            "char_start": group[0]["char_start"],
            "char_end": group[-1]["char_end"],
            "page_start": group[0]["page"],
            "page_end": group[-1]["page"],
            "images": [p.get("image", "") for p in group if p.get("image")],
        })
        cid += 1
        if j >= n:
            break
        # 1-page overlap: restart one page before the next boundary
        i = max(j - P.CHUNK_PAGE_OVERLAP, i + 1)
    return chunks


def _abs_images(stable, image_names):
    out = []
    base = IMAGES_ROOT / stable
    for nm in image_names:
        if nm:
            out.append(str(base / nm))
    return out


# ----------------------------------------------------------------------------
# Prepare
# ----------------------------------------------------------------------------
def cmd_prepare(args):
    pass_letter = args.pass_.upper()
    if pass_letter not in PASSES:
        print("ERROR: --pass must be B or C", file=sys.stderr)
        return 2

    text_index = load_text_index()
    if not text_index:
        return 2

    stables = list(text_index.keys())
    only = set(s.strip() for s in (args.only or "").split(",") if s.strip())
    if only:
        stables = [s for s in stables if s in only]
    if args.limit:
        stables = stables[: args.limit]

    pdir = REFILL_PENDING / pass_letter
    if pdir.exists() and not args.keep:
        shutil.rmtree(pdir)
    pdir.mkdir(parents=True, exist_ok=True)

    ceiling = P.CHUNK_TOKEN_CEILING
    n_written = 0
    n_missing_aug = 0

    for stable in stables:
        offs = load_aug_offsets(stable)
        if not offs or not offs.get("pages"):
            n_missing_aug += 1
            print("WARNING: no augmented offsets for", stable, file=sys.stderr)
            continue
        aug_text_path = offs.get("text_path") or str(AUG_TEXT_DIR / (stable + ".txt"))
        doi = text_index[stable].get("doi", "")
        chunks = plan_chunks(offs["pages"], ceiling)
        for ch in chunks:
            cid = ch["chunk_id"]
            reply_path = pdir / "{}__c{}.refill.verdict.json".format(stable, cid)
            task = {
                "stable_name": stable,
                "doi": doi,
                "pass": pass_letter,
                "chunk_id": cid,
                "text_path": aug_text_path,
                "char_start": ch["char_start"],
                "char_end": ch["char_end"],
                "images": _abs_images(stable, ch["images"]),
                "page_start": ch["page_start"],
                "page_end": ch["page_end"],
                "ocr_source": True,
                "_is_refill": True,
                "_refill_instruction": REFILL_INSTRUCTION,
                "reply_path": str(reply_path),
            }
            task_path = pdir / "{}__c{}.task.json".format(stable, cid)
            task_path.write_text(json.dumps(task, indent=2), encoding="utf-8")
            n_written += 1

    print("Prepared {} refill manifest(s) for pass {} in {}".format(
        n_written, pass_letter, pdir))
    if n_missing_aug:
        print("Papers with no augmented offsets (run build_augmented_text.py):",
              n_missing_aug, file=sys.stderr)
    return 0


def cmd_status(args):
    print("Pass | tasks | replies | pending")
    for pl in PASSES:
        pdir = REFILL_PENDING / pl
        if not pdir.exists():
            print("  {}  |   -   |    -    |   -    (not prepared)".format(pl))
            continue
        tasks = list(pdir.glob("*.task.json"))
        replies = list(pdir.glob("*.refill.verdict.json"))
        reply_ids = set(p.name.replace(".refill.verdict.json", "") for p in replies)
        pending = sum(
            1 for t in tasks
            if t.name.replace(".task.json", "") not in reply_ids)
        print("  {}  | {:5d} | {:7d} | {:6d}".format(
            pl, len(tasks), len(replies), pending))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Prepare B/C refill manifests.")
    ap.add_argument("--pass", dest="pass_", default="",
                    help="B or C")
    ap.add_argument("--only", default="", help="Comma-separated stable_names.")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--keep", action="store_true",
                    help="Do not rmtree the pass dir before writing.")
    ap.add_argument("--status", action="store_true",
                    help="Report task/reply/pending counts and exit.")
    args = ap.parse_args()

    if args.status:
        return cmd_status(args)
    if not args.pass_:
        print("ERROR: provide --pass B|C (or --status).", file=sys.stderr)
        return 2
    return cmd_prepare(args)


if __name__ == "__main__":
    raise SystemExit(main())
