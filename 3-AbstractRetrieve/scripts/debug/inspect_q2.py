import json
with open("quarantine.jsonl") as f:
    for line in f:
        q = json.loads(line)
        v = q.get("verdict", {})
        if not isinstance(v, dict): continue
        flags = [k for k in v.keys() if k.startswith("_")]
        rid_tail = v.get("record_id","")[-12:]
        cat = v.get("primary", {}).get("category", "?")
        print(f"  rid=...{rid_tail}  cat={cat:25s}  flags={flags}")
