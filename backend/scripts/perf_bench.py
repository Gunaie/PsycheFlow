# -*- coding: utf-8 -*-
"""性能压测脚本：50 并发 health + 10 并发 chat（受百炼 rate limit 限制）。

在容器内运行（backend/scripts/ 挂载到容器 /app/scripts/，cwd=/app）：
  docker exec psycheflow-backend uv run python scripts/perf_bench.py

或宿主机运行（需 httpx）：
  python backend/scripts/perf_bench.py
"""
import asyncio
import time
import statistics
import sys

import httpx

BASE = "http://localhost:8000"


async def bench_health(n: int = 50):
    """50 并发 /api/health，测 FastAPI 基础并发能力。"""
    print(f"\n[1] /api/health x{n} 并发")
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
        async def one():
            t0 = time.perf_counter()
            r = await c.get("/api/health")
            dt = (time.perf_counter() - t0) * 1000
            return r.status_code == 200, dt
        t0 = time.perf_counter()
        results = await asyncio.gather(*[one() for _ in range(n)])
        total = (time.perf_counter() - t0) * 1000
    oks = [r for r, _ in results]
    lats = [l for _, l in results]
    print(f"  成功率: {sum(oks)}/{n} ({sum(oks)/n*100:.0f}%)")
    print(f"  总耗时: {total:.0f}ms")
    print(f"  平均延迟: {statistics.mean(lats):.1f}ms")
    print(f"  P50: {statistics.median(lats):.1f}ms")
    print(f"  P95: {sorted(lats)[int(n*0.95)]:.1f}ms")
    print(f"  QPS: {n / total * 1000:.1f}")
    return sum(oks) == n


async def bench_chat(n: int = 10):
    """10 并发 /api/chat，测 LangGraph 编排链路（受百炼 rate limit）。"""
    print(f"\n[2] /api/chat x{n} 并发（LLM 编排链路）")
    msg = "我最近心情不太好"
    async with httpx.AsyncClient(base_url=BASE, timeout=120) as c:
        async def one(i: int):
            t0 = time.perf_counter()
            try:
                r = await c.post("/api/chat", json={
                    "message": msg,
                    "history": [],
                    "session_id": f"perf-sid-{i:03d}",
                    "account_id": f"perf-acc-{i:03d}",
                })
                dt = (time.perf_counter() - t0) * 1000
                ok = r.status_code == 200
                if ok:
                    body = r.json()
                    print(f"  [{i:02d}] {dt:.0f}ms agent={body.get('current_agent','?')} trace={body.get('agent_trace',[])} crisis={body.get('crisis')}")
                else:
                    print(f"  [{i:02d}] FAIL {r.status_code} {r.text[:80]}")
                return ok, dt
            except Exception as e:
                dt = (time.perf_counter() - t0) * 1000
                print(f"  [{i:02d}] ERROR {type(e).__name__}: {str(e)[:80]}")
                return False, dt
        t0 = time.perf_counter()
        results = await asyncio.gather(*[one(i) for i in range(n)])
        total = (time.perf_counter() - t0)
    oks = [r for r, _ in results]
    lats = [l for _, l in results]
    ok_count = sum(oks)
    print(f"\n  成功率: {ok_count}/{n} ({ok_count/n*100:.0f}%)")
    if lats:
        print(f"  平均延迟: {statistics.mean(lats):.0f}ms")
        print(f"  P50: {statistics.median(lats):.0f}ms")
        if ok_count > 2:
            ok_lats = [l for ok, l in results if ok]
            print(f"  成功请求平均: {statistics.mean(ok_lats):.0f}ms")
    print(f"  总耗时: {total:.1f}s")
    return ok_count >= n * 0.8  # 80% 成功率算 PASS（LLM 可能有 rate limit）


async def main():
    print("=" * 60)
    print("PsycheFlow 性能压测")
    print("=" * 60)

    ok1 = await bench_health(50)
    ok2 = await bench_chat(10)

    print("\n" + "=" * 60)
    print(f"总结: health={'PASS' if ok1 else 'FAIL'} | chat={'PASS' if ok2 else 'FAIL'}")
    print("=" * 60)
    sys.exit(0 if (ok1 and ok2) else 1)


asyncio.run(main())
