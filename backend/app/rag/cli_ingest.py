"""RAG 知识库 markdown 语料批量入库 CLI 工具。

用法：
    python -m app.rag.cli_ingest --dir ../../data/knowledge --chunk 300 --overlap 50 --reset
    python -m app.rag.cli_ingest                          # 使用默认参数

也可以通过 `from app.rag.cli_ingest import do_ingest` 在代码中调用。
"""
import argparse
import asyncio
import glob
import os
import sys

from app.rag.store import rag_store


def _default_knowledge_dir() -> str:
    """根据当前文件路径求 data/knowledge 的绝对路径。

    cli_ingest.py 位于 backend/app/rag/，向上两级到 backend/，
    再上一级到项目根，再进 data/knowledge。
    """
    here = os.path.dirname(os.path.abspath(__file__))  # backend/app/rag
    return os.path.abspath(os.path.join(here, "..", "..", "..", "data", "knowledge"))


async def do_ingest(
    dir: str = None,
    chunk: int = 300,
    overlap: int = 50,
    reset: bool = False,
    namespace: str = "psycheflow_knowledge",
) -> int:
    """批量 ingest 指定目录下所有 *.md 到 Chroma。

    返回最终插入的 chunks 总数（失败的文件跳过，不计入总数）。
    """
    knowledge_dir = dir or _default_knowledge_dir()
    if not os.path.isdir(knowledge_dir):
        print(f"[ingest] 目录不存在: {knowledge_dir}")
        return 0

    # 1. 若指定 --reset，先清空 collection
    if reset:
        rag_store.reset_namespace(namespace)
        print(f"[ingest] 已重置 namespace: {namespace}")

    # 2. 查找目录下所有 .md 文件
    md_pattern = os.path.join(knowledge_dir, "*.md")
    md_files = sorted(glob.glob(md_pattern))
    if not md_files:
        print(f"[ingest] 目录下无 .md 文件: {knowledge_dir}")
        return 0

    # 3. 逐个 ingest_markdown
    total = 0
    for fpath in md_files:
        try:
            cnt = await rag_store.ingest_markdown(
                file_path=fpath,
                chunk_size=chunk,
                overlap=overlap,
                namespace=namespace,
            )
            total += cnt
            fname = os.path.basename(fpath)
            print(f"[ingest] {fname}: chunks={cnt}")
        except Exception as e:
            fname = os.path.basename(fpath)
            print(f"[ingest] {fname}: 失败 - {e}", file=sys.stderr)

    # 4. 打印总计
    docs_count = rag_store.count_docs(namespace)
    print(f"[ingest] 完成，新增/更新 chunks={total}，当前 collection 文档总数={docs_count}")
    return total


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="批量 ingest markdown 语料到 PsycheFlow RAG 向量库"
    )
    parser.add_argument(
        "--dir",
        type=str,
        default=None,
        help="markdown 语料目录（默认 = 项目 data/knowledge 绝对路径）",
    )
    parser.add_argument(
        "--chunk",
        type=int,
        default=300,
        help="每个 chunk 的字符数（默认 300）",
    )
    parser.add_argument(
        "--overlap",
        type=int,
        default=50,
        help="相邻 chunk 的重叠字符数（默认 50）",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="ingest 前先删除整个 namespace 集合（默认 False）",
    )
    parser.add_argument(
        "--namespace",
        type=str,
        default="psycheflow_knowledge",
        help="Chroma collection 名（默认 psycheflow_knowledge）",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(
        do_ingest(
            dir=args.dir,
            chunk=args.chunk,
            overlap=args.overlap,
            reset=args.reset,
            namespace=args.namespace,
        )
    )
