import json
with open("rescued_abstracts_pdf.jsonl") as f:
    for line in f:
        r = json.loads(line)
        if not r["accepted"]:
            print(f"DOI: {r['doi']}  reason: {r['reason']}  chars: {r['char_len']}")
            print(f"  PDF: {r['pdf_abs_path']}")
            ab = (r.get('abstract') or '')[:400]
            print(f"  Preview: {ab}")
            print()
