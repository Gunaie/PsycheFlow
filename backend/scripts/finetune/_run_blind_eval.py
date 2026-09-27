# -*- coding: utf-8 -*-
"""盲评执行：30 场景打 API，结果存 blind_eval_results.json"""
import json
import urllib.request
import time

with open("scripts/finetune/blind_eval_30.json", "r", encoding="utf-8") as f:
    scenarios = json.load(f)

results = []
for s in scenarios:
    payload = {"message": s["user_message"], "persona_id": "default", "history": []}
    req = urllib.request.Request(
        "http://localhost:8000/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
            results.append({
                "id": s["id"], "kind": s["kind"], "theme": s["theme"],
                "user": s["user_message"], "assistant": data.get("reply", ""),
                "status": "ok",
            })
            print(f"[{s['id']}/30] {s['kind']} OK")
    except Exception as e:
        results.append({"id": s["id"], "error": str(e), "status": "fail"})
        print(f"[{s['id']}/30] FAIL: {e}")
    time.sleep(0.3)

with open("scripts/finetune/blind_eval_results.json", "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)

ok = sum(1 for r in results if r["status"] == "ok")
print(f"\nDone: {ok}/30 succeeded -> scripts/finetune/blind_eval_results.json")
