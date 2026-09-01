"""语音链路（D3 四期）单测。

覆盖：
- strip_source_marks：TTS 前剥离「来源：《xxx》」引用
- transcribe：payload 构造（模型名/Data URL/上限）、响应解析、异常分支
- synthesize：空文本/正常合成（mock _tts_http）/HTTP 请求体构造/异常包装
- API 层：/api/voice/transcribe（类型校验+mock 转写）、
  /api/voice/synthesize（audio/mpeg 响应）
"""
import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.voice import strip_source_marks
from app.core.config import settings
from app.core.voice import MAX_AUDIO_BYTES, VoiceError, transcribe, synthesize
from app.main import app

client = TestClient(app)


class TestStripSourceMarks:
    def test_strips_source_line(self):
        text = "先接住情绪。我们可以试试深呼吸。\n来源：《CBT 技术》"
        cleaned = strip_source_marks(text)
        assert "来源" not in cleaned
        assert "深呼吸" in cleaned

    def test_strips_bracketed_source_inline(self):
        cleaned = strip_source_marks("情绪可能轻松一点。「来源：《放松技术》」记得找老师聊聊")
        assert "《放松技术》" not in cleaned
        assert "记得找老师聊聊" in cleaned

    def test_keeps_normal_text(self):
        text = "我在听你说，慢慢来。"
        assert strip_source_marks(text) == text


class TestTranscribe:
    async def test_ok_returns_text_and_payload_shape(self):
        fake_resp = {"output": {"output": {"sentence": {"text": "你好呀"}}, "text": "你好呀"}}
        with patch("app.core.voice._dashscope_generate", new=AsyncMock(return_value=fake_resp)) as m:
            text = await transcribe(b"RIFF....", mime_type="audio/wav")

        assert text == "你好呀"
        payload = m.call_args.args[0]
        assert payload["model"] == settings.model_asr
        assert payload["parameters"]["format"] == "wav"
        assert payload["parameters"]["sample_rate"] == "16000"
        part = payload["input"]["messages"][0]["content"][0]
        assert part["type"] == "input_audio"
        assert part["input_audio"]["data"].startswith("data:audio/wav;base64,")
        encoded = part["input_audio"]["data"].split(",", 1)[1]
        assert base64.b64decode(encoded) == b"RIFF...."

    async def test_mp3_skips_sample_rate(self):
        fake_resp = {"output": {"text": "你好"}}
        with patch("app.core.voice._dashscope_generate", new=AsyncMock(return_value=fake_resp)) as m:
            text = await transcribe(b"ID3", mime_type="audio/mpeg")
        assert text == "你好"
        payload = m.call_args.args[0]
        assert payload["parameters"]["format"] == "mp3"
        assert "sample_rate" not in payload["parameters"]

    async def test_unsupported_mime_raises(self):
        with pytest.raises(VoiceError, match="不支持的音频格式"):
            await transcribe(b"x", mime_type="audio/webm")

    async def test_empty_audio_raises(self):
        with pytest.raises(VoiceError, match="为空"):
            await transcribe(b"")

    async def test_oversized_audio_raises(self):
        with pytest.raises(VoiceError, match="10MB"):
            await transcribe(b"x" * (MAX_AUDIO_BYTES + 1))

    async def test_transport_failure_wrapped_as_voice_error(self):
        with patch("app.core.voice._dashscope_generate", new=AsyncMock(side_effect=VoiceError("语音识别服务调用失败：ConnectError"))):
            with pytest.raises(VoiceError, match="调用失败"):
                await transcribe(b"audio")

    async def test_bad_response_shape_raises(self):
        with patch("app.core.voice._dashscope_generate", new=AsyncMock(return_value={"error": "x"})):
            with pytest.raises(VoiceError, match="未能识别"):
                await transcribe(b"audio")

    async def test_empty_content_raises(self):
        with patch("app.core.voice._dashscope_generate", new=AsyncMock(return_value={"output": {"text": ""}})):
            with pytest.raises(VoiceError, match="未能识别"):
                await transcribe(b"audio")


class TestSynthesize:
    async def test_ok_returns_bytes_via_http(self):
        with patch("app.core.voice._tts_http", new=AsyncMock(return_value=b"mp3-bytes")) as m:
            audio = await synthesize("我在听你说。")
        assert audio == b"mp3-bytes"
        m.assert_awaited_once_with("我在听你说。")

    async def test_empty_text_raises(self):
        with pytest.raises(VoiceError, match="为空"):
            await synthesize("   ")

    async def test_long_text_truncated(self):
        with patch("app.core.voice._tts_http", new=AsyncMock(return_value=b"mp3")) as m:
            await synthesize("长" * 3000)
        assert m.await_args.args[0] == "长" * 2000

    async def test_unexpected_error_wrapped(self):
        with patch(
            "app.core.voice._tts_http",
            new=AsyncMock(side_effect=VoiceError("语音合成服务调用失败：ConnectError")),
        ):
            with pytest.raises(VoiceError, match="调用失败"):
                await synthesize("你好")


class TestTtsHttpPayload:
    """验证 TTS HTTP 请求体构造（voice 必须在 input 内，DashScope 硬性要求）。"""

    async def test_payload_shape(self):
        fake_resp = {
            "output": {"audio": {"url": "http://oss.example.com/a.mp3", "data": ""}},
        }
        with (
            patch("app.core.voice.httpx.AsyncClient") as mock_cli_cls,
            patch("app.core.voice.settings") as mock_settings,
        ):
            mock_settings.model_tts = "qwen-audio-3.0-tts-flash"
            mock_settings.tts_voice = "longanhuan_v3.6"
            mock_settings.dashscope_api_key = "sk-test"
            mock_settings.dashscope_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            mock_cli = mock_cli_cls.return_value.__aenter__.return_value
            mock_cli.post = AsyncMock(return_value=_fake_http_response(200, fake_resp))
            mock_cli.get = AsyncMock(return_value=_fake_http_response(200, b"mp3-data", json_mode=False))

            audio = await synthesize("你好")

        assert audio == b"mp3-data"
        url = mock_cli.post.call_args.args[0]
        assert "tts/SpeechSynthesizer" in url
        payload = mock_cli.post.call_args.kwargs["json"]
        assert payload["model"] == "qwen-audio-3.0-tts-flash"
        assert payload["input"]["voice"] == "longanhuan_v3.6"
        assert payload["input"]["format"] == "mp3"
        # voice 不允许出现在顶层
        assert "voice" not in payload

    async def test_http_500_wrapped(self):
        with patch("app.core.voice.httpx.AsyncClient") as mock_cli_cls:
            mock_cli = mock_cli_cls.return_value.__aenter__.return_value
            mock_cli.post = AsyncMock(return_value=_fake_http_response(500, {"message": "boom"}))
            with pytest.raises(VoiceError, match="语音合成服务返回 500"):
                await synthesize("你好")


def _fake_http_response(status: int, body, json_mode: bool = True):
    from types import SimpleNamespace

    if json_mode:
        return SimpleNamespace(status_code=status, json=lambda: body, text=str(body), content=b"{}")
    return SimpleNamespace(status_code=status, content=body)


class TestVoiceApi:
    def test_transcribe_endpoint(self):
        with patch("app.api.voice.voice_service.transcribe", new=AsyncMock(return_value="我最近压力好大")) as m:
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("rec.wav", b"RIFFdata", "audio/wav")},
            )
        assert r.status_code == 200
        assert r.json() == {"text": "我最近压力好大"}
        m.assert_awaited_once()

    def test_transcribe_rejects_non_audio(self):
        r = client.post(
            "/api/voice/transcribe",
            files={"file": ("doc.pdf", b"%PDF", "application/pdf")},
        )
        assert r.status_code == 415

    def test_transcribe_voice_error_maps_422(self):
        with patch(
            "app.api.voice.voice_service.transcribe",
            new=AsyncMock(side_effect=VoiceError("未能识别出语音内容，请靠近麦克风重试")),
        ):
            r = client.post(
                "/api/voice/transcribe",
                files={"file": ("rec.wav", b"RIFFdata", "audio/wav")},
            )
        assert r.status_code == 422
        assert "未能识别" in r.json()["detail"]

    def test_synthesize_endpoint_returns_mpeg(self):
        with patch("app.api.voice.voice_service.synthesize", new=AsyncMock(return_value=b"mp3")) as m:
            r = client.post("/api/voice/synthesize", json={"text": "我听到你说考试没考好。\n来源：《CBT》"})
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"
        assert r.content == b"mp3"
        # 来源标记在到达合成前已被剥离
        m.assert_awaited_once_with("我听到你说考试没考好。")

    def test_synthesize_empty_text_422(self):
        r = client.post("/api/voice/synthesize", json={"text": "「来源：《CBT》」"})
        assert r.status_code == 422
