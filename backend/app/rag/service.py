"""RAG 服务：向量化 + 检索 + 索引构建。

知识库语料放 data/knowledge/*.txt，结构感知切片：Markdown 标题作为章节前缀、
过短段落合并、超长段落按句子边界二次切分，保证每片"有头（章节）有尾（完整句）"。
向量化用百炼 text-embedding-v3，存入 Chroma。
"""
import glob
import os
import re

import jieba
from rank_bm25 import BM25Okapi

from app.core.llm import provider
from app.rag.store import rag_store

KNOWLEDGE_DIR = "/app/data/knowledge"

# 切片参数：句子级重切 + 章节前缀
MAX_CHUNK_CHARS = 400   # 单片正文中段上限，超长按句子边界二次切分
MIN_CHUNK_CHARS = 40    # 缓冲下限：过短段落与相邻内容合并，避免碎片
MAX_SECTION_PREFIX = 40  # 章节前缀截断长度
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?；;])")  # 保留句尾标点的零宽切分
_MD_HEADER_RE = re.compile(r"^#{1,6}\s+")
_META_SOURCE_RE = re.compile(r"^#{0,6}\s*来源[:：]")  # 匹配 txt「来源：」与 md「## 来源」
_META_SCENE_RE = re.compile(r"^适用场景[:：]\s*(.+)$")


def _split_long(buf: str) -> list:
    """把超长缓冲按句子边界切成 ≤MAX_CHUNK_CHARS 的片；单句超长才按长度硬切。"""
    if len(buf) <= MAX_CHUNK_CHARS:
        return [buf]
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(buf) if s]
    pieces: list = []
    cur = ""
    for sent in sentences:
        if cur and len(cur) + len(sent) > MAX_CHUNK_CHARS:
            pieces.append(cur)
            cur = sent
        else:
            cur += sent
        while len(cur) > MAX_CHUNK_CHARS:
            pieces.append(cur[:MAX_CHUNK_CHARS])
            cur = cur[MAX_CHUNK_CHARS:]
    if cur:
        pieces.append(cur)
    return pieces


def chunk_text(text: str) -> list:
    """结构感知切片：按空行分段。

    - Markdown 标题行（#/##/###）作为"章节"，不单独成片，而是给后续段落加前缀
      【章节】，让切片有"头"（标题行的裸片段毫无信息量，是"没头"碎片的主因）
    - 过短段落（<10 字噪声）丢弃；累积缓冲 <MIN_CHUNK_CHARS 时并入后续段落
    - 超长段落按句子边界二次切分，切片以完整句子收尾（有"尾"）
    """
    chunks: list = []
    section = ""   # 当前章节标题（最近一个 Markdown 标题行）
    buf = ""       # 段落累积缓冲

    def _flush() -> None:
        nonlocal buf
        if not buf:
            return
        pieces = _split_long(buf)
        if section:
            prefix = f"【{section[:MAX_SECTION_PREFIX]}】"
            pieces = [f"{prefix}{p}" for p in pieces]
        chunks.extend(pieces)
        buf = ""

    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        if _MD_HEADER_RE.match(block):
            _flush()  # 换章节前先落片，避免跨章节内容混在一片
            section = _MD_HEADER_RE.sub("", block).strip()
            continue
        if len(block) < 10:  # 纯噪声碎片（与旧逻辑 len>=10 一致）
            continue
        buf = f"{buf}\n{block}" if buf else block
        if len(buf) >= MIN_CHUNK_CHARS:
            _flush()
    _flush()
    return [c for c in chunks if len(c) >= 10]


def parse_metadata(text: str) -> tuple:
    """从文件头部解析来源与适用场景标签。

    匹配并剥离「来源：」「## 来源」「适用场景：标签1,标签2」行，
    返回 (清理后的正文, 标签列表)。标签用于检索时的场景过滤。
    """
    lines = text.split("\n")
    tags: list[str] = []
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if _META_SOURCE_RE.match(stripped):
            continue
        m = _META_SCENE_RE.match(stripped)
        if m:
            raw = m.group(1).strip()
            tags = [t.strip() for t in re.split(r"[,，]", raw) if t.strip()]
            continue
        kept.append(line)
    return "\n".join(kept), tags


def load_corpus(knowledge_dir: str = KNOWLEDGE_DIR) -> list:
    """读取 data/knowledge/*.{txt,md}，返回 [{id, text, source, tags}]。"""
    docs = []
    patterns = ("*.txt", "*.md")
    for pattern in patterns:
        for path in sorted(glob.glob(os.path.join(knowledge_dir, pattern))):
            source = os.path.basename(path)
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            text, tags = parse_metadata(text)
            for i, chunk in enumerate(chunk_text(text)):
                docs.append({
                    "id": f"{source}#{i}",
                    "text": chunk,
                    "source": source,
                    "tags": tags,
                })
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
        # 重建前清空旧集合：切片规则变更后 chunk ID 变化，旧片残留会污染检索
        self.store.reset_namespace()
        texts = [d["text"] for d in docs]
        embeddings = await self.llm.embed(texts)
        self.store.upsert(
            ids=[d["id"] for d in docs],
            documents=texts,
            embeddings=embeddings,
            metadatas=[{"source": d["source"], "tags": d.get("tags", [])} for d in docs],
        )
        # 强制重置 BM25，下次 search 时重新初始化
        self.bm25 = None
        return {"indexed": len(docs), "collection_size": self.store.count()}

    async def search(self, query: str, top_k: int = 3, threshold: float = 0.75) -> list:
        """混合检索：向量检索 + BM25 检索，使用 RRF (Reciprocal Rank Fusion) 融合。

        threshold: 相似度阈值（针对向量检索的 L2 距离）。
        0.60 → 0.75 放宽：原 0.60 过严，把「04_放松技术.txt」（腹式呼吸 chunk 无
        「焦虑」关键词，向量距离 0.71）过滤掉，导致求做法问题只召回 DBT 等间接
        相关内容。放宽到 0.75 后放松/睡眠类直接做法能召回，由来源去重保证多样性。
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
            max_score = float(scores.max()) if len(scores) else 0.0
            if max_score > 0:
                # 取前 top_k * 2 个结果的索引
                import numpy as np
                top_indices = np.argsort(scores)[::-1][:top_k * 2]

                for idx in top_indices:
                    # 自适应过滤：仅保留强关键词命中（得分 ≥ 0.5*最高分），
                    # 单个常见词碰巧出现的低分命中直接丢弃（弱相关卡片的主要来源）
                    if scores[idx] >= 0.5 * max_score:
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

        # 危机内容过滤：非危机查询（用户未提及自杀/自伤/轻生等）不推送危机热线片段。
        # 实测"睡不着"会召回含 12355 热线的危机科普卡片，属于噪声，应过滤。
        _CRISIS_QUERY_WORDS = ("自杀", "自伤", "轻生", "不想活", "结束生命", "割腕", "跳楼")
        query_is_crisis = any(w in query for w in _CRISIS_QUERY_WORDS)

        for doc_id, rrf_score in sorted_ids:
            if doc_id in id_to_vec_info:
                info = id_to_vec_info[doc_id]
                dist = info["dist"]
                text = info["text"]
                meta = info["meta"] or {}
            elif doc_id in id_to_corpus_info:
                info = id_to_corpus_info[doc_id]
                # BM25 独有命中：候选已按"得分 ≥ 0.5*最高分"过滤，虚拟距离 0.55
                # 可过 0.60 阈值，但弱于典型向量命中（强关键词精确匹配才走到这）
                dist = 0.55
                text = info["text"]
                meta = {"source": info["source"], "chunk_id": info["chunk_id"], "tags": info.get("tags", [])}
            else:
                continue

            tags = meta.get("tags", []) or []

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
                "tags": tags,
                "distance": adjusted_dist,
                "rrf_score": rrf_score
            })

        # 非危机查询过滤带「危机」标签的片段，避免"睡不着"却推送自杀干预热线卡片。
        # 用标签过滤替代原内容关键词匹配，更精确（危机内容本身含"热线""120"等词，
        # 关键词法会误杀含这些词的非危机片段）。
        if not query_is_crisis:
            final_docs = [
                d for d in final_docs
                if "危机" not in d.get("tags", [])
            ]

        # 来源去重：同一来源最多保留 1 条（排名最高的），确保返回多样化方法
        # 避免 top_k=3 全是 dbt_skills.md 同一来源的不同片段
        seen_sources: set[str] = set()
        deduped: list = []
        for d in final_docs:
            src = d.get("source", "")
            if src not in seen_sources:
                seen_sources.add(src)
                deduped.append(d)
            if len(deduped) >= top_k:
                break
        final_docs = deduped

        return final_docs


rag_service = RAGService()
