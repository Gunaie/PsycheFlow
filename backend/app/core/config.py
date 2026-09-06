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

    # 语音模式 (D5 阶段)
    #   cloud = 阿里云百炼 (默认)
    #   local = 本地 ASR (faster-whisper) + 本地 TTS (sherpa-onnx)
    voice_mode: str = "cloud"
    local_asr_model_path: str = "/models/voice/asr/medium"
    local_tts_model_dir: str = "/models/voice/tts/vits-zh-aishell3"
    local_tts_sid: int = 0  # 默认发音人 ID (aishell3 共有 174 个)

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
    # 备份加密（合规 c：SQLite 备份 AES-256-CBC 口令，从 .env 注入，勿入库；空则拒绝备份）
    backup_passphrase: str = ""
    # Ollama 本地兜底（五期：百炼失败/离线时回退本地 LLM；空 base_url = 禁用，保持原 cloud-only 行为）
    # 容器内连宿主机 Ollama 用 http://host.docker.internal:11434/v1；连 compose 的 ollama 服务用 http://ollama:11434/v1
    ollama_base_url: str = ""
    ollama_model: str = "qwen2.5:7b"

    # LLM 运行模式（双版本切换，见 docs/本地模型化方案.md）：
    #   cloud = 阿里云百炼云端（默认，按量付费）
    #   local = Ollama 完全本地（对话/分诊/报告/embedding 全走本地，数据不出本机，可离线；语音 ASR/TTS 仍需云端）
    llm_mode: str = "cloud"
    # 本地模式（LLM_MODE=local）使用的 Ollama 模型
    local_model: str = "qwen2.5:7b"       # 默认基座（intake/triage 分诊用；RTX 4060 8GB 可跑 Q4 量化）
    local_embed_model: str = "bge-m3"     # RAG 向量化（Ollama /v1/embeddings 端点，1024 维）
    # 3.B 微调专用模型（留空 = 回退 local_model 基座）：dialog/report 可各自挂 LoRA 合并后的 GGUF
    local_model_triage: str = ""          # 极速分诊模型（推荐 qwen2.5:0.5b）
    local_model_dialog: str = ""          # 共情对话（intervention 节点 dialog/dialog_stream）
    local_model_report: str = ""          # 报告发展建议（report）

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
    def _validate_llm_mode(self) -> "Settings":
        """local 模式必须配置 OLLAMA_BASE_URL（否则启动即快速失败，不静默回退云端）。"""
        if self.llm_mode.strip().lower() == "local" and not self.ollama_base_url:
            raise ValueError(
                "LLM_MODE=local 必须配置 OLLAMA_BASE_URL"
                "（后端容器内填 http://host.docker.internal:11434/v1）"
            )
        return self

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
