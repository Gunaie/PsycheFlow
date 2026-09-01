"""SSE 首 token 实测脚本（NFR-5 验证）。

用法（容器内）：
    uv run python scripts/sse_first_token.py [--message "最近考试压力大"]

输出：
- 首 token 到达时间（从请求发出到第一个 token 事件）
- 各阶段事件时间（agent/sources/done）
- 完整回复与总耗时

需后端已加载 /api/chat/stream 路由（docker restart psycheflow-backend 后约 10s）。
"""
import argparse
import asyncio
import json
import time

import httpx


def parse_event(block: str):
    """解析单条 SSE 事件块，返回 (event_type, data_dict)。"""
    event = "message"
    data_str = ""
    for line in block.split("\n"):
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            data_str += line[6:]
    try:
        data = json.loads(data_str) if data_str else {}
    except json.JSONDecodeError:
        data = {"raw": data_str}
    return event, data


async def stream_once(base_url: str, message: str, persona: str | None = None):
    """跑一次 SSE 流式对话，打印各事件时间。"""
    t0 = time.time()
    t_first_token = None
    tokens = []
    reply_from_done = ""

    payload = {"message": message}
    if persona:
        payload["persona_id"] = persona

    print(f"[{time.time()-t0:6.2f}s] POST /api/chat/stream  message={message!r}")
    async with httpx.AsyncClient(base_url=base_url, timeout=120) as c:
        async with c.stream("POST", "/api/chat/stream", json=payload) as r:
            if r.status_code != 200:
                print(f"[{time.time()-t0:6.2f}s] HTTP {r.status_code}: {await r.aread()}")
                return
            buf = ""
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    if not block.strip():
                        continue
                    event, data = parse_event(block)
                    elapsed = time.time() - t0
                    if event == "agent":
                        print(f"[{elapsed:6.2f}s] agent: {data.get('agent')}  trace={data.get('agent_trace')}")
                    elif event == "sources":
                        print(f"[{elapsed:6.2f}s] sources: {len(data.get('sources',[]))} 条")
                    elif event == "token":
                        if t_first_token is None:
                            t_first_token = elapsed
                            print(f"[{elapsed:6.2f}s] 首 token!  token={data.get('token')!r}")
                        tokens.append(data.get("token", ""))
                    elif event == "crisis":
                        print(f"[{elapsed:6.2f}s] crisis: reply_len={len(data.get('reply',''))}")
                        reply_from_done = data.get("reply", "")
                    elif event == "done":
                        print(f"[{elapsed:6.2f}s] done  current_agent={data.get('current_agent')} crisis={data.get('crisis')}")
                        reply_from_done = data.get("reply", "")
                    elif event == "error":
                        print(f"[{elapsed:6.2f}s] error: {data.get('message')}")

    elapsed = time.time() - t0
    full_reply = "".join(tokens) or reply_from_done
    print("-" * 60)
    print(f"首 token : {t_first_token:.2f}s" if t_first_token else "首 token : N/A（危机/异常路径无 token）")
    print(f"总耗时   : {elapsed:.2f}s")
    print(f"回复长度 : {len(full_reply)} 字")
    print(f"回复预览 : {full_reply[:120]}{'...' if len(full_reply) > 120 else ''}")


def main():
    p = argparse.ArgumentParser(description="SSE 首 token 实测（NFR-5）")
    p.add_argument("--base-url", default="http://localhost:8000", help="后端 base url")
    p.add_argument("--message", default="最近考试压力大，睡不好", help="测试消息")
    p.add_argument("--persona", default=None, help="persona_id（default/sister/senior/listener）")
    args = p.parse_args()

    asyncio.run(stream_once(args.base_url, args.message, args.persona))


if __name__ == "__main__":
    main()
