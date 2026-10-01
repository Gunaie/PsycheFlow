"""RAG 知识库 markdown 语料批量入库 CLI 工具（统一走 service.build_index 的结构感知切片）。

注意：本工具是 build_index 的兼容入口，新增语料请优先使用 POST /api/rag/build。
历史版本（3.A）曾用 ingest_markdown 的字符滑窗切片，不带章节前缀和 tags，
会造成索引退化（详见 2026-09-26 Chroma 索引静默丢失教训），已统一收口到 chunk_text。

用法：
    python -m app.rag.cli_ingest --reset
    python -m app.rag.cli_ingest                          # 使用默认参数
"""
import argparse
import asyncio
import sys


async def do_ingest(reset: bool = False) -> int:
    """调用统一的 build_index 重建知识库（结构感知切片 + tags 元数据）。

    返回最终插入的 chunks 总数。
    """
    # 延迟 import 避免循环依赖
    from app.rag.service import build_index
    from app.rag.store import rag_store

    if reset:
        rag_store.reset_namespace()
        print("[ingest] 已重置 namespace: psycheflow_knowledge")

    total = await build_index()
    docs_count = rag_store.count()
    print(f"[ingest] 完成，新增/更新 chunks={total}，当前 collection 文档总数={docs_count}")
    return total


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量 ingest 知识库语料到 PsycheFlow RAG 向量库（统一走 chunk_text 结构感知切片）"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="ingest 前先删除整个 namespace 集合（默认 False）",
    )
    # 以下参数仅保留向后兼容，实际由 chunk_text 内部逻辑接管
    parser.add_argument("--dir", type=str, default=None, help="（已废弃）语料目录，由 settings.rag_knowledge_dir 决定")
    parser.add_argument("--chunk", type=int, default=300, help="（已废弃）chunk 大小，由 chunk_text 结构感知切片接管")
    parser.add_argument("--overlap", type=int, default=50, help="（已废弃）重叠字符数，由 chunk_text 结构感知切片接管")
    parser.add_argument("--namespace", type=str, default="psycheflow_knowledge", help="（已废弃）固定使用 psycheflow_knowledge")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    if args.dir or args.chunk != 300 or args.overlap != 50 or args.namespace != "psycheflow_knowledge":
        print("[ingest] 警告: --dir/--chunk/--overlap/--namespace 参数已废弃，统一由 chunk_text 接管", file=sys.stderr)
    asyncio.run(do_ingest(reset=args.reset))
