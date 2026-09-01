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


if __name__ == "__main__":
    unittest.main()
