"""语音服务：ASR 转写 + TTS 合成（D3 四期）。

ASR（qwen-audio-3.0-asr-flash）：
  走 DashScope 同步 multimodal-generation 端点（该模型不支持 OpenAI 兼容
  chat/completions，会报 "format is empty"）。音频以 Data URL（base64，≤10MB）
  放入 input_audio，parameters.format 必须显式指定音频格式。

TTS（qwen-audio-3.0-tts-flash）：
  走 DashScope 原生非实时 HTTP API（/api/v1/services/audio/tts/SpeechSynthesizer），
  voice 必须放在 input 内；返回 output.audio.url（OSS 临时 mp3 链接），下载即得 bytes。
  不走 SDK 的 WebSocket 通道（容器内 ws 初始化失败，'NoneType' has no attribute
  'close_frame'），httpx 直连与 ASR 同栈。

安全边界：语音只是输入/输出通道，转写文本进入 /api/chat 现有管线后，
危机前置扫描、硬编码升级等安全链路完全不变。
"""
import base64
import logging
from urllib.parse import urlparse

import httpx

from app.core.config import settings
from app.core.voice_local import transcribe_local, synthesize_local

logger = logging.getLogger("psycheflow.core.voice")

MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 百炼 ASR 输入上限 10MB（编码后）
MAX_TTS_CHARS = 2000                # 回复上限 250 字，此处留足余量

# 浏览器采集/常见上传格式 → DashScope parameters.format 值
FORMAT_BY_MIME = {
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
}


class VoiceError(Exception):
    """语音链路业务错误（向上传递给 API 层转 4xx/5xx）。"""


def _dashscope_root() -> str:
    """从 OpenAI 兼容 base_url 推导 DashScope 根域名（同源跟随配置）。"""
    origin = urlparse(settings.dashscope_base_url)
    return f"{origin.scheme}://{origin.netloc}"


async def _dashscope_generate(payload: dict) -> dict:
    """调用 DashScope 同步 multimodal-generation 端点，返回 JSON dict。"""
    url = f"{_dashscope_root()}/api/v1/services/aigc/multimodal-generation/generation"
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            resp = await cli.post(url, json=payload, headers=headers)
    except Exception as e:
        logger.warning("voice: ASR transport failed: %s", e)
        raise VoiceError(f"语音识别服务调用失败：{type(e).__name__}") from e
    if resp.status_code != 200:
        # 尝试提取 DashScope 实际错误信息，方便定位（如模型未开通、额度耗尽等）
        detail = ""
        try:
            err_body = resp.json()
            detail = err_body.get("message") or err_body.get("errors") or ""
        except Exception:
            detail = resp.text[:300]
        logger.warning("voice: ASR http %s: %s", resp.status_code, detail)
        raise VoiceError(f"语音识别服务返回 {resp.status_code}：{detail}")
    return resp.json()


async def transcribe(audio_bytes: bytes, mime_type: str = "audio/wav") -> str:
    """音频 → 文本。失败抛 VoiceError。"""
    if not audio_bytes:
        raise VoiceError("音频内容为空")
    
    # 优先使用本地模式 (D5)
    if settings.voice_mode == "local":
        try:
            text = await transcribe_local(audio_bytes)
            if not text:
                raise VoiceError("未能识别出语音内容，请靠近麦克风重试")
            logger.info("voice_local: transcribed %d chars", len(text))
            return text
        except VoiceError:
            raise
        except Exception as e:
            logger.error("voice_local: ASR failed, check logs: %s", e)
            # 本地失败不回退云端（确保数据不出机原则），直接抛错
            raise VoiceError(f"本地语音识别失败: {e}")

    if len(audio_bytes) > MAX_AUDIO_BYTES:
        raise VoiceError("音频超过 10MB 上限")

    mime_type = (mime_type or "audio/wav").split(";")[0].strip().lower() or "audio/wav"
    fmt = FORMAT_BY_MIME.get(mime_type)
    if not fmt:
        raise VoiceError("不支持的音频格式，请使用 WAV 或 MP3 录音")

    data_url = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode()}"
    # sample_rate 仅对 WAV 生效（前端固定采集 16kHz）；mp3 自带头信息无需指定
    parameters: dict = {"format": fmt}
    if fmt == "wav":
        parameters["sample_rate"] = "16000"

    payload = {
        "model": settings.model_asr,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_audio", "input_audio": {"data": data_url}},
                    ],
                }
            ]
        },
        "parameters": parameters,
    }

    resp = await _dashscope_generate(payload)

    # 响应结构（该模型特有，无 choices）：
    # {"output": {"output": {"sentence": {"text": ...}}, "text": "全文"}, "request_id": ...}
    output = resp.get("output") or {}
    text = (output.get("text") or "").strip()
    if not text:
        inner = ((output.get("output") or {}).get("sentence") or {}).get("text") or ""
        text = inner.strip()
    if not text:
        raise VoiceError("未能识别出语音内容，请靠近麦克风重试")
    logger.info("voice: transcribed %d chars", len(text))
    return text


async def _tts_http(text: str) -> bytes:
    """TTS：DashScope 原生非实时 HTTP API，返回 mp3 bytes。

    请求体：voice/format 等参数必须放在 input 内（顶层会报
    InvalidParameter: TTS speak operation failed）。
    响应：output.audio.url 为 OSS 临时下载链接（data 为空），
    部分情况可能直接返回 base64，两种都兼容。
    """
    url = f"{_dashscope_root()}/api/v1/services/audio/tts/SpeechSynthesizer"
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.model_tts,
        "input": {
            "text": text,
            "voice": settings.tts_voice,
            "format": "mp3",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as cli:
            resp = await cli.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.warning("voice: TTS http %s: %s", resp.status_code, resp.text[:200])
                raise VoiceError(f"语音合成服务返回 {resp.status_code}")
            audio = ((resp.json().get("output") or {}).get("audio") or {})
            audio_url = audio.get("url") or ""
            audio_b64 = audio.get("data") or ""
            if audio_url:
                dl = await cli.get(audio_url)
                if dl.status_code != 200:
                    logger.warning("voice: TTS download http %s", dl.status_code)
                    raise VoiceError(f"语音合成音频下载失败（{dl.status_code}）")
                data = dl.content
            elif audio_b64:
                data = base64.b64decode(audio_b64)
            else:
                raise VoiceError("语音合成未返回音频数据")
    except VoiceError:
        raise
    except Exception as e:
        logger.warning("voice: TTS transport failed: %s", e)
        raise VoiceError(f"语音合成服务调用失败：{type(e).__name__}") from e
    if not data:
        raise VoiceError("语音合成未返回音频数据")
    logger.info("voice: synthesized %d bytes", len(data))
    return data


async def synthesize(text: str) -> bytes:
    """文本 → mp3 bytes。失败抛 VoiceError。"""
    text = (text or "").strip()
    if not text:
        raise VoiceError("合成文本为空")
    
    # 优先使用本地模式 (D5)
    if settings.voice_mode == "local":
        try:
            return await synthesize_local(text)
        except Exception as e:
            logger.error("voice_local: TTS failed: %s", e)
            raise VoiceError(f"本地语音合成失败: {e}")

    if len(text) > MAX_TTS_CHARS:
        text = text[:MAX_TTS_CHARS]
    return await _tts_http(text)
