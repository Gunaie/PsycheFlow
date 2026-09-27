# -*- coding: utf-8 -*-
import json, asyncio, aiohttp

async def main():
    with open("scripts/finetune/blind_eval_30.json", "r", encoding="utf-8") as f:
        scenarios = json.load(f)
    results = []
    async with aiohttp.ClientSession() as session:
        for s in scenarios:
            payload = {"message": s["user_message"], "persona_id": "default", "history": []}
            try:
                async with session.post("http://localhost:8000/api/chat", json=payload) as resp:
                    data = await resp.json()
                    reply = data.get("reply", "")
                    results.append({
                        "id": s["id"], "kind": s["kind"], "theme": s["theme"],
                        "user": s["user_message"], "assistant": reply, "status": "ok"
                    })
                    print(f'[{s["id"]}/30] {s["kind"]} {len(reply)}字')
            except Exception as e:
                results.append({"id": s["id"], "error": str(e), "status": "fail"})
                print(f'[{s["id"]}/30] ERROR {e}')
    with open("scripts/finetune/blind_eval_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nDone -> scripts/finetune/blind_eval_results.json")

asyncio.run(main())
