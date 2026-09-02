"""应用配置：百炼平台 + 模型 + 向量库 + 安全参数。

通过 .env 注入，避免硬编码与密钥入库。
"""
import os

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 阿里云百炼平台
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 5 个百炼模型（各司其职）
    model_intake: str = "qwen3.8-2.4t-a95b"       # 结构化提取/计分辅助（有思考链）
    model_triage: str = "qwen3.8-27b"             # 意图分类（关思考链，4 类标签输出快；首 token 0.38s）
    model_dialog: str = "deepseek-v4-pro-0813"    # 开放对话/共情（有 reasoning_content 思考链）
    model_dialog_stream: str = "qwen3.8-max"      # 流式干预专用（关思考链，首 content ~0.58s，NFR-5）
    model_report: str = "deepseek-v4-flash-0731" # 高频/兜底（有 reasoning_content 思考链）
    model_embed: str = "text-embedding-v3"       # RAG 向量化

    # 语音（D3 四期，DashScope 原生 HTTP API）
    model_asr: str = "qwen-audio-3.0-asr-flash"  # 语音识别（multimodal-generation）
    model_tts: str = "qwen-audio-3.0-tts-flash"  # 语音合成（audio/tts/SpeechSynthesizer）
    tts_voice: str = "longanhuan_v3.6"           # TTS 音色

    # Chroma 向量库
    chroma_host: str = "chroma"
    chroma_port: int = 8000

    # SQLite
    sqlite_path: str = "/app/data/psycheflow.db"

    # FastAPI
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    frontend_origin: str = "http://localhost:5173"

    # 安全（青少年合规）
    enable_audit_log: bool = True
    crisis_hotline_12355: str = "12355"

    # LLM 温度：计分场景确定性优先，对话场景放宽
    temp_intake: float = 0.1
    temp_triage: float = 0.1   # 意图分类确定性优先
    temp_dialog: float = 0.35
    temp_report: float = 0.1

    # 目录：审计日志 + RAG 知识库（默认从 sqlite_path 推导，支持 .env 覆盖）
    logs_dir: str = ""
    rag_knowledge_dir: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="after")
    def _ensure_dirs(self) -> "Settings":
        data_dir = os.path.dirname(self.sqlite_path)
        if not self.logs_dir:
            self.logs_dir = os.path.join(data_dir, "logs")
        if not self.rag_knowledge_dir:
            self.rag_knowledge_dir = os.path.join(data_dir, "knowledge")
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(self.rag_knowledge_dir, exist_ok=True)
        return self


settings = Settings()
