import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import app.rag.service as rag_service_mod
from app.rag.service import RAGService, chunk_text


class TestChunkText(unittest.TestCase):
    def test_split_by_blank_line(self):
        text = "第一段内容足够长了啊哈。\n\n第二段内容也足够长了啊哈。"
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 2)

    def test_drop_short_fragments(self):
        text = "a\n\n完整的足够长的段落内容在这里。"
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "完整的足够长的段落内容在这里。")


class TestBuildIndex(unittest.IsolatedAsyncioTestCase):
    async def test_build_embeds_and_upserts_all_docs(self):
        store = MagicMock()
        store.count.return_value = 2
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.1], [0.2]])

        docs = [
            {"id": "a#0", "text": "段一", "source": "a.txt"},
            {"id": "a#1", "text": "段二", "source": "a.txt"},
        ]
        orig = rag_service_mod.load_corpus
        rag_service_mod.load_corpus = lambda d: docs
        try:
            svc = RAGService(store=store, llm=llm)
            result = await svc.build_index()
        finally:
            rag_service_mod.load_corpus = orig

        self.assertEqual(result["indexed"], 2)
        self.assertEqual(result["collection_size"], 2)
        llm.embed.assert_awaited_once_with(["段一", "段二"])
        store.upsert.assert_called_once()
        kw = store.upsert.call_args.kwargs
        self.assertEqual(kw["ids"], ["a#0", "a#1"])
        self.assertEqual(kw["documents"], ["段一", "段二"])
        self.assertEqual(kw["embeddings"], [[0.1], [0.2]])
        self.assertEqual(kw["metadatas"], [{"source": "a.txt"}, {"source": "a.txt"}])

    async def test_build_returns_zero_when_no_corpus(self):
        store = MagicMock()
        llm = MagicMock()
        llm.embed = AsyncMock()
        orig = rag_service_mod.load_corpus
        rag_service_mod.load_corpus = lambda d: []
        try:
            svc = RAGService(store=store, llm=llm)
            result = await svc.build_index()
        finally:
            rag_service_mod.load_corpus = orig
        self.assertEqual(result["indexed"], 0)
        llm.embed.assert_not_awaited()
        store.upsert.assert_not_called()


class TestSearch(unittest.IsolatedAsyncioTestCase):
    async def test_search_embeds_query_and_maps_results(self):
        store = MagicMock()
        store.query.return_value = {
            "documents": [["段A", "段B"]],
            "metadatas": [[{"source": "a.txt"}, {"source": "b.txt"}]],
            "distances": [[0.1, 0.2]],
        }
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.5]])

        svc = RAGService(store=store, llm=llm)
        results = await svc.search("焦虑", top_k=2)

        llm.embed.assert_awaited_once_with(["焦虑"])
        store.query.assert_called_once_with([0.5], top_k=2)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["text"], "段A")
        self.assertEqual(results[0]["source"], "a.txt")
        self.assertEqual(results[1]["distance"], 0.2)

    async def test_search_empty_results(self):
        store = MagicMock()
        store.query.return_value = {"documents": [[]], "metadatas": [[]], "distances": [[]]}
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.5]])
        svc = RAGService(store=store, llm=llm)
        results = await svc.search("x")
        self.assertEqual(results, [])


class TestLoadCorpus(unittest.TestCase):
    def test_load_real_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as f:
                f.write("第一段足够长的内容写在这里。\n\n第二段也足够长的内容写在这里。")
            with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as f:
                f.write("单独一段足够长的内容写在这里啊。")
            from app.rag.service import load_corpus
            docs = load_corpus(d)
        self.assertEqual(len(docs), 3)
        self.assertEqual(docs[0]["source"], "a.txt")
        self.assertEqual(docs[0]["id"], "a.txt#0")
        self.assertEqual(docs[2]["source"], "b.txt")


if __name__ == "__main__":
    unittest.main()
