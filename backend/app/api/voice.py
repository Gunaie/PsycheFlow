"""语音端点（D3 四期）：语音输入转写 + AI 回复合成朗读。

POST /api/voice/transcribe  multipart(file=音频)  -> {text}
POST /api/voice/synthesize  {text}                -> audio/mpeg 二进制

设计：
- 语音只是通道：转写文本由前端拿到后再走既有 /api/chat，
  危机前置扫描/硬编码升级链路不变
- TTS 前剥离「来源：《xxx》」引用标记，避免把文献出处读出来
- 无需登录（与 /api/chat 保持一致的匿名可用策略）
"""
import logging
import re

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from app.core import voice as voice_service
from app.core.voice import VoiceError

router = APIRouter(prefix="/api/voice", tags=["voice"])

logger = logging.getLogger("psycheflow.api.voice")

# 浏览器 MediaRecorder / Web Audio 常见输出格式
ALLOWED_MIME_PREFIXES = ("audio/",)

# 「来源：《xxx》」/「来源：xxx」/来源：《xxx》/来源：xxx 行，TTS 前剥离
_SOURCE_RE = re.compile(r"「?\s*来源\s*[：:]\s*《?[^》\n]{1,60}》?」?")


class SynthesizeRequest(BaseModel):
    text: str


def strip_source_marks(text: str) -> str:
    """剥离来源引用标记，TTS 不朗读文献出处。"""
    cleaned = _SOURCE_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """音频上传 → 转写文本。前端拿到文本后自行调用 /api/chat。"""
    content_type = file.content_type or "audio/wav"
    if not content_type.startswith(ALLOWED_MIME_PREFIXES):
        raise HTTPException(status_code=415, detail=f"不支持的音频类型：{content_type}")

    audio = await file.read()
    try:
        text = await voice_service.transcribe(audio, mime_type=content_type)
    except VoiceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"text": text}


@router.post("/synthesize")
async def synthesize(req: SynthesizeRequest):
    """回复文本 → mp3 音频流（前端用 <audio> 播放）。"""
    text = strip_source_marks(req.text)
    if not text:
        raise HTTPException(status_code=422, detail="合成文本为空")
    try:
        audio = await voice_service.synthesize(text)
    except VoiceError as e:
        logger.warning("synthesize failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))
    return Response(content=audio, media_type="audio/mpeg")
