"""LLM Provider 抽象层（双模式：云端百炼 / 本地 Ollama）。

模式由 settings.llm_mode 控制（.env LLM_MODE，见 docs/本地模型化方案.md）：

- cloud（默认）：百炼平台兼容 OpenAI 协议
  - base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
  - 鉴权: Bearer DASHSCOPE_API_KEY
  - 模型按角色取用：intake=qwen3.8-2.4t-a95b / triage=qwen3.8-27b /
    dialog=deepseek-v4-pro / dialog_stream=qwen3.8-max / report=deepseek-v4-flash /
    embed=text-embedding-v3
  - 兜底链：百炼 cloud → Ollama 本地（ollama_base_url 配置时）→ 节点级硬编码话术

- local：完全本地私有化，对话/分诊/报告/embedding 全走 Ollama，**不触云端**
  （数据不出本机，可离线；语音 ASR/TTS 暂仍走百炼，后续本地化）
  - chat 类角色统一 local_model（qwen2.5:7b），embed 用 local_embed_model（bge-m3）
  - Ollama 失败时返回 "" / 上抛，由节点级硬编码话术兜底（不回退云端）

注意：deepseek-v4 系列有 reasoning_content（思考链）字段，会先思考再输出 content，
max_tokens 须足够容纳 reasoning + content（否则 content 为空、finish_reason=length）。
温度按角色自动取用：计分场景确定性优先（0.1），对话场景放宽（0.35）。
"""
import logging
from typing import Optional

from openai import AsyncOpenAI

from app.core.config import settings

logger = logging.getLogger("psycheflow.llm")


def _embedding_index(d) -> int:
    """embedding 响应条目排序 key：取 index，缺失/非 int 时归 0（保稳定原序）。"""
    try:
        return int(getattr(d, "index", 0))
    except (TypeError, ValueError):
        return 0


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

    @property
    def is_local(self) -> bool:
        """本地私有化模式（LLM_MODE=local）：所有调用直走 Ollama，不触云端。"""
        return str(self._settings.llm_mode).strip().lower() == "local"

    def model_for(self, role: str) -> str:
        """角色 → 模型名。

        local 模式：chat 类角色统一 local_model，embed 用 local_embed_model；
        cloud 模式：按角色映射百炼模型。
        """
        chat_roles = ("intake", "triage", "dialog", "dialog_stream", "report")
        if self.is_local:
            if role == "embed":
                return self._settings.local_embed_model
            if role in chat_roles:
                return self._settings.local_model
            raise ValueError(f"未知角色: {role}，可用: embed / {list(chat_roles)}")
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

    def _primary_for(self, role: str):
        """返回主调用三元组 (client, model, extra_body)。

        local 模式：Ollama client + 本地模型名 + 无 extra_body（enable_thinking 是百炼参数）；
        cloud 模式：百炼 client + 角色模型 + triage/dialog_stream 关思考链。
        """
        if self.is_local:
            if not self.ollama_enabled:
                # config 校验理论上已拦截，此处双保险（不静默回退云端）
                raise RuntimeError("LLM_MODE=local 但 OLLAMA_BASE_URL 未配置")
            return self.ollama_client, self.model_for(role), {}
        return self.client, self.model_for(role), self._extra_body_for(role)

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

        local 模式：直走 Ollama 本地模型，异常/空回复返回 ""（节点级硬编码话术兜底），
        **不回退云端**（数据不出本机）。
        cloud 模式：cloud 异常或空回复时，若启用 Ollama 则转本地模型兜底；
        cloud 异常且 Ollama 未启用时异常上抛（由节点级硬编码话术兜底）。
        """
        temp = self.temp_for(role) if temperature is None else temperature
        if self.is_local:
            client, model, extra = self._primary_for(role)
            try:
                content = await self._chat_once(client, model, messages, temp, max_tokens, extra)
                if content:
                    return content
                logger.warning("local Ollama chat 返回空内容（role=%s, model=%s）", role, model)
            except Exception as e:
                logger.warning("local Ollama chat 失败（role=%s）: %s", role, e)
            return ""
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

        local 模式：直走 Ollama 流式，异常上抛由 SSE error 事件 + 节点级话术兜底（不回退云端）。
        cloud 兜底：cloud 流起始即失败（未 yield 任何 token）时，若启用 Ollama 则转本地流式；
        已部分输出则不再切（避免拼接错乱），异常上抛由 SSE error 事件处理。
        """
        temp = self.temp_for(role) if temperature is None else temperature
        if self.is_local:
            client, model, extra = self._primary_for(role)
            async for tok in self._stream_once(client, model, messages, temp, max_tokens, extra):
                yield tok
            return
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

        local 模式：走 Ollama /v1/embeddings（bge-m3），分批 8 条（本地逐条推理，保守批量）；
        cloud 模式：百炼 text-embedding-v3，单次上限 10 条，自动分批。
        返回顺序与输入 texts 一致（按 resp.data 的 index 排序保险）。
        """
        if not texts:
            return []
        if self.is_local:
            client, model, _ = self._primary_for("embed")
            BATCH = 8
            result = []
            for i in range(0, len(texts), BATCH):
                batch = texts[i:i + BATCH]
                resp = await client.embeddings.create(model=model, input=batch)
                ordered = sorted(resp.data, key=_embedding_index)
                result.extend(d.embedding for d in ordered)
            return result
        BATCH = 10
        result = []
        for i in range(0, len(texts), BATCH):
            batch = texts[i:i + BATCH]
            resp = await self.client.embeddings.create(
                model=self.model_for("embed"),
                input=batch,
            )
            ordered = sorted(resp.data, key=_embedding_index)
            result.extend(d.embedding for d in ordered)
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
