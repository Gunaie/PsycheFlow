"""RAG 服务：向量化 + 检索 + 索引构建。

知识库语料放 data/knowledge/*.txt，按空行切片，每段一个文档。
向量化用百炼 text-embedding-v3，存入 Chroma。
"""
import glob
import os
import jieba
from rank_bm25 import BM25Okapi

from app.core.llm import provider
from app.rag.store import rag_store

KNOWLEDGE_DIR = "/app/data/knowledge"


def chunk_text(text: str) -> list:
    """按空行切片，丢弃过短片段。"""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return [c for c in chunks if len(c) >= 10]


def load_corpus(knowledge_dir: str = KNOWLEDGE_DIR) -> list:
    """读取 data/knowledge/*.{txt,md}，返回 [{id, text, source}]。"""
    docs = []
    patterns = ("*.txt", "*.md")
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(knowledge_dir, pattern))):
            source = os.path.basename(path)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            for i, chunk in enumerate(chunk_text(text)):
                docs.append({"id": f"{source}#{i}", "text": chunk, "source": source})
    return docs


class RAGService:
    def __init__(self, store=None, llm=None, knowledge_dir=KNOWLEDGE_DIR):
        self.store = store or rag_store
        self.llm = llm or provider
        self.knowledge_dir = knowledge_dir
        self.bm25 = None
        self.corpus_docs = []  # 存储原始文档内容和元数据，用于 BM25 检索后回显

    def _init_bm25(self):
        """从 Chroma 获取全量文档并初始化 BM25 索引。"""
        if self.bm25 is not None:
            return

        # 从 Chroma 获取所有文档
        res = self.store.collection.get(include=["documents", "metadatas"])
        documents = res.get("documents", [])
        metadatas = res.get("metadatas", [])
        ids = res.get("ids", [])

        if not documents:
            return

        self.corpus_docs = []
        tokenized_corpus = []
        for i in range(len(documents)):
            doc_text = documents[i]
            meta = metadatas[i] or {}
            self.corpus_docs.append({
                "id": ids[i],
                "text": doc_text,
                "source": meta.get("source", ""),
                "chunk_id": meta.get("chunk_id", 0)
            })
            # 使用 jieba 分词
            words = list(jieba.cut(doc_text))
            tokenized_corpus.append(words)

        if tokenized_corpus:
            self.bm25 = BM25Okapi(tokenized_corpus)

    async def build_index(self) -> dict:
        """读取语料，向量化，写入 Chroma。返回入库文档数。"""
        docs = load_corpus(self.knowledge_dir)
        if not docs:
            return {"indexed": 0, "detail": "知识库目录无 txt 文件"}
        texts = [d["text"] for d in docs]
        embeddings = await self.llm.embed(texts)
        self.store.upsert(
            ids=[d["id"] for d in docs],
            documents=texts,
            embeddings=embeddings,
            metadatas=[{"source": d["source"]} for d in docs],
        )
        # 强制重置 BM25，下次 search 时重新初始化
        self.bm25 = None
        return {"indexed": len(docs), "collection_size": self.store.count()}

    async def search(self, query: str, top_k: int = 3, threshold: float = 0.70) -> list:
        """混合检索：向量检索 + BM25 检索，使用 RRF (Reciprocal Rank Fusion) 融合。
        
        threshold: 相似度阈值（针对向量检索的 L2 距离）。
        """
        # 1. 向量检索
        q_emb = (await self.llm.embed([query]))[0]
        vec_results = self.store.query(q_emb, top_k=top_k * 2)  # 取多一点用于融合
        vec_docs = vec_results.get("documents", [[]])[0]
        vec_metas = vec_results.get("metadatas", [[]])[0]
        vec_ids = vec_results.get("ids", [[]])[0]
        vec_dists = vec_results.get("distances", [[]])[0]

        # 2. BM25 检索
        self._init_bm25()
        bm25_hits = []
        if self.bm25:
            query_words = list(jieba.cut(query))
            # 获取所有文档的 BM25 分数
            scores = self.bm25.get_scores(query_words)
            # 获取前 top_k * 2 个结果的索引
            import numpy as np
            top_indices = np.argsort(scores)[::-1][:top_k * 2]
            
            for idx in top_indices:
                if scores[idx] > 0:  # 只保留有匹配的分数
                    bm25_hits.append(self.corpus_docs[idx]["id"])

        # 3. RRF 融合
        # rrf_score = sum(1 / (k + rank))
        k = 60
        rrf_scores = {}

        # 处理向量检索排名
        for i, doc_id in enumerate(vec_ids):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + i + 1)

        # 处理 BM25 检索排名
        for i, doc_id in enumerate(bm25_hits):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + i + 1)

        # 排序并取前 top_k
        sorted_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        # 准备返回结果，同时保留原有的关键词加权和阈值逻辑（针对向量距离）
        # 如果是 BM25 独有的结果，我们给它一个虚拟的距离值
        final_docs = []
        
        # 为了获取完整信息，建立一个映射
        id_to_vec_info = {vec_ids[i]: {"dist": vec_dists[i], "meta": vec_metas[i], "text": vec_docs[i]} for i in range(len(vec_ids))}
        id_to_corpus_info = {d["id"]: d for d in self.corpus_docs}
        
        keywords = ["压力", "失眠", "焦虑", "难过", "抑郁", "放松", "考试"]

        for doc_id, rrf_score in sorted_ids:
            if doc_id in id_to_vec_info:
                info = id_to_vec_info[doc_id]
                dist = info["dist"]
                text = info["text"]
                meta = info["meta"] or {}
            elif doc_id in id_to_corpus_info:
                info = id_to_corpus_info[doc_id]
                dist = 0.65  # 给 BM25 命中但向量未命中的结果一个适中的虚拟距离，确保能过阈值
                text = info["text"]
                meta = {"source": info["source"], "chunk_id": info["chunk_id"]}
            else:
                continue

            # 原有关键词加权逻辑
            adjusted_dist = dist
            if any(kw in text for kw in keywords):
                adjusted_dist -= 0.05
            
            if adjusted_dist > threshold:
                continue

            final_docs.append({
                "text": text,
                "source": meta.get("source", ""),
                "chunk_id": meta.get("chunk_id", 0),
                "distance": adjusted_dist,
                "rrf_score": rrf_score
            })

        return final_docs


rag_service = RAGService()
