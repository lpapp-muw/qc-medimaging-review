import json
with open("quarantine.jsonl") as f:
    for line in f:
        try:
            q = json.loads(line)
            v = q.get("verdict", {})
            if isinstance(v, dict) and "primary" in v:
                print(f"  Cat: {v['primary'].get('category')}  conf: {v.get('confidence')}")
                print(f"  Reason: {q.get('errors')}")
                print(f"  Record: {v.get('record_id','')[-12:]}")
                print(f"  Quote attempted: {v['primary'].get('evidence_quote','')[:150]}")
                print()
        except Exception:
            print(f"  Parse-error line")
            print()
