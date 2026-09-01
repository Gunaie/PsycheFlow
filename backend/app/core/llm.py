"""百炼 LLM Provider 抽象层。

百炼平台兼容 OpenAI 协议：
- base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
- 鉴权: Bearer DASHSCOPE_API_KEY

4 个模型按角色取用（来自 settings）：
- intake  : qwen3.8-2.4t-a95b     结构化提取/分诊/计分辅助
- dialog  : deepseek-v4-pro-0813  开放对话/共情（有 reasoning_content 思考链）
- report  : deepseek-v4-flash-0731 报告生成/高频兜底（有 reasoning_content 思考链）
- embed   : text-embedding-v3     RAG 向量化

注意：deepseek-v4 系列有 reasoning_content（思考链）字段，会先思考再输出 content，
max_tokens 须足够容纳 reasoning + content（否则 content 为空、finish_reason=length）。
温度按角色自动取用：计分场景确定性优先（0.1），对话场景放宽（0.35）。
"""
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import settings


class LLMProvider:
    """百炼 LLM/Embedding 调用封装，按角色路由模型。"""

    def __init__(self, settings_obj=None):
        self._settings = settings_obj or settings
        self._client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._settings.dashscope_base_url,
                api_key=self._settings.dashscope_api_key,
            )
        return self._client

    def model_for(self, role: str) -> str:
        mapping = {
            "intake": self._settings.model_intake,
            "dialog": self._settings.model_dialog,
            "report": self._settings.model_report,
            "embed": self._settings.model_embed,
        }
        if role not in mapping:
            raise ValueError(f"未知角色: {role}，可用: {list(mapping)}")
        return mapping[role]

    def temp_for(self, role: str) -> float:
        mapping = {
            "intake": self._settings.temp_intake,
            "dialog": self._settings.temp_dialog,
            "report": self._settings.temp_report,
        }
        return mapping.get(role, 0.7)

    async def chat(
        self,
        role: str,
        messages: list,
        temperature: Optional[float] = None,
        max_tokens: int = 2048,
    ) -> str:
        """调用 chat completion，返回 assistant 文本。"""
        temp = self.temp_for(role) if temperature is None else temperature
        resp = await self.client.chat.completions.create(
            model=self.model_for(role),
            messages=messages,
            temperature=temp,
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""

    async def embed(self, texts: list) -> list:
        """调用 embedding，返回向量列表。

        百炼 text-embedding-v3 单次上限 10 条，自动分批。
        """
        BATCH = 10
        result = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            resp = await self.client.embeddings.create(
                model=self.model_for("embed"),
                input=batch,
            )
            result.extend(d.embedding for d in resp.data)
        return result

    async def ping(self, role: str = "report") -> str:
        """轻量连通性测试：发一句话，返回模型回复。"""
        return await self.chat(
            role,
            [{"role": "user", "content": "请只回复 pong 两个字。"}],
            temperature=0,
            max_tokens=300,
        )


provider = LLMProvider()
