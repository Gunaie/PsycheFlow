"""PsycheFlow 对话接口压测（Python，容器内 uv run 执行）。

运行方式：
    docker exec psycheflow-backend uv run python scripts/loadtest/chat_load.py

说明：
- /api/health 高并发（50 VU × 30s），验证 HTTP 栈基线 QPS
- /api/chat 低并发长时（10 VU × 2min），走完整多智能体链路
- 压测前注册学生账号（登录用户豁免 IP 限流）
- 本地 LoRA 推理单轮 ~3-7s，GPU 串行排队属预期；压测关注 P95/错误率
"""
import json
import os
import sys
import time
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE = os.getenv("BASE_URL", "http://localhost:8000")
HEALTH_URL = f"{BASE}/api/health"
CHAT_URL = f"{BASE}/api/chat"
REGISTER_URL = f"{BASE}/api/auth/register"

CHAT_PROMPTS = [
    "我最近压力很大，晚上睡不着",
    "什么是焦虑？",
    "我总是担心考试成绩",
    "和同学闹矛盾了，心情不好",
    "怎样才能放松一点",
]

def _now() -> float:
    return time.perf_counter()


def register_account() -> str:
    """注册压测账号并返回 token（登录用户豁免 IP 限流）。"""
    payload = {
        "consents": {"tool": True, "guardian": True, "privacy14": True, "crisis": True},
        "profile": {"name": "loadtest", "grade": "初三"},
        "role": "student",
    }
    try:
        r = requests.post(REGISTER_URL, json=payload, timeout=10)
    except Exception as e:
        print(f"注册网络失败: {e}")
        sys.exit(1)
    if r.status_code != 200:
        # 幂等：如果已经注册过，尝试登录
        print(f"注册返回 {r.status_code}，尝试直接登录...")
        # 这里无法直接登录（不知道 label），但一次压测只跑一次 setup
        print(f"响应: {r.text[:200]}")
        sys.exit(1)
    return r.json()["token"]


def hit_health() -> tuple[float, int]:
    """GET /api/health，返回 (耗时秒, 状态码)。"""
    start = _now()
    try:
        r = requests.get(HEALTH_URL, timeout=5)
        return (_now() - start, r.status_code)
    except Exception as e:
        return (_now() - start, 0)


def hit_chat(token: str) -> tuple[float, int, bool]:
    """POST /api/chat，返回 (耗时秒, 状态码, 是否有回复)。"""
    prompt = CHAT_PROMPTS[int(_now() * 1000) % len(CHAT_PROMPTS)]
    payload = {"message": prompt, "history": [], "persona_id": "default"}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    start = _now()
    try:
        r = requests.post(CHAT_URL, json=payload, headers=headers, timeout=120)
        dur = _now() - start
        has_reply = False
        if r.status_code == 200:
            try:
                has_reply = bool(r.json().get("reply"))
            except Exception:
                pass
        return (dur, r.status_code, has_reply)
    except Exception as e:
        dur = _now() - start
        return (dur, 0, False)


def run_health_load(vus: int = 50, duration_sec: int = 30) -> dict:
    """高并发 health 端点压测。"""
    print(f"\n[health] {vus} VU × {duration_sec}s 并发压测...")
    results = []
    end_time = _now() + duration_sec
    sent = 0

    with ThreadPoolExecutor(max_workers=vus) as pool:
        futures = []
        while _now() < end_time:
            futures.append(pool.submit(hit_health))
            sent += 1
            if len(futures) >= vus * 2:
                # 控制队列深度
                for f in as_completed(futures[:vus]):
                    results.append(f.result())
                futures = futures[vus:]
        for f in as_completed(futures):
            results.append(f.result())

    ok = [dur for dur, code in results if code == 200]
    codes = [code for _, code in results if code != 200]
    total = len(results)
    qps = total / duration_sec
    print(f"  总请求: {total}, QPS: {qps:.1f}, 200: {len(ok)}, 错误码: {codes[:10]}")
    if ok:
        print(f"  P50: {statistics.median(ok)*1000:.1f}ms, P95: {statistics.quantiles(ok, n=20)[18]*1000:.1f}ms")
    return {
        "endpoint": "health",
        "vus": vus,
        "duration_sec": duration_sec,
        "total": total,
        "qps": qps,
        "ok_count": len(ok),
        "error_codes": codes,
        "latencies_s": ok,
    }


def run_chat_load(token: str, vus: int = 10, duration_sec: int = 120) -> dict:
    """低并发 chat 端点压测（真实 LLM 链路）。"""
    print(f"\n[chat] {vus} VU × {duration_sec}s 并发压测（本地 LoRA，串行推理）...")
    results = []
    end_time = _now() + duration_sec

    with ThreadPoolExecutor(max_workers=vus) as pool:
        futures = []
        while _now() < end_time:
            futures.append(pool.submit(hit_chat, token))
            time.sleep(0.2)  # 控制提交速率，避免瞬间洪峰
            if len(futures) >= vus:
                for f in as_completed(futures[:vus]):
                    results.append(f.result())
                futures = futures[vus:]
        for f in as_completed(futures):
            results.append(f.result())

    ok = [dur for dur, code, has_reply in results if code == 200 and has_reply]
    errors = [(dur, code, has_reply) for dur, code, has_reply in results if code != 200 or not has_reply]
    total = len(results)
    print(f"  总请求: {total}, 成功: {len(ok)}, 失败: {len(errors)}")
    for dur, code, has_reply in errors[:5]:
        print(f"    错误: code={code}, has_reply={has_reply}, dur={dur:.2f}s")
    if ok:
        ok.sort()
        p50 = ok[len(ok) // 2]
        p95 = ok[int(len(ok) * 0.95)]
        print(f"  P50: {p50:.2f}s, P95: {p95:.2f}s, Min: {min(ok):.2f}s, Max: {max(ok):.2f}s")
    return {
        "endpoint": "chat",
        "vus": vus,
        "duration_sec": duration_sec,
        "total": total,
        "ok_count": len(ok),
        "errors": errors,
        "latencies_s": ok,
    }


def main():
    print(f"压测目标: {BASE}")
    token = register_account()
    print("压测账号注册成功，开始压测...")

    # 环境变量控制：SKIP_HEALTH=1 跳过基线；HEALTH_VUS/HEALTH_SEC、CHAT_VUS/CHAT_SEC 调并发与时长
    # 注意：本地单卡 GPU 串行推理，CHAT_VUS 过高会导致排队超 120s timeout（非后端 bug）
    skip_health = os.getenv("SKIP_HEALTH") == "1"
    health_result = None
    if not skip_health:
        health_result = run_health_load(
            vus=int(os.getenv("HEALTH_VUS", "50")),
            duration_sec=int(os.getenv("HEALTH_SEC", "30")),
        )
        time.sleep(2)
    chat_result = run_chat_load(
        token,
        vus=int(os.getenv("CHAT_VUS", "10")),
        duration_sec=int(os.getenv("CHAT_SEC", "120")),
    )

    # 写报告
    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "base_url": BASE,
        "health": health_result,
        "chat": chat_result,
    }
    out_path = "scripts/loadtest/results/latest_load_report.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入: {out_path}")


if __name__ == "__main__":
    main()
