"""病例文档解析：文字版 PDF 文本抽取 + 文本清洗/截断。

一期仅支持文字版 PDF（pypdf 本地抽取，零 LLM 额度）与直接粘贴文本。
扫描件（整页图片、无文本层）抽不到文字时抛 NoTextLayerError，
由 API 层转成友好提示引导用户粘贴文本（图片 OCR 留二期）。

隐私：PDF bytes 仅在内存中解析，不写磁盘。
"""
import io
import logging
import re

logger = logging.getLogger("psycheflow.case_parser")

# 送入 LLM 的病例文本上限（字符数）。超长截断，防 token 滥用/费用失控。
MAX_CASE_CHARS = 6000
# 判定"有文本层"的最低字符数（低于此值视为扫描件）
MIN_TEXT_CHARS = 30


class CaseParseError(Exception):
    """病例解析业务异常（message 可直接展示给用户）。"""


class NoTextLayerError(CaseParseError):
    """PDF 无文本层（扫描件/图片型 PDF），一期无法解析。"""


def clean_text(text: str) -> str:
    """清洗抽取/粘贴的文本：统一换行、压缩多余空白、去零宽字符。"""
    if not text:
        return ""
    # 去零宽字符/BOM
    text = text.replace("\ufeff", "").replace("\u200b", "")
    # 每行首尾空白
    lines = [ln.strip() for ln in text.splitlines()]
    text = "\n".join(lines)
    # 3 个以上连续换行压成 2 个；行内连续空白压缩
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def truncate_case_text(text: str, limit: int = MAX_CASE_CHARS) -> tuple[str, bool]:
    """截断到 limit 字符，返回 (文本, 是否被截断)。"""
    if len(text) <= limit:
        return text, False
    return text[:limit] + "\n……（内容较长，以下省略）", True


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """从文字版 PDF 抽取纯文本。

    - pypdf 逐页 extract_text 拼接
    - 总字符数 < MIN_TEXT_CHARS 视为扫描件（无文本层）→ NoTextLayerError
    - 非 PDF/损坏 → CaseParseError
    """
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover - 依赖缺失属环境问题
        raise CaseParseError("PDF 解析组件未安装，请联系管理员") from e

    if not pdf_bytes:
        raise CaseParseError("文件内容为空")

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as e:
        raise CaseParseError("无法读取该 PDF，文件可能已损坏或不是有效的 PDF") from e

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as e:  # 单页失败不阻断，继续其余页
            logger.warning("case_parser: 单页抽取失败: %s", e)

    text = clean_text("\n".join(parts))
    if len(text) < MIN_TEXT_CHARS:
        logger.info("case_parser: PDF 文本层字符数=%d，判定为扫描件", len(text))
        raise NoTextLayerError(
            "该 PDF 是扫描件/图片版（没有可复制的文字层），暂无法直接解读。"
            "请打开文件后把文字内容复制粘贴到文本框，再发送给我。"
        )

    text, _ = truncate_case_text(text)
    return text
