import unittest
from unittest.mock import AsyncMock, MagicMock

from app.core.llm import LLMProvider


class TestModelFor(unittest.TestCase):
    def test_each_role_returns_configured_model(self):
        p = LLMProvider()
        self.assertEqual(p.model_for("intake"), p._settings.model_intake)
        self.assertEqual(p.model_for("dialog"), p._settings.model_dialog)
        self.assertEqual(p.model_for("report"), p._settings.model_report)
        self.assertEqual(p.model_for("embed"), p._settings.model_embed)

    def test_unknown_role_raises(self):
        p = LLMProvider()
        with self.assertRaises(ValueError):
            p.model_for("xxx")


class TestTempFor(unittest.TestCase):
    def test_chat_roles_use_configured_temp(self):
        p = LLMProvider()
        self.assertEqual(p.temp_for("intake"), p._settings.temp_intake)
        self.assertEqual(p.temp_for("dialog"), p._settings.temp_dialog)
        self.assertEqual(p.temp_for("report"), p._settings.temp_report)

    def test_unknown_role_defaults_07(self):
        p = LLMProvider()
        self.assertEqual(p.temp_for("embed"), 0.7)


class TestChat(unittest.IsolatedAsyncioTestCase):
    async def test_chat_uses_role_model_and_temp(self):
        p = LLMProvider()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "pong"
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        p._client = mock_client

        reply = await p.chat("dialog", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "pong")
        mock_client.chat.completions.create.assert_awaited_once()
        kw = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kw["model"], p.model_for("dialog"))
        self.assertEqual(kw["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(kw["temperature"], p.temp_for("dialog"))

    async def test_explicit_temperature_overrides(self):
        p = LLMProvider()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "x"
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        p._client = mock_client

        await p.chat("report", [{"role": "user", "content": "x"}], temperature=0.5)

        kw = mock_client.chat.completions.create.call_args.kwargs
        self.assertEqual(kw["temperature"], 0.5)

    async def test_empty_content_returns_empty_string(self):
        p = LLMProvider()
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = None
        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        p._client = mock_client

        reply = await p.chat("intake", [{"role": "user", "content": "x"}])
        self.assertEqual(reply, "")


class TestEmbed(unittest.IsolatedAsyncioTestCase):
    async def test_embed_returns_vectors_in_order(self):
        p = LLMProvider()
        mock_resp = MagicMock()
        d1 = MagicMock(); d1.embedding = [0.1, 0.2]
        d2 = MagicMock(); d2.embedding = [0.3, 0.4]
        mock_resp.data = [d1, d2]
        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(return_value=mock_resp)
        p._client = mock_client

        vecs = await p.embed(["a", "b"])

        self.assertEqual(vecs, [[0.1, 0.2], [0.3, 0.4]])
        kw = mock_client.embeddings.create.call_args.kwargs
        self.assertEqual(kw["model"], p.model_for("embed"))
        self.assertEqual(kw["input"], ["a", "b"])

    async def test_embed_batches_over_10(self):
        p = LLMProvider()

        def fake_create(*args, **kwargs):
            n = len(kwargs["input"])
            resp = MagicMock()
            resp.data = [MagicMock() for _ in range(n)]
            for d in resp.data:
                d.embedding = [0.1]
            return resp

        mock_client = MagicMock()
        mock_client.embeddings.create = AsyncMock(side_effect=fake_create)
        p._client = mock_client

        texts = [f"t{i}" for i in range(25)]  # 25 条 → 3 批 (10+10+5)
        vecs = await p.embed(texts)

        self.assertEqual(len(vecs), 25)
        self.assertEqual(mock_client.embeddings.create.await_count, 3)
        last_input = mock_client.embeddings.create.call_args_list[-1].kwargs["input"]
        self.assertEqual(len(last_input), 5)


# ============ Ollama 本地兜底单测 ============
# 覆盖：cloud 异常/空回复 → ollama；未启用保持原 cloud-only 行为；stream 起始即失败切 ollama、
# 已部分输出不切（避免拼接错乱）。全部 mock，不依赖真实 Ollama 可达性。


def _fake_settings(ollama_enabled: bool):
    """构造带 ollama 配置的假 settings（避免触碰真实 .env）。"""
    s = MagicMock()
    s.ollama_base_url = "http://ollama:11434/v1" if ollama_enabled else ""
    s.ollama_model = "qwen2.5:7b"
    # role 模型/温度（intake 用于多数 chat 测试，dialog 用于 stream 测试）
    s.model_intake = "intake-m"; s.temp_intake = 0.1
    s.model_dialog = "dialog-m"; s.temp_dialog = 0.35
    s.model_report = "report-m";  s.temp_report = 0.1
    return s


def _chat_client_with_content(content):
    """非流式 client：create 返回 content（None 表示空回复）。"""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    return client


def _chat_client_raising(exc):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=exc)
    return client


def _stream_client_from_chunks(chunks_content):
    """流式 client：create 返回 async gen，依次 yield delta.content=c（None 跳过）。"""
    async def _gen():
        for c in chunks_content:
            chunk = MagicMock()
            chunk.choices = [MagicMock()]
            chunk.choices[0].delta.content = c
            yield chunk
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_gen())
    return client


def _stream_client_raising(exc):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=exc)
    return client


def _stream_client_gen_then_raise(first_content, exc):
    """流式 client：先 yield 一个 chunk 再抛 exc（模拟中途断流）。"""
    async def _gen():
        chunk = MagicMock()
        chunk.choices = [MagicMock()]
        chunk.choices[0].delta.content = first_content
        yield chunk
        raise exc
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=_gen())
    return client


class TestChatOllamaFallback(unittest.IsolatedAsyncioTestCase):
    def _make(self, cloud_client, ollama_client=None, enabled=True):
        p = LLMProvider(_fake_settings(enabled))
        p._client = cloud_client
        if ollama_client is not None:
            p._ollama_client = ollama_client
        return p

    async def test_cloud_exception_falls_back_to_ollama(self):
        cloud = _chat_client_raising(RuntimeError("cloud down"))
        ollama = _chat_client_with_content("ollama-reply")
        p = self._make(cloud, ollama, enabled=True)

        reply = await p.chat("intake", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ollama-reply")
        cloud.chat.completions.create.assert_awaited_once()
        ollama.chat.completions.create.assert_awaited_once()

    async def test_cloud_empty_falls_back_to_ollama(self):
        cloud = _chat_client_with_content(None)
        ollama = _chat_client_with_content("ollama")
        p = self._make(cloud, ollama, enabled=True)

        reply = await p.chat("intake", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "ollama")
        ollama.chat.completions.create.assert_awaited_once()

    async def test_cloud_exception_ollama_disabled_reraises(self):
        # 未启用 Ollama 时，cloud 异常应原样上抛（保持原 cloud-only 行为）
        cloud = _chat_client_raising(RuntimeError("cloud down"))
        p = self._make(cloud, None, enabled=False)

        with self.assertRaises(RuntimeError):
            await p.chat("intake", [{"role": "user", "content": "hi"}])

    async def test_cloud_ok_does_not_call_ollama(self):
        cloud = _chat_client_with_content("cloud")
        ollama = _chat_client_with_content("ollama")
        p = self._make(cloud, ollama, enabled=True)

        reply = await p.chat("intake", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "cloud")
        ollama.chat.completions.create.assert_not_awaited()

    async def test_both_fail_returns_empty(self):
        # cloud 异常 + ollama 也异常 → 返回 ""（节点级硬编码话术兜底）
        cloud = _chat_client_raising(RuntimeError("cloud down"))
        ollama = _chat_client_raising(RuntimeError("ollama down"))
        p = self._make(cloud, ollama, enabled=True)

        reply = await p.chat("intake", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "")

    async def test_cloud_empty_ollama_empty_returns_empty(self):
        cloud = _chat_client_with_content(None)
        ollama = _chat_client_with_content(None)
        p = self._make(cloud, ollama, enabled=True)

        reply = await p.chat("intake", [{"role": "user", "content": "hi"}])

        self.assertEqual(reply, "")


class TestStreamOllamaFallback(unittest.IsolatedAsyncioTestCase):
    def _make(self, cloud_client, ollama_client=None, enabled=True):
        p = LLMProvider(_fake_settings(enabled))
        p._client = cloud_client
        if ollama_client is not None:
            p._ollama_client = ollama_client
        return p

    async def _collect(self, gen):
        out = []
        async for tok in gen:
            out.append(tok)
        return out

    async def test_cloud_start_failure_falls_back_to_ollama(self):
        # stream 起始即失败（未 yield 任何 token）→ 切 ollama 流
        cloud = _stream_client_raising(RuntimeError("cloud stream down"))
        ollama = _stream_client_from_chunks(["a", "b"])
        p = self._make(cloud, ollama, enabled=True)

        tokens = await self._collect(p.stream("dialog", [{"role": "user", "content": "hi"}]))

        self.assertEqual(tokens, ["a", "b"])
        cloud.chat.completions.create.assert_awaited_once()
        ollama.chat.completions.create.assert_awaited_once()

    async def test_cloud_partial_failure_reraises_no_switch(self):
        # 已 yield token 后中途断流 → 不切 ollama（避免拼接错乱），异常上抛
        cloud = _stream_client_gen_then_raise("a", RuntimeError("mid-stream"))
        ollama = _stream_client_from_chunks(["x"])
        p = self._make(cloud, ollama, enabled=True)

        tokens = []
        with self.assertRaises(RuntimeError):
            async for tok in p.stream("dialog", [{"role": "user", "content": "hi"}]):
                tokens.append(tok)

        self.assertEqual(tokens, ["a"])
        ollama.chat.completions.create.assert_not_awaited()

    async def test_cloud_ok_does_not_call_ollama(self):
        cloud = _stream_client_from_chunks(["x"])
        ollama = _stream_client_from_chunks(["y"])
        p = self._make(cloud, ollama, enabled=True)

        tokens = await self._collect(p.stream("dialog", [{"role": "user", "content": "hi"}]))

        self.assertEqual(tokens, ["x"])
        ollama.chat.completions.create.assert_not_awaited()

    async def test_cloud_failure_ollama_disabled_reraises(self):
        cloud = _stream_client_raising(RuntimeError("cloud stream down"))
        p = self._make(cloud, None, enabled=False)

        with self.assertRaises(RuntimeError):
            await self._collect(p.stream("dialog", [{"role": "user", "content": "hi"}]))


if __name__ == "__main__":
    unittest.main()
