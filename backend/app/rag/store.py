"""Chroma 向量库封装。

连接 chroma 容器（http://chroma:8000），管理知识库集合。
向量由百炼 text-embedding-v3 或本地 bge-m3 生成，存入 Chroma 做相似检索。

注意：语料切片/入库统一走 app.rag.service.build_index（chunk_text 结构感知切片 + tags
元数据）。本模块只负责 collection 连接与基础 upsert/query/count/reset。
"""
import chromadb

from app.core.config import settings

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

    def count_docs(self, namespace: str = "psycheflow_knowledge") -> int:
        """返回指定 namespace 集合中的文档数。"""
        try:
            collection = self._get_collection(namespace)
            return collection.count()
        except Exception:
            return 0


rag_store = RAGStore()
