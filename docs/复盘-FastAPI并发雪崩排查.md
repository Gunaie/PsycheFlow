# 一次 FastAPI 并发雪崩排查：当 7B LoRA 遇上 asyncio 事件循环

> 排查日期：2026-10-01
> 环境：Windows 11 + Docker Desktop，RTX 4060 Laptop 8GB
> 栈：FastAPI + LangGraph + Ollama（qwen2.5 7B Q4_K_M LoRA × 2，bge-m3-cpu）
> 结论：修复前并发 ≥2 全部 120s 超时；修复后 10 并发 37/37 成功，P95 18.6s

## TL;DR

给项目补压测数据时发现一个荒诞的对比：`/api/health` 压出 **563 QPS、0 错误**，而真实业务接口 `/api/chat` **2 个并发就全部超时**，单发却只要 0.25s。

排查后发现两个叠加问题：

1. **RAG 的 bge-reranker 同步推理直接跑在 async 函数里**，`reranker.predict()` 一旦执行就锁死整个事件循环（真实隐患，顺手修了）；
2. **真凶**：RAG 查询重写在"全本地私有化"模式下仍然调用一个 4.4GB 的 triage-LoRA 模型做一次 LLM 调用，单次 48s。它和对话模型在 8GB 显卡上反复换入换出，并发时互相挤爆——这是主因。

修复方式都不复杂：同步推理用 `asyncio.to_thread` 扔进线程池；本地模式直接跳过 LLM 查询重写。但定位过程中踩的几个"迷惑现象"值得记录。

## 背景：系统链路

PsycheFlow 的对话接口走 LangGraph 多智能体链：

```
用户消息
  → Triage（危机词表/寒暄/求助，全规则零 LLM）
  → RAG 检索（查询重写 → 向量 + BM25 融合 → bge-reranker 重排序）
  → Intervention（dialog-LoRA 生成 → 回复质检，不合格最多三级重试）
  → SSE 流式返回 + 对话落库
```

本地私有化模式（`LLM_MODE=local`）下，两个 LLM 角色各挂一个独立 LoRA：`qwen2.5:dialog-lora`（对话）和 `qwen2.5:triage-lora`（分诊），Q4_K_M 量化后各 4.4GB。8GB 显存**装不下两个同时常驻**。

## 现象：三个互相矛盾的数字

压测脚本（`backend/scripts/loadtest/chat_load.py`，注册学生账号绕过匿名限流）先跑出来的结果：

| 测试 | 结果 |
|---|---|
| `/api/health`，50 并发 × 30s | 16900 请求全部成功，**QPS 563，P95 102ms** |
| `/api/chat`，10 并发 × 120s | **10/10 全部超时**（120s read timeout） |
| 单发 `/api/chat`（"你好"） | **0.25s 正常返回** |

同一个后端进程，无状态健康检查飞快、单发飞快、并发即死。典型的"共享资源被串行化"症状，但被第一个假象带偏了很久。

### 迷惑点 1：单发的"你好"根本没走 LLM

一开始拿单发 0.25s 的成功当"链路是通的"证据，怀疑方向全在并发框架上。

实际上 Triage 节点有一条**寒暄快速通道**：`detect_greeting()` 白名单正则命中"你好"后直接返回硬编码话术，毫秒级结束，**不加载任何模型**。而压测 worker 发的是 `"你好0"、"你好1"、"你好2"`——混入数字后不命中白名单，走的是完整倾诉链路（RAG + LoRA）。

两边测的根本不是一条代码路径。后来换成 `"我最近压力很大，晚上睡不着怎么办"` 单发，立刻复现：**95.38s**。

> 教训：压测的"单发基线"必须确认请求真的穿过了目标链路。看一眼响应内容、对一眼节点日志，别只看状态码。

### 迷惑点 2：后端日志里"没有"请求

并发超时的两分钟里，`docker logs psycheflow-backend` 只有 Prometheus 抓 `/metrics` 的记录，**看不到任何 `/api/chat` 访问日志**。一度怀疑请求没进后端。

其实是 uvicorn 的访问日志在**响应完成后才打印**。请求全部挂在 handler 里等 LLM，日志自然一条都没有。这反而说明阻塞发生在请求处理内部，而不是入口层。

### 迷惑点 3：Ollama 里看不到模型

`ollama ps` 在超时期间反复查看，多数时候是空表，偶尔能抓到一个 `llama-server` 进程处于 `D`（不可中断睡眠）状态。模型似乎在加载，但永远加载不完。

## 排查：拆链路，给每一步计时

怀疑事件循环被同步调用阻塞后，先扫了一遍 async 函数里的同步重活，锁定第一个嫌疑：

### 嫌疑一（隐患成立，但不是主因）：reranker 同步推理

RAG 检索的重排序环节直接在 async 函数里调用 sentence-transformers：

```python
# backend/app/rag/service.py（修复前）
async def search(self, query, top_k=3, ...):
    ...
    candidates = self._rerank_candidates(query, candidates, top_k * 3)  # 同步调用！

def _rerank_candidates(self, query, candidates, top_k):
    ...
    scores = reranker.predict(pairs)   # transformers 同步 CPU 推理
```

`reranker.predict()` 是纯同步的 CPU 密集调用，在 `async def search()` 里直接执行会**独占事件循环线程**——期间整个 FastAPI 进程的所有请求（包括其他用户的健康检查）都得等它。这是教科书级别的 asyncio 阻塞反模式。

修复：扔到默认线程池。

```python
# 修复后
async def _rerank_candidates_async(self, query, candidates, top_k):
    ...
    reranker = await asyncio.to_thread(self._get_reranker)
    pairs = [[query, c["text"]] for c in candidates]
    scores = await asyncio.to_thread(reranker.predict, pairs)
    ...
```

重启、复测 2 并发——**仍然全部 120s 超时**。隐患是真的，但它解释不了 95 秒级的单发延迟，真凶另有其"人"。

### 直接测 Ollama：把 LLM 链路拆成秒表

不经过业务代码，直接在后端容器里对 provider 的每一步单独计时：

```python
# embed 2.22s  —— bge-m3-cpu，正常
# triage chat 48.14s  —— ？？？分诊不是已经零 LLM 了吗
# dialog chat 46.44s  —— 对话主推理
```

embed 正常，dialog 46s 虽然慢但在 7B 单卡的预期内。问题是 **triage 角色居然还有一次 48s 的 LLM 调用**——2026-09-30 分诊节点已经全面规则化、生产路径不再加载 triage 模型，这次调用是从哪冒出来的？

### 真凶：本地模式下的 LLM 查询重写

顺藤摸到 RAG 的查询重写：

```python
# backend/app/rag/service.py（修复前）
async def _rewrite_query(self, query: str) -> str:
    ...
    # 用 triage 角色快速生成（关思考链，max_tokens 小）
    rewrite = await self.llm.chat(
        "triage",                                  # ← local 模式解析到 qwen2.5:triage-lora
        [{"role": "user", "content": prompt}],
        max_tokens=30,
    )
```

注释写着"快速生成"——这个假设在云端成立（百炼 triage 模型亚秒级），但在本地模式下，`model_for("triage")` 解析到的是 4.4GB 的 `qwen2.5:triage-lora`。一次非寒暄对话的真实开销于是变成：

1. Ollama 加载 triage-lora（4.4GB 冷加载）→ 推理 → **48s**
2. bge-m3 向量检索 → 2.2s
3. Ollama 换载 dialog-lora（另一个 4.4GB，triage 被挤出显存）→ **46s**
4. 质检不合格时重试，每一次重试还要再走一遍

单请求 95s，和线上观测的 95.38s 完全吻合。

**并发为什么是雪崩而不是线性变慢？** Ollama 单实例启动参数是 `-np 1`（单推理 slot），请求严格串行。N 个并发请求交替要求 triage-lora 和 dialog-lora，8GB 显存里两个 4.4GB 模型不断换入换出，缓存命中率趋近于零；所有请求排在一个越来越长的队里，互相等待对方需要的模型被加载。120s 超时只是把这条死队截断了而已。

### 修复

查询重写是云端时代的优化（用一次便宜 LLM 换检索召回率），在本地 7B 上它的代价比整个检索流程还贵，收益完全不成立。按部署形态分流：

```python
# 修复后
async def _rewrite_query(self, query: str) -> str:
    if not query or len(query) > 50:
        return query
    # 本地模式：禁用 LLM 查询重写，避免 triage LoRA 冷加载/推理拖垮链路
    if getattr(self.llm, "is_local", False):
        return query
    # 云端行为不变：亚秒级调用，重写收益成立
    ...
```

## 验证：修复前后对比

同一台机器、同一压测脚本（`docker exec` 容器内执行，登录账号豁免 IP 限流）：

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 2 并发（非寒暄真实链路） | 2/2 超时（120s） | **5.25s / 6.98s** |
| 5 并发 × 60s | 全部超时 | **35/35 成功，P50 5.91s，P95 11.75s，0 错误** |
| 10 并发 × 60s | 10/10 超时 | **37/37 成功，P50 10.2s，P95 18.6s，0 错误** |
| 20 并发 × 120s | — | 0/20 超时（见下节） |

延迟构成（修复后单发非寒暄）：bge-m3 嵌入 ~2.2s + dialog-LoRA 生成 ~3-6s（48 tok/s，含 prompt 处理）+ RAG/质检开销，数字与分步计时对得上。

### 容量边界：20 并发仍然会死，这是物理限制

修复后继续加压，20 并发时请求再次全部超时。这次不是 bug：

- Ollama `-np 1` 单 slot，7B 推理严格串行；
- 按 10 并发 P50 10.2s 估算，稳态吞吐约 1 req/s，20 个请求在 120s 窗口内排不完队；
- 这是**容量边界**而非故障。扩容路径明确：`OLLAMA_NUM_PARALLEL>1`（显存换并发，8GB 卡上空间有限）、vLLM 多实例 + 前置负载均衡、或对话场景继续依赖 SSE 流式（首 token ~1.2s，用户无等待感）削峰。

把"系统在多少并发下失效、为什么、怎么扩"写进压测报告，比一个漂亮但不可信的高 QPS 数字有价值得多。

## 复盘：五条教训

1. **健康检查的 QPS 与业务容量无关。** `/api/health` 不经过任何模型，563 QPS 只能证明 HTTP 栈没坏。压测必须压真实链路，而且要用真实穿过目标分支的输入（"你好"走的是静态话术，不是 LLM 链路）。

2. **`async def` 里每一个同步调用都是定时炸弹。** transformers、requests、`time.sleep`、任何持 GIL 的 CPU 重活，在协程里直接调都会锁死整个进程。代码评审时见到 async 函数里的同步 SDK 调用，条件反射地问一句"要不要 `asyncio.to_thread`"。

3. **"优化"是部署形态相关的，没有免费的通用优化。** 查询重写在云端是亚秒级换召回率的好买卖，在本地 7B 上是 48s 的拖油瓶。同一份代码跑两种部署形态（云 API / 全本地），每个带 LLM 调用的"优化项"都要分别算账。

4. **矛盾现象优先怀疑共享资源。** "单发快、并发死"几乎必然指向被串行化的共享资源：事件循环、GPU、模型显存缓存、数据库连接。排查方法是把链路拆成步骤独立计时——embed 2s / triage 48s / dialog 46s 一出来，问题就结束了。

5. **多模型 LoRA 方案要算显存切换账。** 8GB 卡上放两个 4.4GB 模型，任何让两个模型在同一请求链路中先后出现的设计（哪怕是"顺便"做的查询重写）都会触发反复换载。架构层面的正确方向是单模型统一路由（项目路线图已定），临时方案是严格限制每次请求只触碰一个大模型。

## 附：复现方式

```bash
# 压测脚本（容器内运行，自动注册学生账号绕过匿名限流）
docker exec -e SKIP_HEALTH=1 -e CHAT_VUS=10 -e CHAT_SEC=60 \
  psycheflow-backend uv run python scripts/loadtest/chat_load.py

# 分步计时 LLM 各角色耗时
docker exec psycheflow-backend uv run python -c "
import asyncio
from app.core.llm import provider
async def t():
    ...  # 分别 await provider.embed(...) / provider.chat('triage', ...) / provider.chat('dialog', ...)
asyncio.run(t())
"

# 观察 Ollama 推理 slot 配置与模型加载
docker exec ollama ps aux | grep llama-server   # 注意 -np 1
docker exec ollama ollama ps
```

修复提交涉及文件：

- `backend/app/rag/service.py`：reranker 推理改 `asyncio.to_thread`；`_rewrite_query` 增加本地模式守卫
- `backend/scripts/loadtest/chat_load.py`：压测脚本（health + chat 双场景，报告落 JSON）
