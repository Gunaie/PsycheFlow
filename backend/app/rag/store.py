"""Chroma 向量库封装。

连接 chroma 容器（http://chroma:8000），管理知识库集合。
向量由百炼 text-embedding-v3 生成，存入 Chroma 做相似检索。
"""
import os

import chromadb

from app.core.config import settings
from app.core.llm import provider

COLLECTION_NAME = "psycheflow_knowledge"


class RAGStore:
    def __init__(self):
        self._client = None
        self._collection = None
        self._collections_cache = {}

    @property
    def client(self):
        if self._client is None:
            self._client = chromadb.HttpClient(
                host=settings.chroma_host,
                port=settings.chroma_port,
            )
        return self._client

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.client.get_or_create_collection(COLLECTION_NAME)
        return self._collection

    def _get_collection(self, namespace: str):
        """按 namespace 获取 collection（带缓存）。"""
        if namespace == COLLECTION_NAME and self._collection is not None:
            return self._collection
        if namespace not in self._collections_cache:
            self._collections_cache[namespace] = self.client.get_or_create_collection(namespace)
        return self._collections_cache[namespace]

    def upsert(self, ids, documents, embeddings, metadatas=None):
        self.collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=metadatas,
        )

    def query(self, embedding, top_k=3):
        return self.collection.query(query_embeddings=[embedding], n_results=top_k)

    def count(self):
        return self.collection.count()

    # ========== 以下为 Task 8 新增方法 ==========

    def reset_namespace(self, namespace: str = "psycheflow_knowledge") -> None:
        """删除指定 namespace 对应的集合（如果存在）。"""
        try:
            self.client.delete_collection(namespace)
        except Exception:
            # 集合不存在或其他异常，静默忽略
            pass
        # 清理缓存
        if namespace in self._collections_cache:
            del self._collections_cache[namespace]
        if namespace == COLLECTION_NAME:
            self._collection = None

    async def ingest_markdown(
        self,
        file_path: str,
        chunk_size: int = 300,
        overlap: int = 50,
        namespace: str = "psycheflow_knowledge",
    ) -> int:
        """读取 markdown 文件，按字符切分，向量化后写入 Chroma。

        返回插入的 chunks 总数。
        """
        # 1. 读取整个 markdown 文件
        with open(file_path, "r", encoding="utf-8") as f:
            text = f.read()

        # 2. 按 chunk_size 字符切分，带 overlap 重叠
        chunks = []
        start = 0
        text_len = len(text)
        while start < text_len:
            end = min(start + chunk_size, text_len)
            chunk = text[start:end].strip()
            if chunk:  # 跳过空片段
                chunks.append(chunk)
            if end >= text_len:
                break
            start = end - overlap
            if start < 0:
                start = 0

        total_chunks = len(chunks)
        if total_chunks == 0:
            return 0

        # 3. 用百炼 text-embedding-v3 做 embedding
        embeddings = await provider.embed(chunks)

        # 4. 写进 Chroma 的 collection
        collection = self._get_collection(namespace)
        source_name = os.path.basename(file_path)
        ids = [f"{source_name}#{i}" for i in range(total_chunks)]
        metadatas = [
            {
                "source": source_name,
                "chunk_id": i,
                "total_chunks": total_chunks,
            }
            for i in range(total_chunks)
        ]
        collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return total_chunks

    def count_docs(self, namespace: str = "psycheflow_knowledge") -> int:
        """返回指定 namespace 集合中的文档数。"""
        try:
            collection = self._get_collection(namespace)
            return collection.count()
        except Exception:
            return 0


rag_store = RAGStore()
