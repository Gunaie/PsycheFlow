"""应用配置：百炼平台 + 模型 + 向量库 + 安全参数。

通过 .env 注入，避免硬编码与密钥入库。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 阿里云百炼平台
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    # 4 个百炼模型（各司其职）
    model_intake: str = "qwen3.7-plus"          # 分诊/结构化提取/报告
    model_dialog: str = "deepseek-v4-pro-0813"  # 开放对话/共情
    model_report: str = "deepseek-v4-flash-0731"  # 高频/兜底
    model_embed: str = "text-embedding-v3"      # RAG 向量化

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
    temp_dialog: float = 0.35
    temp_report: float = 0.1

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
