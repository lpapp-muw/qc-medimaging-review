import json
with open("quarantine.jsonl") as f:
    for i, line in enumerate(f, 1):
        try:
            q = json.loads(line)
            v = q.get("verdict")
            if not isinstance(v, dict) or "_PARSE_ERROR_" not in v:
                continue
            blob = v["_PARSE_ERROR_"]
            print(f"=== Line {i} ===")
            print(f"  Length: {len(blob)} chars")
            print(f"  First 200: {blob[:200]}")
            print(f"  Last 200:  {blob[-200:]}")
            print()
        except Exception:
            pass
