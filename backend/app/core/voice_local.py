"""本地语音驱动：faster-whisper (ASR) + sherpa-onnx (TTS)。

D5 阶段：私有化增强，支持完全离线运行。
模型从宿主机挂载至容器内 /models/voice。
"""
import io
import os
import logging
import wave
import numpy as np
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("psycheflow.core.voice_local")

# 懒加载实例，避免启动时强制加载大模型导致超时或 OOM
_asr_model = None
_tts_engine = None

def _get_asr_model():
    global _asr_model
    if _asr_model is None:
        from faster_whisper import WhisperModel
        path = settings.local_asr_model_path
        if not os.path.exists(path):
            raise RuntimeError(f"ASR 模型目录不存在: {path}")
        
        logger.info("Loading local ASR model from %s...", path)
        # 优先使用 CUDA，自动回退 CPU
        try:
            _asr_model = WhisperModel(path, device="cuda", compute_type="float16")
            logger.info("ASR loaded on GPU (CUDA)")
        except Exception as e:
            logger.warning("ASR GPU load failed (%s), falling back to CPU", e)
            _asr_model = WhisperModel(path, device="cpu", compute_type="int8")
            logger.info("ASR loaded on CPU")
    return _asr_model

def _get_tts_engine():
    global _tts_engine
    if _tts_engine is None:
        import sherpa_onnx
        model_dir = settings.local_tts_model_dir
        if not os.path.exists(model_dir):
            raise RuntimeError(f"TTS 模型目录不存在: {model_dir}")
        
        # 查找模型文件
        # vits-zh-aishell3 目录下可能有 vits-aishell3.onnx 或 vits-zh-aishell3.onnx
        vits_model = os.path.join(model_dir, "vits-aishell3.onnx")
        if not os.path.exists(vits_model):
            vits_model = os.path.join(model_dir, "vits-zh-aishell3.onnx")
        
        tokens = os.path.join(model_dir, "tokens.txt")
        lexicon = os.path.join(model_dir, "lexicon.txt")
        
        config = sherpa_onnx.OfflineTtsVitsModelConfig(
            model=vits_model,
            lexicon=lexicon,
            tokens=tokens,
            data_dir="",
            noise_scale=0.667,
            noise_scale_w=0.8,
            length_scale=1.0,
        )
        
        tts_config = sherpa_onnx.OfflineTtsConfig(
            model=sherpa_onnx.OfflineTtsModelConfig(vits=config, debug=False),
            rule_fsts="",
            max_num_sentences=1,
        )
        
        if not tts_config.validate():
            raise RuntimeError("TTS 配置验证失败")
            
        _tts_engine = sherpa_onnx.OfflineTts(tts_config)
        logger.info("Local TTS (Sherpa-ONNX) loaded")
    return _tts_engine

async def transcribe_local(audio_bytes: bytes) -> str:
    """本地 ASR 转写。"""
    model = _get_asr_model()
    
    # faster-whisper 需要文件对象或路径
    audio_file = io.BytesIO(audio_bytes)
    segments, info = model.transcribe(audio_file, beam_size=5, language="zh")
    
    text = "".join([s.text for s in segments]).strip()
    return text

async def synthesize_local(text: str) -> bytes:
    """本地 TTS 合成，返回 WAV bytes (前端通常支持 WAV)。"""
    engine = _get_tts_engine()
    
    # 合成生成的音频对象
    audio = engine.generate(text, sid=settings.local_tts_sid, speed=1.0)
    
    # 转换为 WAV 格式 bytes
    with io.BytesIO() as wav_io:
        with wave.open(wav_io, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(audio.sample_rate)
            # 转换 float32 为 int16，确保 audio.samples 是 numpy 数组
            samples_np = np.array(audio.samples)
            samples = (samples_np * 32767).astype(np.int16)
            wav_file.writeframes(samples.tobytes())
        return wav_io.getvalue()
