"""A4 RAG ingest + search 返回 source 单测。

注意：该测试依赖百炼 embedding API（text-embedding-v3）真实 HTTP 调用 + Chroma 容器端口 8001 存活。
若环境不可用（CI offline）或 API 报错，测试标记 skip。
"""
import asyncio
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, patch

import pytest


KNOWLEDGE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "knowledge"))


@pytest.fixture(name="rag_ns")
async def _ns():
    # 用独立 namespace 避免污染正式数据
    ns = "test_ingest_" + os.urandom(4).hex()
    yield ns
    # cleanup: reset namespace
    try:
        from app.rag.store import rag_store
        rag_store.reset_namespace(ns)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_ingest_then_search_sources(rag_ns):
    if not os.path.isdir(KNOWLEDGE_DIR):
        pytest.skip(f"知识库目录不存在：{KNOWLEDGE_DIR}")
    mds = [f for f in os.listdir(KNOWLEDGE_DIR) if f.endswith(".md")]
    if len(mds) < 3:
        pytest.skip(f"语料不足，期望 ≥ 3 实际 {mds}")

    from app.rag.cli_ingest import do_ingest
    try:
        count = await do_ingest(dir=KNOWLEDGE_DIR, chunk=200, overlap=30, reset=True, namespace=rag_ns)
    except Exception as e:  # 可能百炼 embedding 失败或 Chroma 不可达
        pytest.skip(f"ingest 失败（可能离线或 API 不可达）: {type(e).__name__}: {e}")
        return
    assert count >= 10, f"ingest 文档 chunks 数应 ≥ 10 实际 {count}"

    from app.rag.service import rag_service
    r1 = await rag_service.search("重度抑郁有哪些症状", top_k=3)
    # 至少 1 条包含 ccmd3_summary.md
    srcs = [x.get("source", "") for x in r1]
    assert any("ccmd3_summary.md" in s for s in srcs), f"抑郁症状搜索应命中 ccmd3_summary.md 实际 sources={srcs}"
    # 每条都有非空 source 和 chunk_id:int
    for x in r1:
        assert isinstance(x.get("source"), str) and len(x["source"]) > 0
        assert isinstance(x.get("chunk_id"), int)

    # 腹式呼吸 -> cbt_intro.md
    r2 = await rag_service.search("什么是4-2-6腹式呼吸法", top_k=3)
    srcs2 = [x.get("source", "") for x in r2]
    assert any("cbt_intro.md" in s for s in srcs2), f"腹式呼吸应命中 cbt_intro.md 实际 sources={srcs2}"
