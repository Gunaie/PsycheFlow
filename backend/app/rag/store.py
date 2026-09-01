"""Chroma 向量库封装。

连接 chroma 容器（http://chroma:8000），管理知识库集合。
向量由百炼 text-embedding-v3 生成，存入 Chroma 做相似检索。
"""
import chromadb

from app.core.config import settings

COLLECTION_NAME = "psycheflow_knowledge"


class RAGStore:
    def __init__(self):
        self._client = None
        self._collection = None

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


rag_store = RAGStore()
