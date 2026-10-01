"""RAG 服务：向量化 + 检索 + 索引构建。

知识库语料放 data/knowledge/*.txt，结构感知切片：Markdown 标题作为章节前缀、
过短段落合并、超长段落按句子边界二次切分，保证每片"有头（章节）有尾（完整句）"。
向量化用百炼 text-embedding-v3，存入 Chroma。
"""
import datetime
import glob
import json
import logging
import os
import re

import jieba
from rank_bm25 import BM25Okapi

from app.core.llm import provider
from app.core.safety import CRISIS_KEYWORDS
from app.rag.store import rag_store

logger = logging.getLogger("psycheflow.rag")

# P1 RAG 进阶：bge-reranker 重排序（本地模式启用，云端模式跳过保持轻量）
RERANKER_ENABLED = False
try:
    from sentence_transformers import CrossEncoder
    RERANKER_ENABLED = True
    logger.info("bge-reranker loaded (CrossEncoder)")
except ImportError:
    logger.info("bge-reranker not available, skip rerank (pip install sentence-transformers)")

KNOWLEDGE_DIR = "/app/data/knowledge"

# 检索埋点：query 文本最多保留 200 字（够主题聚类，控日志体积）
TRACE_QUERY_MAX_CHARS = 200

# 检索阈值按嵌入模型校准（L2 距离，向量均已归一化）：
# - 云端 text-embedding-v3：相关片段实测 0.60–0.74，阈值 0.75
# - 本地 bge-m3（Ollama）：相关片段实测 0.77–0.91、无关片段 ≥1.10
#   （cos≈0.55 为 bge 系列常用检索下限），阈值 0.95；沿用 0.75 会把
#   PTSD/ADHD 等新主题的语义命中整片过滤（2026-09-09 评测实测）
VEC_THRESHOLD_CLOUD = 0.75
VEC_THRESHOLD_LOCAL = 0.95
# BM25 独有命中的虚拟距离：略低于阈值可过检，但弱于中等以上向量命中，
# 保证"语义匹配优先、关键词仅补充"
BM25_VIRTUAL_GAP = 0.03

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


def _write_search_trace(event: dict) -> None:
    """检索埋点：best-effort 追加一行 JSON 到 logs/rag_search_YYYYMMDD.jsonl。

    18.1.7 零命中/弱命中周度聚类的数据来源（同主题簇单周 ≥5 次弱命中即立项补文档）。
    - 隐私纪律：只记 query 文本与检索指标，**不记 session/账号/IP 等任何用户标识**；
    - caller 词表：intervention（真实对话）/ case（案例上传）/ api（调试端点）/
      eval（评测脚本，周度聚类须排除）/ unknown（未透传，单测等）；
    - 写入失败仅 warning，**绝不阻断检索主流程**。
    """
    try:
        from app.core.config import settings

        os.makedirs(settings.logs_dir, exist_ok=True)
        day = datetime.datetime.now().strftime("%Y%m%d")
        path = os.path.join(settings.logs_dir, f"rag_search_{day}.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 - 埋点失败永不影响检索
        logger.warning("rag-search trace 写入失败: %s", e)


class RAGService:
    def __init__(self, store=None, llm=None, knowledge_dir=KNOWLEDGE_DIR):
        self.store = store or rag_store
        self.llm = llm or provider
        self.knowledge_dir = knowledge_dir
        self.bm25 = None
        self.corpus_docs = []  # 存储原始文档内容和元数据，用于 BM25 检索后回显
        self._reranker = None  # 延迟初始化 CrossEncoder

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
                "chunk_id": meta.get("chunk_id", 0),
                # tags 必须透传：BM25 独有命中也要参与「危机」标签过滤，
                # 否则非危机查询会通过 BM25 补充召回漏入危机片段
                "tags": meta.get("tags", []) or [],
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

    def _default_threshold(self) -> float:
        """按嵌入模型返回 L2 距离阈值（is_local 须严格为 True，兼容测试 mock）。"""
        return VEC_THRESHOLD_LOCAL if getattr(self.llm, "is_local", False) is True else VEC_THRESHOLD_CLOUD

    def _get_reranker(self):
        """延迟初始化 bge-reranker（CrossEncoder），本地模式启用，云端跳过。"""
        if not RERANKER_ENABLED:
            return None
        if self._reranker is None:
            try:
                from sentence_transformers import CrossEncoder
                # bge-reranker-base 中文优化，跨编码器重排序
                self._reranker = CrossEncoder("BAAI/bge-reranker-base", max_length=512)
                logger.info("reranker: BAAI/bge-reranker-base loaded")
            except Exception as e:
                logger.warning("reranker load failed: %s", e)
                return None
        return self._reranker

    def _rerank_candidates(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        """用 CrossEncoder 对融合候选重排序，返回 top_k。

        仅当本地模式且 reranker 可用时生效；云端模式保持原排序（避免额外延迟）。
        """
        if not getattr(self.llm, "is_local", False):
            return candidates[:top_k]
        reranker = self._get_reranker()
        if not reranker or len(candidates) <= top_k:
            return candidates[:top_k]
        pairs = [[query, c["text"]] for c in candidates]
        scores = reranker.predict(pairs)
        for c, s in zip(candidates, scores):
            c["rerank_score"] = float(s)
        candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
        logger.info("rerank: reordered %d candidates, top1 score=%.3f", len(candidates), scores[0])
        return candidates[:top_k]

    async def _rewrite_query(self, query: str) -> str:
        """查询重写：LLM 将口语化查询扩展为知识库同义词（提升召回）。

        示例："最近老失眠" → "睡眠障碍 入睡困难 失眠 睡眠卫生"
        失败时返回原查询，保证不阻断检索。
        """
        if not query or len(query) > 50:
            return query
        try:
            prompt = (
                "将以下用户口语化心理倾诉改写为知识库检索关键词（同义词扩展，空格分隔，不超过20字）。\n"
                "只输出关键词，不要解释。\n"
                "用户输入：{query}\n"
                "改写关键词："
            ).format(query=query)
            # 用 triage 角色快速生成（关思考链，max_tokens 小）
            rewrite = await self.llm.chat(
                "triage",
                [{"role": "user", "content": prompt}],
                max_tokens=30,
                temperature=0.0,
            )
            rewrite = (rewrite or "").strip()
            if rewrite and len(rewrite) <= 30:
                logger.info("query rewrite: '%s' → '%s'", query, rewrite)
                return f"{query} {rewrite}"
        except Exception as e:
            logger.warning("query rewrite failed: %s", e)
        return query

    async def search(
        self,
        query: str,
        top_k: int = 3,
        threshold: float | None = None,
        intent: str = "",
        caller: str = "unknown",
    ) -> list:
        """混合检索：向量检索为主 + BM25 补充召回 + 可选 CrossEncoder 重排序。

        排序策略：
        1）向量检索 top_k*3 候选，BM25 补充召回（虚拟距离 = 阈值-0.03）+ 双重印证加权（-0.03）
        2）本地模式：CrossEncoder 重排序（top-10 → top-3），云端模式跳过保持轻量
        3）过滤：距离阈值 / 危机标签 / 同源去重

        查询重写：口语化查询先经 LLM 扩展为知识库同义词，提升召回
        （示例："最近老失眠" → "睡眠障碍 入睡困难 失眠"）。

        弃用 RRF 的原因：RRF 排名融合让 BM25 关键词命中权重过大，
        「焦虑怎么缓解」中仅含"缓解"关键词的科普片段会挤掉含具体做法、
        向量距离更近的片段。向量语义相似度比关键词命中更能反映相关性。

        threshold: 向量 L2 距离阈值；None 时按嵌入模型自适应
        （云端 v3=0.75，本地 bge-m3=0.95——两模型距离尺度不同，实测见
        VEC_THRESHOLD_LOCAL 注释）。
        intent/caller: 检索埋点上下文（18.1.7）。intent 透传 triage 意图
        （倾诉/咨询/危机/寒暄/求助/案例等）；caller 标识调用来源
        （intervention/case/api/eval），周度弱命中聚类只统计真实流量。
        每次检索无论命中与否都追加一行结构化 JSON 日志到
        logs/rag_search_YYYYMMDD.jsonl（不记任何用户标识）。
        """
        if threshold is None:
            threshold = self._default_threshold()
        bm25_virtual = threshold - BM25_VIRTUAL_GAP

        # 0. 查询重写：口语化查询扩展为知识库同义词（提升召回，失败静默回退原查询）
        rewritten_query = await self._rewrite_query(query)
        logger.debug("rag search: query='%s' rewritten='%s'", query, rewritten_query)

        # 1. 向量检索（用重写后的查询）
        q_emb = (await self.llm.embed([rewritten_query]))[0]
        vec_results = self.store.query(q_emb, top_k=top_k * 3)  # 取多一点用于融合
        vec_docs = vec_results.get("documents", [[]])[0]
        vec_metas = vec_results.get("metadatas", [[]])[0]
        vec_ids = vec_results.get("ids", [[]])[0]
        vec_dists = vec_results.get("distances", [[]])[0]

        # 2. BM25 检索（仅取 top 命中用于补充召回 + 双重印证加权）
        self._init_bm25()
        bm25_hit_ids: set[str] = set()
        bm25_unique: list[dict] = []  # BM25 命中但向量未命中的片段
        if self.bm25:
            query_words = list(jieba.cut(query))
            scores = self.bm25.get_scores(query_words)
            max_score = float(scores.max()) if len(scores) else 0.0
            if max_score > 0:
                import numpy as np
                top_indices = np.argsort(scores)[::-1][:top_k * 3]
                vec_id_set = set(vec_ids)
                for idx in top_indices:
                    if scores[idx] < 0.7 * max_score:
                        continue
                    doc = self.corpus_docs[idx]
                    doc_id = doc["id"]
                    bm25_hit_ids.add(doc_id)
                    if doc_id not in vec_id_set:
                        bm25_unique.append(doc)

        # 3. 融合：以向量距离为主排序，BM25 补充召回 + 双重印证小幅加权
        from app.core.config import settings
        boost_keywords = settings.rag_boost_keywords
        candidates: list[dict] = []

        # 向量命中的片段
        for i in range(len(vec_ids)):
            meta = vec_metas[i] or {}
            text = vec_docs[i]
            adjusted = vec_dists[i]
            # 双重印证：同时被 BM25 强命中，距离减 0.03
            if vec_ids[i] in bm25_hit_ids:
                adjusted -= 0.03
            # 关键词命中加权（可配置）
            if any(kw in text for kw in boost_keywords):
                adjusted -= 0.05
            candidates.append({
                "id": vec_ids[i],
                "text": text,
                "source": meta.get("source", ""),
                "chunk_id": meta.get("chunk_id", 0),
                "tags": meta.get("tags", []) or [],
                "distance": adjusted,
            })

        # BM25 独有命中（向量未召回）：虚拟距离 = 阈值-0.03，可过检但弱于
        # 中等以上向量命中，让语义匹配优先。不做关键词加权——加权会让它在
        # bge-m3 尺度下（0.92-0.05=0.87）反超 0.86–0.90 的真实向量命中。
        for doc in bm25_unique:
            candidates.append({
                "id": doc["id"],
                "text": doc["text"],
                "source": doc["source"],
                "chunk_id": doc["chunk_id"],
                "tags": doc.get("tags", []) or [],
                "distance": bm25_virtual,
            })

        # 按调整后距离升序排序（越小越相关）
        candidates.sort(key=lambda x: x["distance"])

        # 3.5 CrossEncoder 重排序（本地模式且 reranker 可用时，top-10 → top-3）
        candidates = self._rerank_candidates(query, candidates, top_k * 3)

        # 4. 过滤 + 去重
        # 危机 query 判定复用 safety.CRISIS_KEYWORDS 单一事实源
        # （含自残/想死/了结自己/活不下去等，比硬编码子集更全）
        query_is_crisis = any(w in query for w in CRISIS_KEYWORDS)

        seen_sources: set[str] = set()
        final_docs: list[dict] = []
        # 埋点用过滤分支计数：区分零命中根因（距离超阈值 / 危机标签过滤 / 同源去重）
        n_drop_threshold = 0
        n_drop_crisis_tag = 0
        n_drop_dedup = 0
        for c in candidates:
            if c["distance"] > threshold:
                n_drop_threshold += 1
                continue
            if not query_is_crisis and "危机" in c["tags"]:
                n_drop_crisis_tag += 1
                continue
            src = c["source"]
            if src in seen_sources:
                n_drop_dedup += 1
                continue
            seen_sources.add(src)
            final_docs.append(c)
            if len(final_docs) >= top_k:
                break

        # 5. 结构化检索埋点（18.1.7：零命中/弱命中周度聚类的数据来源，不记用户标识）
        top1 = candidates[0] if candidates else None
        embed_mode = "local" if getattr(self.llm, "is_local", False) is True else "cloud"
        trace_event = {
            "ts": datetime.datetime.now().isoformat(timespec="milliseconds"),
            "caller": caller,
            "intent": intent or "unknown",
            "is_crisis": query_is_crisis,
            "threshold": round(threshold, 4),
            "embed_mode": embed_mode,
            "vec_hits": len(vec_ids),
            "bm25_unique": len(bm25_unique),
            # top1 距离取融合排序后首候选（无论是否被过滤），零命中时据此判弱命中
            "top1_distance": round(top1["distance"], 4) if top1 else None,
            "top1_passed": bool(top1 and top1["distance"] <= threshold),
            "result_count": len(final_docs),
            "drop_threshold": n_drop_threshold,
            "drop_crisis_tag": n_drop_crisis_tag,
            "drop_dedup": n_drop_dedup,
            "results": [
                {
                    "source": d.get("source", ""),
                    "tags": d.get("tags", []) or [],
                    "distance": round(d.get("distance", 0.0), 4),
                }
                for d in final_docs[:top_k]
            ],
            "query": (query or "")[:TRACE_QUERY_MAX_CHARS],
        }
        _write_search_trace(trace_event)

        # Prometheus 埋点：RAG 检索质量
        from app.core.metrics import track_rag_search
        top1_dist = top1["distance"] if top1 else None
        track_rag_search(caller=caller, intent=intent, result_count=len(final_docs), top1_distance=top1_dist)

        logger.info(
            "rag-search caller=%s intent=%s mode=%s threshold=%.2f top1=%s passed=%s results=%d",
            caller,
            intent or "-",
            embed_mode,
            threshold,
            f"{top1['distance']:.3f}" if top1 else "-",
            trace_event["top1_passed"],
            len(final_docs),
        )

        return final_docs


rag_service = RAGService()
