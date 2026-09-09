import json
with open("quarantine.jsonl") as f:
    for i, line in enumerate(f, 1):
        try:
            q = json.loads(line)
            v = q.get("verdict", {})
            if not isinstance(v, dict) or "primary" not in v:
                # parse error or no-record-id entry
                print(f"=== Line {i}: orphan ===")
                print(f"  Content: {json.dumps(q)[:500]}")
                print()
        except Exception as e:
            print(f"=== Line {i}: raw parse error ===")
            print(f"  Raw: {line[:300]}")
            print()
