# -*- coding: utf-8 -*-
"""匿名 LLM 接口的轻量 IP 限流（方案 A 加固项）。

设计要点：
- 滑动窗口计数，内存实现，无新依赖（不引 slowapi/redis）
- **仅限匿名请求**：登录用户豁免——校园 NAT 下大量学生共享出口 IP，
  按 IP 限流只能针对匿名池，否则会误伤同校登录用户
- 豁免判定基于解析成功的账号（get_current_account 对无效 token 返回 None，
  只看 header 是否存在可被伪造绕过）；FastAPI 同依赖缓存，不产生二次查库
- 键支持 X-Forwarded-For（nginx 反代场景取第一个 IP）
- 桶数量超阈值时清理过期键，防内存无界增长
"""
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.api.deps import get_current_account
from app.models import User

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_MAX_BUCKETS = 10_000


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(name: str, limit: int, window_sec: int):
    """生成限流依赖。name 相同的端点共享同一个桶（如 chat 与 chat/stream）。"""

    async def dep(request: Request, account: User | None = Depends(get_current_account)) -> None:
        if account is not None:
            return  # 登录用户豁免

        key = f"{name}:{_client_ip(request)}"
        now = time.monotonic()
        bucket = _BUCKETS[key]
        while bucket and now - bucket[0] > window_sec:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试（匿名访问有频率限制，登录后不限）",
            )
        bucket.append(now)

        if len(_BUCKETS) > _MAX_BUCKETS:
            stale = [k for k, v in _BUCKETS.items() if not v or now - v[-1] > window_sec]
            for k in stale:
                _BUCKETS.pop(k, None)

    return dep
