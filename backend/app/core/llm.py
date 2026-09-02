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
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger("psycheflow.llm")


class LLMProvider:
    """百炼 LLM/Embedding 调用封装，按角色路由模型。

    兜底链：百炼 cloud → Ollama 本地（ollama_base_url 配置时）→ 节点级硬编码话术。
    Ollama 仅在 cloud 异常或空回复时介入；未配置（base_url 空）则保持原 cloud-only 行为。
    """

    def __init__(self, settings_obj=None):
        self._settings = settings_obj or settings
        self._client: Optional[AsyncOpenAI] = None
        self._ollama_client: Optional[AsyncOpenAI] = None

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                base_url=self._settings.dashscope_base_url,
                api_key=self._settings.dashscope_api_key,
            )
        return self._client

    @property
    def ollama_client(self) -> AsyncOpenAI:
        """本地 Ollama 客户端（OpenAI 兼容端点）。Ollama 不需鉴权，api_key 填占位非空值。"""
        if self._ollama_client is None:
            self._ollama_client = AsyncOpenAI(
                base_url=self._settings.ollama_base_url,
                api_key="ollama",
            )
        return self._ollama_client

    @property
    def ollama_enabled(self) -> bool:
        """是否启用 Ollama 兜底（ollama_base_url 非空即启用）。"""
        return bool(self._settings.ollama_base_url)

    def model_for(self, role: str) -> str:
        mapping = {
            "intake": self._settings.model_intake,
            "triage": self._settings.model_triage,
            "dialog": self._settings.model_dialog,
            "dialog_stream": self._settings.model_dialog_stream,
            "report": self._settings.model_report,
            "embed": self._settings.model_embed,
        }
        if role not in mapping:
            raise ValueError(f"未知角色: {role}，可用: {list(mapping)}")
        return mapping[role]

    def temp_for(self, role: str) -> float:
        mapping = {
            "intake": self._settings.temp_intake,
            "triage": self._settings.temp_triage,
            "dialog": self._settings.temp_dialog,
            "dialog_stream": self._settings.temp_dialog,  # 流式干预复用 dialog 温度 0.35
            "report": self._settings.temp_report,
        }
        return mapping.get(role, 0.7)

    # 低延迟流式角色关闭思考链以保证首 token 速度。
    # qwen3.x 系列（max/27b 等）支持 enable_thinking=False；qwen3.8-2.4t-a95b 例外（强制开启）。
    _NO_THINKING_ROLES = frozenset({"triage", "dialog_stream"})

    def _extra_body_for(self, role: str) -> dict:
        """triage/dialog_stream 关闭思考链；其余角色不传（deepseek 走 reasoning_content 提质量）。"""
        if role in self._NO_THINKING_ROLES:
            return {"enable_thinking": False}
        return {}

    async def _chat_once(
        self, client, model, messages, temp, max_tokens, extra_body
    ) -> str:
        """单次 chat completion（不重试、不兜底），返回 content 或 ""。"""
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temp,
            max_tokens=max_tokens,
        )
        if extra_body:
            kwargs["extra_body"] = extra_body
        resp = await client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""

    async def chat(
        self,
        role: str,
        messages: list,
        temperature: Optional[float] = None,
        max_tokens: int = 2048,
    ) -> str:
        """调用 chat completion，返回 assistant 文本。

        兜底：cloud 异常或空回复时，若启用 Ollama 则转本地模型；
        cloud 异常且 Ollama 未启用时保持原行为（异常上抛，由节点级硬编码话术兜底）。
        """
        temp = self.temp_for(role) if temperature is None else temperature
        cloud_extra = self._extra_body_for(role)
        try:
            content = await self._chat_once(
                self.client, self.model_for(role), messages, temp, max_tokens, cloud_extra
            )
        except Exception as e:
            if not self.ollama_enabled:
                raise
            logger.warning("cloud chat 失败，转 Ollama 兜底: %s", e)
            content = ""
        if content:
            return content
        # cloud 空回复（quota/思考链耗尽）或异常 → Ollama 兜底
        if self.ollama_enabled:
            try:
                content = await self._chat_once(
                    self.ollama_client, self._settings.ollama_model,
                    messages, temp, max_tokens, {},
                )
                if content:
                    return content
            except Exception as e:
                logger.warning("Ollama 兜底也失败: %s", e)
        return ""

    async def _stream_once(self, client, model, messages, temp, max_tokens, extra_body):
        """单次流式 chat completion（不重试、不兜底），async yield content token。"""
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temp,
            max_tokens=max_tokens,
            stream=True,
        )
        if extra_body:
            kwargs["extra_body"] = extra_body
        stream = await client.chat.completions.create(**kwargs)
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # 只取 content（最终回复），reasoning_content 思考链不外推
            content = getattr(delta, "content", None)
            if content:
                yield content

    async def stream(
        self,
        role: str,
        messages: list,
        temperature: Optional[float] = None,
        max_tokens: int = 2048,
    ):
        """流式 chat completion，async yield content token（用户可见文本）。

        deepseek-v4 / qwen3 系列均有 reasoning_content（思考链），stream 模式下
        delta 可能同时含 reasoning_content 和 content；此处只 yield content（最终
        回复），思考链不推给用户。

        首 token 速度：triage/dialog_stream 两角色经 _extra_body_for 关闭
        enable_thinking（qwen3.x 的 max/27b 支持关闭），保证首 content < 2s（NFR-5）。
        qwen3.8-2.4t-a95b 强制开启思考（不可关），故不用于这两个角色。

        兜底：cloud 流起始即失败（未 yield 任何 token）时，若启用 Ollama 则转本地流式；
        已部分输出则不再切（避免拼接错乱），异常上抛由 SSE error 事件处理。
        """
        temp = self.temp_for(role) if temperature is None else temperature
        cloud_extra = self._extra_body_for(role)
        yielded = False
        try:
            async for tok in self._stream_once(
                self.client, self.model_for(role), messages, temp, max_tokens, cloud_extra
            ):
                yielded = True
                yield tok
        except Exception as e:
            if yielded or not self.ollama_enabled:
                raise
            logger.warning("cloud stream 起始即失败，转 Ollama 兜底: %s", e)
            async for tok in self._stream_once(
                self.ollama_client, self._settings.ollama_model,
                messages, temp, max_tokens, {},
            ):
                yield tok

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
