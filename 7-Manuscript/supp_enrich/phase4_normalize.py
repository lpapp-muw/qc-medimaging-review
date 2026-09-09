# -*- coding: utf-8 -*-
# Phase 4.3a - supplement normalizer.
#
# Reads enrich_inputs.jsonl (from phase4_filter.py) and, for every kept file,
# produces the text and images the enrich-from-supp subagent will read:
#   PDF  (si_document): page text + each page rasterized to PNG (captures circuit
#                       figures, including vector ones).
#   DOCX (si_document): document text + embedded raster figures from word/media
#                       (EMF/WMF vector figures are flagged, not lost silently).
#   .doc (legacy):      flagged unconvertible (pure-Python cannot read it).
#   ipynb:              markdown + code cell text + text/plain outputs, and any
#                       image/png cell outputs saved as figures.
#   config/readme/csv:  raw text (size-capped).
#
# Output per paper under out_dir/<stable_name>/: text/<src>.txt, images/<src>_pN.png,
# and index.json listing what was produced (the harness assembles + caps from it).
#
# Deterministic. Resumable: skips a source whose outputs already exist.
# Python 3.8 strict: .format() only, no f-strings, no PEP-604/585 unions.
# Dependency: pymupdf (import fitz).
#
# Usage:
#   python phase4_normalize.py --inputs out/enrich_inputs.jsonl --corpus supp_corpus --out supp_norm
#   python phase4_normalize.py --selftest

import sys
import os
import re
import csv
import json
import base64
import zipfile
import argparse

try:
    import fitz  # pymupdf
except Exception:
    fitz = None

RASTER_SCALE = 2.0        # ~144 DPI
MAX_PDF_PAGES = 60        # per PDF safety cap
MAX_TEXT_CHARS = 400000   # per source-file text cap
RASTER_MEDIA_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff")
VECTOR_MEDIA_EXT = (".emf", ".wmf")


def safe(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")[:100] or "src"


def write_text(path, text):
    with open(path, "w", errors="replace") as fh:
        fh.write(text[:MAX_TEXT_CHARS])


def norm_pdf(path, tdir, idir, src):
    doc = fitz.open(path)
    n = doc.page_count
    texts = []
    imgs = []
    flags = []
    pages = min(n, MAX_PDF_PAGES)
    for i in range(pages):
        page = doc.load_page(i)
        texts.append(page.get_text("text"))
        pix = page.get_pixmap(matrix=fitz.Matrix(RASTER_SCALE, RASTER_SCALE))
        ip = os.path.join(idir, "{}_p{}.png".format(src, i + 1))
        pix.save(ip)
        imgs.append(os.path.basename(ip))
    doc.close()
    if n > MAX_PDF_PAGES:
        flags.append("pdf_page_cap:{}>{}".format(n, MAX_PDF_PAGES))
    tf = os.path.join(tdir, src + ".txt")
    write_text(tf, "\n".join(texts))
    return os.path.basename(tf), imgs, flags


def norm_docx(path, tdir, idir, src):
    flags = []
    imgs = []
    try:
        z = zipfile.ZipFile(path)
    except Exception:
        return None, [], ["docx_not_a_zip"]
    # text
    text = ""
    try:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
        parts = re.split(r"</w:p>", xml)
        lines = []
        for seg in parts:
            frag = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", seg, flags=re.S))
            frag = frag.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
            if frag.strip():
                lines.append(frag)
        text = "\n".join(lines)
    except Exception as e:
        flags.append("docx_text_error:{}".format(type(e).__name__))
    # embedded figures
    for nm in z.namelist():
        if not nm.startswith("word/media/"):
            continue
        low = nm.lower()
        if low.endswith(RASTER_MEDIA_EXT):
            data = z.read(nm)
            ip = os.path.join(idir, "{}_{}".format(src, safe(os.path.basename(nm))))
            with open(ip, "wb") as fh:
                fh.write(data)
            imgs.append(os.path.basename(ip))
        elif low.endswith(VECTOR_MEDIA_EXT):
            flags.append("vector_figure_unrendered:{}".format(os.path.basename(nm)))
    tf = os.path.join(tdir, src + ".txt")
    write_text(tf, text)
    return os.path.basename(tf), imgs, flags


def norm_ipynb(path, tdir, idir, src, skip_images=False):
    flags = []
    imgs = []
    try:
        nb = json.load(open(path, "r", errors="replace"))
    except Exception as e:
        return None, [], ["ipynb_parse_error:{}".format(type(e).__name__)]
    parts = []
    k = 0
    for cell in nb.get("cells", []):
        ct = cell.get("cell_type")
        srclines = cell.get("source", [])
        if isinstance(srclines, list):
            srclines = "".join(srclines)
        if ct in ("markdown", "code") and srclines.strip():
            parts.append("[{} cell]\n{}".format(ct, srclines))
        for out in cell.get("outputs", []) or []:
            data = out.get("data", {}) or {}
            txt = data.get("text/plain")
            if txt:
                parts.append("[output]\n" + ("".join(txt) if isinstance(txt, list) else str(txt)))
            png = data.get("image/png")
            if png and not skip_images:
                try:
                    b = base64.b64decode(png if isinstance(png, str) else "".join(png))
                    ip = os.path.join(idir, "{}_out{}.png".format(src, k))
                    with open(ip, "wb") as fh:
                        fh.write(b)
                    imgs.append(os.path.basename(ip))
                    k += 1
                except Exception:
                    flags.append("ipynb_png_decode_fail")
    tf = os.path.join(tdir, src + ".txt")
    write_text(tf, "\n\n".join(parts))
    return os.path.basename(tf), imgs, flags


def norm_raw(path, tdir, src):
    try:
        with open(path, "r", errors="replace") as fh:
            text = fh.read()
    except Exception as e:
        return None, [], ["raw_read_error:{}".format(type(e).__name__)]
    tf = os.path.join(tdir, src + ".txt")
    write_text(tf, text)
    return os.path.basename(tf), [], []


def run(a):
    if fitz is None:
        sys.exit("pymupdf not installed: pip install pymupdf (in the .venv)")
    if not os.path.isdir(a.out):
        os.makedirs(a.out)
    rep_rows = []
    man_fh = open(os.path.join(a.out, "normalized_manifest.jsonl"), "w")
    n_papers = 0
    all_flags = []
    for line in open(a.inputs):
        if not line.strip():
            continue
        rec = json.loads(line)
        sn = rec["stable_name"]
        pdir = os.path.join(a.out, sn)
        tdir = os.path.join(pdir, "text")
        idir = os.path.join(pdir, "images")
        for d in (tdir, idir):
            if not os.path.isdir(d):
                os.makedirs(d)
        entries = []
        used = {}
        for f in rec.get("inputs", []):
            rel = f["rel_path"]
            full = os.path.join(a.corpus, sn, rel)
            if not os.path.exists(full):
                entries.append({"source": rel, "status": "missing_on_disk"})
                all_flags.append((sn, "missing:" + rel))
                continue
            base_src = safe(os.path.basename(rel))
            used[base_src] = used.get(base_src, 0) + 1
            src = base_src if used[base_src] == 1 else "{}__{}".format(base_src, used[base_src])
            low = rel.lower()
            if low.endswith(".pdf"):
                tf, imgs, flags = norm_pdf(full, tdir, idir, src)
            elif low.endswith(".docx"):
                tf, imgs, flags = norm_docx(full, tdir, idir, src)
            elif low.endswith(".doc"):
                tf, imgs, flags = None, [], ["legacy_doc_unconvertible (save as .docx in Word)"]
            elif low.endswith(".ipynb"):
                tf, imgs, flags = norm_ipynb(full, tdir, idir, src, getattr(a, "skip_notebook_images", False))
            else:
                tf, imgs, flags = norm_raw(full, tdir, src)
            entries.append({"source": rel, "keep_reason": f.get("keep_reason"),
                            "text_file": tf, "image_files": imgs, "flags": flags})
            for fl in flags:
                all_flags.append((sn, fl))
        with open(os.path.join(pdir, "index.json"), "w") as fh:
            json.dump({"stable_name": sn, "doi": rec.get("doi", ""), "entries": entries}, fh, ensure_ascii=False, indent=1)
        n_text = sum(1 for e in entries if e.get("text_file"))
        n_img = sum(len(e.get("image_files", [])) for e in entries)
        rep_rows.append({"stable_name": sn, "sources": len(entries),
                         "text_files": n_text, "images": n_img,
                         "flags": sum(1 for e in entries for _ in e.get("flags", []))})
        man_fh.write(json.dumps({"stable_name": sn, "entries": entries}, ensure_ascii=False) + "\n")
        n_papers += 1
    man_fh.close()
    with open(os.path.join(a.out, "normalize_report.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stable_name", "sources", "text_files", "images", "flags"])
        w.writeheader()
        for r in rep_rows:
            w.writerow(r)
    print("papers normalized: {}".format(n_papers))
    print("total images rendered/extracted: {}".format(sum(r["images"] for r in rep_rows)))
    if all_flags:
        print("flags ({}):".format(len(all_flags)))
        from collections import Counter
        for k, c in Counter(fl.split(":")[0].split(" ")[0] for _, fl in all_flags).most_common():
            print("  {:4d}  {}".format(c, k))
        for sn, fl in all_flags:
            if "legacy_doc" in fl or "vector_figure" in fl or "missing" in fl:
                print("  ATTENTION {} -> {}".format(sn, fl))
    print("wrote per-paper text/images + index.json under {}/".format(a.out))


def selftest():
    import tempfile
    d = tempfile.mkdtemp()
    corpus = os.path.join(d, "supp_corpus", "p1", "_chrome_si")
    os.makedirs(corpus)
    # a real PDF via fitz, with text + a drawn line (vector, proves rasterization)
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 72), "n_qubits shown in circuit below")
    pg.draw_line((72, 100), (300, 100))
    doc.save(os.path.join(corpus, "supp.pdf"))
    doc.close()
    # a minimal docx zip: document.xml + one png in media
    png1x1 = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
    dx = os.path.join(corpus, "mmc1.docx")
    with zipfile.ZipFile(dx, "w") as z:
        z.writestr("word/document.xml",
                   '<w:document xmlns:w="x"><w:body><w:p><w:r><w:t>baseline reached 0.88 AUC</w:t></w:r></w:p></w:body></w:document>')
        z.writestr("word/media/image1.png", png1x1)
    # an ipynb with code + a png output
    nb = {"cells": [
        {"cell_type": "code", "source": ["n_qubits = 8\n"], "outputs": [
            {"data": {"text/plain": ["0.9"], "image/png": base64.b64encode(png1x1).decode()}}]}]}
    with open(os.path.join(corpus, "run.ipynb"), "w") as fh:
        json.dump(nb, fh)
    # config
    with open(os.path.join(corpus, "config.yaml"), "w") as fh:
        fh.write("shots: 2048\n")
    inputs = os.path.join(d, "inp.jsonl")
    with open(inputs, "w") as fh:
        fh.write(json.dumps({"stable_name": "p1", "doi": "10/x", "inputs": [
            {"rel_path": "_chrome_si/supp.pdf", "type": "pdf", "keep_reason": "si_document"},
            {"rel_path": "_chrome_si/mmc1.docx", "type": "document", "keep_reason": "si_document"},
            {"rel_path": "_chrome_si/run.ipynb", "type": "code", "keep_reason": "notebook"},
            {"rel_path": "_chrome_si/config.yaml", "type": "code", "keep_reason": "config"},
        ]}) + "\n")

    class A:
        pass
    A.inputs = inputs
    A.corpus = os.path.join(d, "supp_corpus")
    A.out = os.path.join(d, "supp_norm")
    run(A)
    idx = json.load(open(os.path.join(A.out, "p1", "index.json")))
    by = {os.path.basename(e["source"]): e for e in idx["entries"]}
    # pdf: text extracted + at least one page image
    assert by["supp.pdf"]["text_file"], by
    assert len(by["supp.pdf"]["image_files"]) >= 1, "pdf must rasterize a page image"
    # pdf text contains the inserted words
    ptxt = open(os.path.join(A.out, "p1", "text", by["supp.pdf"]["text_file"])).read()
    assert "n_qubits" in ptxt, ptxt
    # docx: text + embedded png extracted
    assert "0.88 AUC" in open(os.path.join(A.out, "p1", "text", by["mmc1.docx"]["text_file"])).read()
    assert len(by["mmc1.docx"]["image_files"]) == 1, "docx media png must be extracted"
    # ipynb: code text + output png
    itxt = open(os.path.join(A.out, "p1", "text", by["run.ipynb"]["text_file"])).read()
    assert "n_qubits = 8" in itxt, itxt
    assert len(by["run.ipynb"]["image_files"]) == 1, "ipynb output png must be saved"
    # config raw
    assert "shots: 2048" in open(os.path.join(A.out, "p1", "text", by["config.yaml"]["text_file"])).read()
    print("selftest OK: pdf text+raster, docx text+media, ipynb text+output-image, config raw")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs")
    ap.add_argument("--corpus")
    ap.add_argument("--out", default="supp_norm")
    ap.add_argument("--skip-notebook-images", dest="skip_notebook_images", action="store_true",
                    help="Skip notebook output-cell images (training plots/samples); keep notebook text and all SI-document page images.")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return
    if not a.inputs or not a.corpus:
        ap.error("--inputs and --corpus are required")
    run(a)


if __name__ == "__main__":
    main()
