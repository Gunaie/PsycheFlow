"""RAG 服务：向量化 + 检索 + 索引构建。

知识库语料放 data/knowledge/*.txt，按空行切片，每段一个文档。
向量化用百炼 text-embedding-v3，存入 Chroma。
"""
import glob
import os

from app.core.llm import provider
from app.rag.store import rag_store

KNOWLEDGE_DIR = "/app/data/knowledge"


def chunk_text(text: str) -> list:
    """按空行切片，丢弃过短片段。"""
    chunks = [c.strip() for c in text.split("\n\n") if c.strip()]
    return [c for c in chunks if len(c) >= 10]


def load_corpus(knowledge_dir: str = KNOWLEDGE_DIR) -> list:
    """读取 data/knowledge/*.txt，返回 [{id, text, source}]。"""
    docs = []
    for path in sorted(glob.glob(os.path.join(knowledge_dir, "*.txt"))):
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
        return {"indexed": len(docs), "collection_size": self.store.count()}

    async def search(self, query: str, top_k: int = 3) -> list:
        """检索相关片段。返回 [{text, source, chunk_id, distance}]。

        source 保证来自 chunk 的 metadata.source，不为空；
        每条额外带 chunk_id: int 字段供前端展示片段号。
        """
        q_emb = (await self.llm.embed([query]))[0]
        results = self.store.query(q_emb, top_k=top_k)
        docs = (results.get("documents") or [[]])[0]
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
        out = []
        for i, doc in enumerate(docs):
            meta = metas[i] if i < len(metas) else {}
            meta = meta or {}
            # source 从 metadata 取值，保证不为空
            src = meta.get("source") or ""
            # chunk_id 从 metadata 取值，保证是 int
            cid = meta.get("chunk_id", 0)
            try:
                cid_int = int(cid)
            except (TypeError, ValueError):
                cid_int = 0
            out.append({
                "text": doc,
                "source": src,
                "chunk_id": cid_int,
                "distance": dists[i] if i < len(dists) else None,
            })
        return out


rag_service = RAGService()
