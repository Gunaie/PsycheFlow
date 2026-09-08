import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

import app.rag.service as rag_service_mod
from app.rag.service import RAGService, chunk_text


class TestChunkText(unittest.TestCase):
    def test_long_paragraphs_split_by_blank_line(self):
        """段落 ≥40 字各自成片（过短才会被合并）。"""
        p1 = "第一段内容足够长了啊哈。" * 4
        p2 = "第二段内容也足够长了啊哈。" * 4
        chunks = chunk_text(f"{p1}\n\n{p2}")
        self.assertEqual(len(chunks), 2)

    def test_short_paragraphs_merge_together(self):
        """过短段落并入相邻内容，避免碎片化切片。"""
        text = "第一段内容足够长了啊哈。\n\n第二段内容也足够长了啊哈。"
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertIn("第一段", chunks[0])
        self.assertIn("第二段", chunks[0])

    def test_header_becomes_section_prefix_not_standalone_chunk(self):
        """Markdown 标题不单独成片，而是给正文加【章节】前缀（有头）。"""
        body = "这是正文段落，讲述热线接线员的基本素质与要求。" * 2
        text = f"## 心理援助热线的基本定位\n\n{body}"
        chunks = chunk_text(text)
        self.assertEqual(len(chunks), 1)
        self.assertTrue(chunks[0].startswith("【心理援助热线的基本定位】"))
        self.assertIn("正文段落", chunks[0])

    def test_oversize_paragraph_splits_at_sentence_boundary(self):
        """超长段落按句子边界二次切分，每片以完整句收尾（有尾）。"""
        body = "这是第一句话，讲述情绪管理的方法。" * 60  # 1020 字 > 400
        chunks = chunk_text(body)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 400)
            self.assertTrue(c.endswith("。"), f"切片未以句号收尾: ...{c[-20:]}")

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
        # 重建前清空旧集合，防止切片规则变更后旧片残留污染检索
        store.reset_namespace.assert_called_once()
        llm.embed.assert_awaited_once_with(["段一", "段二"])
        store.upsert.assert_called_once()
        kw = store.upsert.call_args.kwargs
        self.assertEqual(kw["ids"], ["a#0", "a#1"])
        self.assertEqual(kw["documents"], ["段一", "段二"])
        self.assertEqual(kw["embeddings"], [[0.1], [0.2]])
        self.assertEqual(kw["metadatas"], [{"source": "a.txt", "tags": []}, {"source": "a.txt", "tags": []}])

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
            "metadatas": [[{"source": "a.txt", "chunk_id": 0}, {"source": "b.txt", "chunk_id": 0}]],
            "distances": [[0.1, 0.2]],
            "ids": [["a.txt#0", "b.txt#0"]],
        }
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.5]])

        svc = RAGService(store=store, llm=llm)
        results = await svc.search("焦虑", top_k=2)

        llm.embed.assert_awaited_once_with(["焦虑"])
        # 混合检索：向量侧取 top_k*3 候选用于融合
        store.query.assert_called_once_with([0.5], top_k=6)
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

    async def test_search_bm25_only_hit_uses_virtual_distance(self):
        # BM25 独有命中（向量未召回）→ 虚拟距离 0.72 可过 0.75 阈值
        # 注意：4 篇文档中仅 1 篇含查询词，保证 BM25 IDF 为正（2 篇小语料会退化为 idf=0）
        store = MagicMock()
        store.collection.get.return_value = {
            "documents": [
                "第一个语料段落讲的是日常作息安排。",
                "第二个语料段落讲的是营养搭配建议。",
                "第三个语料段落介绍运动习惯的养成。",
                "第四个语料段落记录情绪变化轨迹。",
            ],
            "metadatas": [
                {"source": "a.txt", "chunk_id": 0},
                {"source": "b.txt", "chunk_id": 0},
                {"source": "c.txt", "chunk_id": 0},
                {"source": "d.txt", "chunk_id": 0},
            ],
            "ids": ["a.txt#0", "b.txt#0", "c.txt#0", "d.txt#0"],
        }
        store.query.return_value = {
            "documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]],
        }
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.5]])

        svc = RAGService(store=store, llm=llm)
        results = await svc.search("作息", top_k=2)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "a.txt")
        self.assertEqual(results[0]["distance"], 0.72)

    async def test_search_bm25_weak_hits_filtered(self):
        """BM25 弱命中（得分 < 0.7*最高分）被自适应过滤，不推弱相关卡片。

        构造：a 短文档强命中"作息"；b 超长文档仅提一次"作息"（长度归一化后得分远低），
        另有 6 篇无关文档（rank_bm25 IDF=ln((N-n+0.5)/(n+0.5))，N=4/n=2 恰好为 0，
        须 N≥5 才能两个命中都拿到正分），旧逻辑两者都返回，新逻辑只保留强命中 a。
        """
        filler = "这句话是无关的填充内容。" * 30
        docs = [
            "日常作息安排规律。",                      # 强命中（短文档）
            f"作息偶尔被提及。{filler}",               # 弱命中（超长文档稀释得分）
            "第三个语料段落讲的是营养搭配建议。",
            "第四个语料段落介绍运动习惯的养成。",
            "第五个语料段落记录情绪变化轨迹。",
            "第六个语料段落讨论人际交往边界。",
            "第七个语料段落说明睡眠卫生要点。",
            "第八个语料段落科普考试焦虑调适。",
        ]
        store = MagicMock()
        store.collection.get.return_value = {
            "documents": docs,
            "metadatas": [{"source": f"{i}.txt", "chunk_id": 0} for i in "abcdefgh"],
            "ids": [f"{i}.txt#0" for i in "abcdefgh"],
        }
        store.query.return_value = {
            "documents": [[]], "metadatas": [[]], "distances": [[]], "ids": [[]],
        }
        llm = MagicMock()
        llm.embed = AsyncMock(return_value=[[0.5]])

        svc = RAGService(store=store, llm=llm)
        results = await svc.search("作息", top_k=3)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["source"], "a.txt")


class TestLoadCorpus(unittest.TestCase):
    def test_load_real_files(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "a.txt"), "w", encoding="utf-8") as f:
                f.write("第一段足够长的内容写在这里。\n\n第二段也足够长的内容写在这里。")
            with open(os.path.join(d, "b.txt"), "w", encoding="utf-8") as f:
                f.write("单独一段足够长的内容写在这里啊。")
            from app.rag.service import load_corpus
            docs = load_corpus(d)
        # a.txt 两个过短段落合并为 1 片 + b.txt 1 片
        self.assertEqual(len(docs), 2)
        self.assertEqual(docs[0]["source"], "a.txt")
        self.assertEqual(docs[0]["id"], "a.txt#0")
        self.assertEqual(docs[1]["source"], "b.txt")


if __name__ == "__main__":
    unittest.main()
