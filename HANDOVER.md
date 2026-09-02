# PsycheFlow 项目交接文档

> 最后更新：2026-09-02
> 当前 commit：`fd86fae`（合规加固深化 (b)(c) — 容器非 root + SQLite 0600 + 备份 AES 加密）
> 阶段：D 四期全部完成 + 生产化准备 + SSE 首 token 优化（NFR-5 达标）+ Ollama 本地兜底（五期灾备），准备进入五期剩余项（多 Provider / 多租户）

---

## 1. Quick Start（5 步跑起来）

```bash
# 1. Clone + 创建 .env（从 §2 复制模板，API Key 问旧账号要）
git clone <repo_url> E:\Trae\PsycheFlow && cd E:\Trae\PsycheFlow
copy .env.example .env

# 2. 启动 3 容器（首次 build 约 5-10 分钟）
docker compose up -d --build

# 3. 等后端起来后，重建 Chroma 向量索引（重要！compose up 会清 chroma 数据）
docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"
# 期望：{'indexed': 183, 'collection_size': 183}

# 4. 跑测试（验证全绿）
docker exec psycheflow-backend uv run pytest -q --no-header
# 期望：187 passed, 1 skipped, 0 failed

# 5. 浏览器打开
# 前端：http://localhost:5174/chat
# 后端健康检查：http://localhost:8000/docs
```

---

## 2. .env 模板（新账号必须手动创建）

> ⚠️ `.env` 在 `.gitignore` 里，clone 后**不会**自动存在。必须手动创建。
> API Key 脱敏显示，问旧账号要 `sk-e835f544...` 完整值。

```dotenv
# ============ 阿里云百炼平台 ============
DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # ← 完整 Key 问旧账号
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1

# ============ 模型配置（百炼模型） ============
# 结构化提取/计分辅助（MoE 架构，分类精准）
MODEL_INTAKE=qwen3.8-2.4t-a95b
# 意图分类（关思考链 enable_thinking=False，4 类标签输出快；triage 节点专用，首 token 0.38s）
MODEL_TRIAGE=qwen3.8-27b
# 开放对话/共情（deepseek-v4 有 reasoning_content 思考链，max_tokens 须够大）
MODEL_DIALOG=deepseek-v4-pro-0813
# 流式干预专用（关思考链 enable_thinking=False，首 token ~0.58s；SSE /api/chat/stream 用此角色）
MODEL_DIALOG_STREAM=qwen3.8-max
# 高频/兜底/报告辅助（deepseek-v4 有 reasoning_content 思考链）
MODEL_REPORT=deepseek-v4-flash-0731
# RAG 向量化
MODEL_EMBED=text-embedding-v3
# 语音识别（DashScope 原生 multimodal-generation HTTP）
MODEL_ASR=qwen-audio-3.0-asr-flash
# 语音合成（DashScope 原生 audio/tts HTTP）
MODEL_TTS=qwen-audio-3.0-tts-flash
# TTS 音色（见百炼音色列表，可换）
TTS_VOICE=longanhuan_v3.6

# ============ Chroma 向量库 ============
CHROMA_HOST=chroma
CHROMA_PORT=8000

# ============ SQLite ============
SQLITE_PATH=/app/data/psycheflow.db

# ============ FastAPI ============
APP_HOST=0.0.0.0
APP_PORT=8000
FRONTEND_ORIGIN=http://localhost:5173

# ============ 安全（青少年合规） ============
ENABLE_AUDIT_LOG=true
CRISIS_HOTLINE_12355=12355
# SQLite 备份加密口令（合规 c；空则 backup_db.py 拒备份）
BACKUP_PASSPHRASE=

# ============ Ollama 本地兜底（五期；空=禁用保持 cloud-only） ============
# 容器连宿主机：http://host.docker.internal:11434/v1；连 compose ollama 服务：http://ollama:11434/v1
OLLAMA_BASE_URL=
OLLAMA_MODEL=qwen2.5:7b
```

### 关键模型注意事项

| 模型 | 角色 | 注意 |
|---|---|---|
| `qwen3.8-2.4t-a95b` | intake | MoE 架构，分类精准。**有思考链**，max_tokens 须够。**不支持 `enable_thinking=False`**（百炼报 400 restricted to True）。如果配额耗尽，次选 `qwen3.8-27b` |
| `qwen3.8-27b` | triage | 关思考链后无 reasoning_content，首 content 0.38s。triage 意图分类专用（9/9 准确，与 qwen-plus 持平）|
| `qwen3.8-max` | dialog_stream | 关思考链后首 content 0.58s。SSE 流式干预专用（共情质量优先）|
| `deepseek-v4-pro-0813` | dialog | **有 `reasoning_content` 思考链**，max_tokens 须 ≥3000，否则 content 为空 |
| `deepseek-v4-flash-0731` | report | 同上，max_tokens 须 ≥4000 |
| `qwen-audio-3.0-asr-flash` | ASR | DashScope 原生 multimodal-generation HTTP，**不走** OpenAI 兼容协议 |
| `qwen-audio-3.0-tts-flash` | TTS | DashScope 原生 audio/tts HTTP，**不走** SDK WebSocket（SDK 会崩） |

> **deepseek-v4 reasoning_content 坑**：deepseek-v4 系列有思考链字段，会先"思考"再输出 content。max_tokens 太小时被思考链用完，content 为空、`finish_reason=length`。**不是**配额耗尽（已验证两个 deepseek 模型仍有额度）。各节点 max_tokens：triage=50（qwen-plus 无思考链）, intervention=3000, reports=4000, llm 默认=2048。
>
> **qwen3.8 思考链无法关闭坑**：qwen3.8-2.4t-a95b 强制 `enable_thinking=True`（百炼报 400 restricted to True），关不掉。流式场景下思考链阻塞首 content token 5-6s，**不能用**于 SSE 流式。已改用 `qwen-plus`（无思考链）承担 triage 和 dialog_stream 两个角色（commit `fe1a595`）。

---

## 3. 常用命令速查（Windows PowerShell 版）

| 目标 | 命令 | 备注 |
|---|---|---|
| 启动/重启 | `docker compose up -d --build` | **重建 chroma 容器会清空向量索引**，之后必须 build_index() |
| 重启单服务 | `docker restart psycheflow-backend` | 仅重启进程，**不会重新读 .env**。改 .env 必须用 `docker compose up -d backend` |
| 看后端日志 | `docker logs psycheflow-backend --tail 50` | 或加 `--since 10m` 看最近 10 分钟 |
| 跑 pytest | `docker exec psycheflow-backend uv run pytest -q --no-header` | 187 passed + 1 skipped |
| 重建 RAG 索引 | `docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` | chroma 被重建后必跑 |
| 跑验证脚本 | `docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` | has_assessment + triage 抽样 |
| 跑性能压测 | `docker exec psycheflow-backend uv run python scripts/perf_bench.py` | 50 并发 health + 10 并发 chat（脚本位于 `backend/scripts/`，容器内 `/app/scripts/`） |
| **SSE 首 token 实测** | `docker exec psycheflow-backend uv run python scripts/sse_first_token.py` | NFR-5 验证：首 token 应 < 2s（实测 1.72s） |
| **流式 chunk 诊断** | `docker exec psycheflow-backend uv run python scripts/diag_stream.py` | 对比各模型首 content token 时间 + chunk delta 字段结构 |
| 前端生产构建 | `docker exec psycheflow-frontend sh -c "npm run build"` | 验证前端编译无错 |
| 进容器 shell | `docker exec -it psycheflow-backend bash` | |
| 生成自签 TLS 证书 | `docker run --rm -v "${PWD}/certs:/certs" alpine:latest sh -c "apk add --no-cache openssl >/dev/null 2>&1; openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /certs/privkey.pem -out /certs/fullchain.pem -subj '/CN=localhost'"` | 内网/开发用；生产放真实证书（Let's Encrypt）到 `./certs/` 同名文件 |
| 验证 nginx 配置 | `docker run --rm --add-host=backend:127.0.0.1 -v "${PWD}/frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "${PWD}/certs:/etc/nginx/certs:ro" nginx:alpine nginx -t` | 改 nginx.conf 后跑；`--add-host` 让 standalone 容器解析 `backend` upstream |
| 生产部署（HTTPS） | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` | 需先生成证书到 `./certs/`；前端 80→443 跳转，4 workers + healthcheck |

### 测对话接口

**非流式（旧 /api/chat，向后兼容）**：

```bash
docker exec -i psycheflow-backend uv run python -c "
import json,urllib.request
req=urllib.request.Request('http://localhost:8000/api/chat',
    data=json.dumps({'message':'我最近压力大','history':[]}).encode('utf-8'),
    headers={'Content-Type':'application/json'})
d=json.loads(urllib.request.urlopen(req,timeout=60).read())
print(f'agent={d[\"current_agent\"]}, crisis={d[\"crisis\"]}, reply[:80]={d[\"reply\"][:80]}')
"
# 期望：agent=intervention, crisis=False, reply 含共情内容
```

**流式（SSE /api/chat/stream，推荐）**：

```bash
docker exec psycheflow-backend uv run python scripts/sse_first_token.py
# 期望：首 token < 2s（实测 1.72s），事件序列 agent(triage)→agent(assessment)→agent(intervention)→sources→token×N→done
# 危机消息：docker exec psycheflow-backend uv run python scripts/sse_first_token.py --message "我想自杀"
# 期望：首 token N/A（危机不流式），事件 agent(triage)→crisis→done，crisis reply 含 12355
```

---

## 4. 已知坑清单

### P1：`.env` 在 gitignore 里，clone 后不存在
**影响**：后端容器启动时所有 model 字段取默认值。
**解决**：从本文档 §2 手动创建，API Key 问旧账号。

### P2：Windows 宿主机的 `.venv` 目录是 Linux 版废的
**现象**：`pyvenv.cfg` 里写 `home = /usr/local/bin`，Windows 下不可用。
**解决**：所有 python/pytest 命令必须走 `docker exec` + 容器内 `uv run`。

### P3：`docker compose up -d` 会清空 Chroma 向量索引
**现象**：chroma 挂的是 `./data/chroma:/chroma/chroma` bind mount，但重建容器时数据偶发丢失。
**解决**：每次 `docker compose up -d --build` 之后，**必须**跑一次 build_index()。

### P4：`docker restart` 不重新读 .env
**现象**：改了 `.env` 里的 MODEL_INTAKE，`docker restart` 后容器内还是旧值。
**解决**：改 .env 后用 `docker compose up -d backend` 强制重建。

### P5：PowerShell 不支持 `&&` 和 heredoc
**解决**：用 `;` 代替 `&&`；git commit 多行消息用 `-F file` 而非 heredoc。

### P6：PowerShell 中文编码导致 curl 传中文变 `???`
**解决**：用本文档 §3 的 python urllib 模板。

### P7：Windows Docker bind mount 同步不稳定
**现象**：前端 `frontend/src/*` 改了后 Vite HMR 偶发不生效，或 chroma bind mount 数据偶发丢失。
**解决**：改完前端文件后 `docker restart psycheflow-frontend` 等 30 秒；chroma 重建后跑 build_index()。

### P8：deepseek-v4 reasoning_content 导致 content 为空（**重要**）
**现象**：`deepseek-v4-pro-0813` 和 `deepseek-v4-flash-0731` 有 `reasoning_content`（思考链）字段，会先思考再输出 content。max_tokens 太小时（如 triage=20）被思考链用完，content 为空、`finish_reason=length`。
**误判**：之前误以为是 free quota 耗尽，实际 max_tokens 不足。
**解决**：各节点 max_tokens 已调大：triage=500, intervention=3000, reports=4000, llm 默认=2048。commit `8eac960`。

### P9：TTS DashScope SDK WebSocket 崩溃
**现象**：`dashscope` SDK 的 `SpeechSynthesizer` (tts_v2) WebSocket 通道在容器内初始化失败（`'NoneType' has no attribute 'close_frame'`），升级 SDK 到 1.27.2 也无效。
**解决**：改用 DashScope 原生 HTTP API（httpx 直连 `POST /api/v1/services/audio/tts/SpeechSynthesizer`），移除 dashscope SDK 依赖。commit `5aaf5f0`。

### P10：Intervention 节点 LLM 返回空字符串不抛异常
**现象**：LLM 返回 `""` 时不走 except 分支，`final_reply` 为空。
**解决**：intervention 节点加显式空字符串检查，触发 fallback 话术。commit `2a2c783`。

### P11：前端容器不识别新增的 public 目录文件
**现象**：Windows Docker bind mount 可能不立即识别 public 目录新增文件。
**解决**：`docker restart psycheflow-frontend` 后生效。

### P12：qwen3.8 思考链无法关闭，SSE 首 token 阻塞（**重要，NFR-5 关键**）
**现象**：SSE 流式 `/api/chat/stream` 首 token 实测 18.08s，远超 NFR-5「首 token < 2s」。瓶颈：triage（intake=qwen3.8-2.4t-a95b）+ intervention（dialog=deepseek-v4-pro-0813）两节点都有 `reasoning_content` 思考链，stream 模式下先输出思考链（5-15s）再输出 content，`stream()` 只 yield content 故首 token 要等思考链跑完。
**误判 1**：以为 `qwen3.8` 可用 `extra_body={"enable_thinking": False}` 关思考——百炼报 400 `The value of the enable_thinking parameter is restricted to True`，**qwen3.8 强制开启思考**。
**误判 2**：以为关了思考后首 token 4.80s 达标——实际是 `stream()` 抛 BadRequestError 被 `stream_intervention` 捕获走 `FALLBACK_REPLY`，4.80s 是 fallback 一次性 yield 时间（triage 4.43s + 0.37s），非真实流式首 token。
**解决**（commit `fe1a595`）：triage 和 dialog_stream 两个角色都换 **`qwen-plus`**（无思考链模型），实测首 content ~0.5s。triage `max_tokens` 500→50（4 类标签）。`llm.py stream()` 移除 `extra_body`（对 qwen3.8 报 400，qwen-plus 不需要）。实测首 token **1.72s**（triage 0.65s + RAG 0.16s + stream 首 token 0.32s），NFR-5 达标。非流式 `/api/chat` 仍用 deepseek-v4-pro（高质量），流式用 qwen-plus（首 token 快）。
**诊断**：`docker exec psycheflow-backend uv run python scripts/diag_stream.py` 对比各模型 chunk 结构 + 首 content 时间；`scripts/sse_first_token.py` 跑端到端 SSE 首 token 实测。

---

## 5. 当前模型配置

| Role | 模型 | 用途 | 温度 | max_tokens | 状态 |
|---|---|---|---|---|---|
| intake | `qwen3.8-2.4t-a95b` | 结构化提取/计分辅助（有思考链） | 0.1 | 2048 | ✅ |
| triage | `qwen3.8-27b` | triage 意图分类（**关思考链，首 token 0.38s**） | 0.1 | 50 | ✅ |
| dialog | `deepseek-v4-pro-0813` | intervention 非流式共情对话（有思考链） | 0.35 | 3000 | ✅ |
| dialog_stream | `qwen3.8-max` | intervention **SSE 流式**共情对话（关思考链，首 token 0.58s） | 0.35 | 3000 | ✅ |
| report | `deepseek-v4-flash-0731` | 报告生成（有思考链） | 0.1 | 4000 | ✅ |
| embed | `text-embedding-v3` | Chroma RAG 向量化 | — | — | ✅ |
| asr | `qwen-audio-3.0-asr-flash` | 语音识别（DashScope HTTP） | — | — | ✅ |
| tts | `qwen-audio-3.0-tts-flash` | 语音合成（DashScope HTTP） | — | — | ✅ |

> **triage 角色分工演进**：原 intake（qwen3.8）承担 triage 意图分类 + 结构化提取两职，但 qwen3.8 有思考链阻塞 SSE 首 token。commit `fe1a595` 拆分出独立 `triage` 角色配 qwen-plus（无思考链），intake 仍保留 qwen3.8 给结构化提取/计分辅助。后续 qwen-plus 无额度，本次替换为 qwen3.8-27b(triage)+qwen3.8-max(dialog_stream)，两者均经 `_extra_body_for` 关 `enable_thinking=False`（qwen3.8-2.4t-a95b 不可关），实测首 token 1.75s、triage 9/9 准确，NFR-5 仍达标。

---

## 6. 模块入口文件索引

### 后端（backend/app/）

| 路径 | 作用 |
|---|---|
| [main.py](backend/app/main.py) | FastAPI app 入口 + RAG 自动 ingest |
| [api/chat.py](backend/app/api/chat.py) | LangGraph chat 端点（支持 persona_id） |
| [api/voice.py](backend/app/api/voice.py) | D3：语音转写 + 语音合成端点 |
| [api/admin.py](backend/app/api/admin.py) | C 三期：批次管理（CSV/筛查码/导出） |
| [api/screening.py](backend/app/api/screening.py) | C 三期：学生凭筛查码匿名作答 |
| [api/personas.py](backend/app/api/personas.py) | D2：人格元数据端点 |
| [api/auth.py](backend/app/api/auth.py) | 注册/登录（token=secrets.token_hex(32)） |
| [agents/graph.py](backend/app/agents/graph.py) | StateGraph 四节点拓扑 |
| [agents/state.py](backend/app/agents/state.py) | AgentState TypedDict |
| [agents/nodes/triage.py](backend/app/agents/nodes/triage.py) | 分诊（detect_crisis 前置 + LLM） |
| [agents/nodes/assessment.py](backend/app/agents/nodes/assessment.py) | 测评（纯 DB 查询 has_assessment） |
| [agents/nodes/intervention.py](backend/app/agents/nodes/intervention.py) | 干预（RAG + LLM，空回复 fallback） |
| [agents/nodes/escalation.py](backend/app/agents/nodes/escalation.py) | 升级（零 LLM + crisis_message + audit） |
| [agents/personas.py](backend/app/agents/personas.py) | D2：4 人格定义（default/sister/senior/listener） |
| [agents/prompts.py](backend/app/agents/prompts.py) | 角色 prompt 模板 |
| [core/llm.py](backend/app/core/llm.py) | 百炼 LLM Provider（按 role 路由） |
| [core/voice.py](backend/app/core/voice.py) | D3：ASR/TTS 实现（DashScope HTTP） |
| [core/config.py](backend/app/core/config.py) | 配置（读 .env） |
| [core/safety.py](backend/app/core/safety.py) | detect_crisis_with_words / crisis_message |
| [core/audit.py](backend/app/core/audit.py) | write_crisis_audit / write_report_audit |
| [rag/service.py](backend/app/rag/service.py) | RAG build_index / search |
| [reports/service.py](backend/app/reports/service.py) | MHT 报告生成（6 章节 + LLM 发展建议） |
| [reports/templates/report.html](backend/app/reports/templates/report.html) | MHT 报告 HTML 模板 |
| [scales/](backend/app/scales/) | 量表库：PHQ-A / SCARED / SDQ / MHT |
| [db.py](backend/app/db.py) | SQLAlchemy engine + Base |
| [models.py](backend/app/models.py) | Session / AssessmentRecord / ConversationTurn / User / ScreeningBatch / BatchEntry |

### 前端（frontend/src/）

| 路径 | 作用 |
|---|---|
| [App.tsx](frontend/src/App.tsx) | React Router 入口 |
| [api.ts](frontend/src/api.ts) | fetch 封装（含 apiPostForm/apiPostBlob） |
| [pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) | 对话页（含 🎤 录音 + 🔊 朗读 + 人格切换） |
| [pages/ScalePage.tsx](frontend/src/pages/ScalePage.tsx) | 量表选择（PHQ-A/SCARED/SDQ/MHT） |
| [pages/HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) | 历史报告列表 + PDF 下载 |
| [pages/ScreeningPage.tsx](frontend/src/pages/ScreeningPage.tsx) | 学生筛查入口 |
| [pages/admin/](frontend/src/pages/admin/) | 管理后台三页（登录/批次列表/批次详情） |
| [lib/recorder.ts](frontend/src/lib/recorder.ts) | D3：浏览器 WAV 录音器（16kHz PCM） |
| [components/CrisisBanner.tsx](frontend/src/components/CrisisBanner.tsx) | 危机横幅组件 |

### 诊断/验证脚本（backend/scripts/，容器内 /app/scripts/）

| 路径 | 作用 |
|---|---|
| [scripts/sse_first_token.py](backend/scripts/sse_first_token.py) | **NFR-5 验证**：SSE /api/chat/stream 首 token 实测（实测 1.72s） |
| [scripts/diag_stream.py](backend/scripts/diag_stream.py) | 各模型 stream chunk 结构 + 首 content token 对比（诊断思考链阻塞） |
| [scripts/perf_bench.py](backend/scripts/perf_bench.py) | 性能压测：50 并发 health + 10 并发 chat |
| [scripts/verify_leftovers.py](backend/scripts/verify_leftovers.py) | 遗留项验证：has_assessment 链路 + triage 意图抽样 |
| [scripts/voice_api_e2e.py](backend/scripts/voice_api_e2e.py) | D3 语音 ASR/TTS 端到端验证 |
| [scripts/voice_probe.py](backend/scripts/voice_probe.py) | D3 语音 API 单点探测 |
| [scripts/tts_http_diag.py](backend/scripts/tts_http_diag.py) | TTS HTTP API 诊断（DashScope 原生端点） |
| [scripts/diag_deepseek.py](backend/scripts/diag_deepseek.py) | deepseek-v4 reasoning_content 思考链诊断 |

### 部署文件

| 路径 | 作用 |
|---|---|
| [docker-compose.yml](docker-compose.yml) | 开发环境（--reload + Vite HMR） |
| [docker-compose.prod.yml](docker-compose.prod.yml) | 生产 override（4 workers + nginx + healthcheck） |
| [frontend/nginx.conf](frontend/nginx.conf) | nginx 配置（gzip/缓存/安全头/流式代理） |
| [backend/Dockerfile](backend/Dockerfile) | 后端镜像（Python + uv + WeasyPrint） |
| [frontend/Dockerfile](frontend/Dockerfile) | 前端镜像（dev target + prod target） |

---

## 7. 各阶段交付状态

### MVP 一期 ✅（commit 43f1d11 → 0ae4ed5）
- 项目骨架、Docker Compose、后端 Dockerfile
- 量表计分引擎 PHQ-A / SCARED
- 百炼 LLM Provider + RAG 知识库 + Chroma
- FastAPI + React 前端
- 报告 PDF + 危机拦截 + 审计日志
- 注册知情同意链 + 历史报告列表

### B 二期 ✅（commit dd853fb → b81f877）
- LangGraph 四智能体编排（triage→assessment→intervention/escalation）
- RAG 知识库修复（.txt + .md 双 pattern）
- ChatPage 阶段可视化（StageStepper + AgentBadge + CrisisBanner + sources 卡片）
- POST /api/chat 向后兼容 + agent_trace

### C 三期 ✅（commit 3d5253d → 1f1d9d8）
- 教师认证（PBKDF2 加盐哈希 + get_current_teacher）
- 批量筛查 API（CSV 名单 + 6 位筛查码 + 统计聚合）
- 学生凭码匿名作答（规则计分，零 LLM）
- 批次汇总 CSV 导出 + 单个学生 PDF 报告
- 前端管理后台三页 + 学生筛查入口页

### D 四期 ✅（commit 3d15d66 → 5aaf5f0）
- **D1 量表库扩展**（commit 3d15d66）：新增 SDQ + MHT，前端量表选择动态化
- **D2 多角色人格切换**（commit 6496cb7）：4 人格（default/sister/senior/listener），安全底线共享，POST /api/chat 新增 persona_id
- **D3 语音输入/输出**（commit 5aaf5f0）：
  - ASR：DashScope 原生 multimodal-generation HTTP（httpx 直连）
  - TTS：DashScope 原生 audio/tts HTTP（移除 dashscope SDK，规避 WebSocket 崩溃）
  - 前端 WavRecorder（16kHz PCM） + ChatPage 🎤 录音 + 🔊 朗读

### 生产化准备 ✅（commit 2a2c783 → 2a34df1）
- **遗留项收尾**（commit 2a2c783 + 8eac960）：
  - has_assessment 真实 E2E 验证 PASS（Assessment→Intervention prompt 注入上下文）
  - triage 意图标签抽样 9/9 全对（100%）
  - deepseek-v4 reasoning_content 坑修复（增大 max_tokens）
  - Intervention 空回复 fallback 修复
- **生产化部署**（commit 2a34df1）：
  - docker-compose.prod.yml（后端 4 workers + healthcheck，前端 nginx + 80 端口）
  - nginx.conf 优化（gzip / 30d 缓存 / 安全头 / 流式代理 / 10m 上传）
  - Token 安全加固（secrets.token_hex(32) 替代 account_id）

### SSE 流式 + 首 token 优化 ✅（commit 70d2917 → fe1a595）
- **SSE 骨架**（commit 70d2917）：POST /api/chat/stream（保留旧 /api/chat 向后兼容）。手动跑 triage→assessment 同步等结果，再 provider.stream() 边生成边推 token；危机路径不流式推完整 crisis_message 后 close；审计双写不破坏。SSE 事件：agent/sources/token/crisis/error/done。前端 streamChat（fetch+ReadableStream 解析 SSE，不用 EventSource 因不支持 POST+auth）+ ChatPage 边收 token 边显示
- **首 token 优化**（commit fe1a595，NFR-5 达标）：首 token **18.08s → 1.72s**
  - 瓶颈定位：triage（intake=qwen3.8）+ intervention（dialog=deepseek-v4-pro）两节点都有 reasoning_content 思考链，stream 模式下先输出思考链 5-15s 再输出 content
  - 误判排查：qwen3.8 不支持 `enable_thinking=False`（百炼报 400 restricted to True）；以为关思考后 4.80s 达标，实际是 stream() 抛 BadRequestError 被 stream_intervention 捕获走 FALLBACK_REPLY 的假象
  - 解决：triage + dialog_stream 两个角色都换 **qwen-plus**（无思考链，首 content ~0.5s）。新增 `model_triage`/`temp_triage` 配置项 + role="triage" 映射。triage max_tokens 500→50。llm.py stream() 移除 extra_body
  - 时序分解（1.72s）：triage 0.65s + RAG 0.16s + stream 首 token 0.32s
  - 浏览器实测 PASS：agent stepper 分诊→测评→干预实时更新，3 个知识卡片渲染，回复边生成边显示；危机消息红色 banner + 12355 + 无 token 流式
  - 187 passed / 1 skipped（+6 流式单测 test_api_chat_stream.py）
- **模型替换**（2026-09-02，qwen-plus 无额度）：triage→`qwen3.8-27b`、dialog_stream→`qwen3.8-max`，均经 `llm.py._extra_body_for` 关 `enable_thinking=False`（qwen3.8-2.4t-a95b 不可关，max/27b 可关）。实测首 token **1.75s**、triage 准确率 9/9（无退化）、187 passed。NFR-5 仍达标

---

## 8. 待做事项（五期优化）

按优先级排序：

1. ~~**性能压测**~~ ✅（2026-09-01 跑通，commit 待补）：脚本 [backend/scripts/perf_bench.py](backend/scripts/perf_bench.py)，命令 `docker exec psycheflow-backend uv run python scripts/perf_bench.py`。验收数据：
   - `/api/health` x50 并发：50/50 (100%)，总耗时 138ms，平均 123.6ms，P50 123.4ms，P95 127.9ms，QPS 361.2 ✅ 满足 NFR「接口 < 200ms」
   - `/api/chat` x10 并发：10/10 (100%)，全 `agent=intervention`/`crisis=False`，端到端整轮平均 20.9s，P50 20.2s，总耗时 26.8s。注：此处测的是 LangGraph 三节点串行（triage→assessment→intervention）+ deepseek-v4 reasoning_content 思考链的**完整响应时延**，非首 token；NFR-5「首 token < 2s」需流式接口（SSE/streaming）改造后单独测量，当前端到端 ~21s 属已知架构特征非 bug
2. **合规加固深化** ✅（2026-09-01 ~ 09-02，字段级加密按评估结论暂缓，访问层 (b)(c) 于 09-02 完成）：
   - ✅ **审计日志双写（文件 + DB）**：新增 `AuditLog` 表（[models.py](backend/app/models.py)），[audit.py](backend/app/core/audit.py) 的 `write_crisis_audit`/`write_report_audit` 落 JSON 文件后镜像写 DB 行（`_db_write_audit`，best-effort 失败仅 warning 不阻断主业务）。单测 `test_crisis_dual_writes_db` + `test_db_write_failure_does_not_block_endpoint` 验证双写一致性 + 不阻断
   - ✅ **授权链复检**：修复 [auth.py](backend/app/api/auth.py) `login_by_label` 教师绕密漏洞——教师账号 password_hash 非空却可凭 label 直接拿 token，绕过密码。现已一律 403（`teacher_requires_password`），必须走 `/login_by_password`。单测 `TestTeacherAuthHardening` 3 例覆盖（教师 label 拒、密码通、学生不受影响）
   - ⏳ **未成年人数据加密评估**（结论：暂不实施字段级加密，当前最优是传输层+访问层加固）：
     - 敏感数据盘点：`User.profile`(name/student_no/grade/klass/gender/age/guardian_phone/school/teacher_email)、`BatchEntry`(student_no/student_name)、`ConversationTurn.content`、`AssessmentRecord.answers/interpretation`
     - 现状保护：传输层 dev HTTP，prod nginx **已上 TLS1.2/1.3 + HSTS + CSP + 全套安全头**（[nginx.conf](frontend/nginx.conf)）；静态层 SQLite `./data/psycheflow.db` Docker volume 无加密；访问层 token=token_hex(32) 分离 + 教师 PBKDF2-SHA256 加盐 + login_by_label 漏洞已修 + 审计 DB 双写
     - 结论：SQLite MVP 实施字段级加密（guardian_phone 走 Fernet/AES）需密钥管理（env 弱密钥 / KMS 过度工程）且破坏 SQL 查询，性价比低。**三步状态**：(a) ✅ 生产 nginx TLS+HSTS+CSP 已完成（2026-09-01，自签证书已生成验证，nginx -t + compose config 双绿）、(b) ✅ `./data` volume 容器内非 root + 文件 600 权限（2026-09-02，[Dockerfile](backend/Dockerfile) 建 appuser uid 1000 + [docker-compose.prod.yml](docker-compose.prod.yml) `user:"1000:1000"`，dev 仍 root 仅测试数据；[db.py](backend/app/db.py) `restrict_db_file_perms` 启动时收紧 0600，实测 db 文件已变 `-rw-------`，单测 `TestDbFilePerms` 覆盖）、(c) ✅ SQLite 备份文件加密（2026-09-02，[backup_db.py](backend/scripts/backup_db.py) `sqlite3.backup` 一致性拷贝 + `openssl enc -aes-256-cbc -pbkdf2 -iter 100000` 加密，口令从 `BACKUP_PASSPHRASE` 注入空则拒备份；实测产出 .db.enc 且 round-trip 解密回 header=`SQLite format 3` size 一致）；规模化迁 PostgreSQL 后对 guardian_phone/teacher_email 走 pgcrypto 列级加密
3. ~~**流式接口（首 token 验收前置）**~~ ✅（2026-09-01 SSE 骨架 + 首 token 优化完成，**NFR-5 达标**）：
   - **SSE 骨架**（commit 70d2917）：新增 [POST /api/chat/stream](backend/app/api/chat.py)（保留旧 `/api/chat` 向后兼容）。架构 Option C：手动跑 triage→assessment（同步等结果），再用 `provider.stream()` 边生成边推 token；危机路径不流式，推完整 crisis_message 后 close。[llm.py](backend/app/core/llm.py) 加 `stream()`（只 yield `delta.content`，过滤 `reasoning_content` 思考链）。[intervention.py](backend/app/agents/nodes/intervention.py) 重构出 `build_intervention_messages`+`stream_intervention`+`FALLBACK_REPLY`，流式与非流式复用同一套 prompt 拼接。SSE 事件：`agent`(节点切换)/`sources`(RAG 卡片提前推)/`token`(流式)/`crisis`(完整话术)/`error`/`done`
   - **前端**：[api.ts](frontend/src/api.ts) 加 `streamChat`（fetch+ReadableStream 解析 SSE，因 EventSource 不支持 POST+auth）；[ChatPage.tsx](frontend/src/pages/ChatPage.tsx) `send` 改用 `streamChat`，边收 token 边追加到 assistant 气泡，空 assistant turn 在 loading 时显示"思考中"
   - **首 token 优化**（commit fe1a595，NFR-5 达标）：首 token **18.08s → 1.72s**
     - 瓶颈：triage（intake=qwen3.8）+ intervention（dialog=deepseek-v4-pro）两节点都有 reasoning_content 思考链，stream 模式下先输出思考链 5-15s 再输出 content
     - 误判排查：qwen3.8 不支持 `enable_thinking=False`（百炼报 400 restricted to True）；4.80s 假象是 stream() 抛异常走 FALLBACK_REPLY
     - 解决：triage + dialog_stream 两角色都换 **qwen-plus**（无思考链，首 content ~0.5s）。详见 §4 P12 + §7「SSE 流式 + 首 token 优化」段
   - **测试**：187 passed / 1 skipped（+6 流式单测 [test_api_chat_stream.py](backend/tests/test_api_chat_stream.py)：正常 token 序列/危机不流式/空回复 fallback/异常 fallback/未知人格回退/RAG 空不推 sources）
   - ~~**非阻塞后续项**：triage 从 qwen3.8 换 qwen-plus 后，准确率需重测（之前 9/9）。sse 实测 1/1 正确，但样本少，建议跑一轮多消息采样确认不退化~~ ✅ 已验证（2026-09-02）：换 `qwen3.8-27b`（关思考链）后 triage 抽样 9/9 全对（求助/倾诉/咨询各 3），无退化
4. **Ollama 本地兜底** ✅（2026-09-02，断网/降本灾备方案已落地）：
   - **兜底链**：百炼 cloud → Ollama 本地（`ollama_base_url` 非空时启用）→ 节点级硬编码话术。Ollama 仅在 cloud 异常**或**空回复（quota/思考链耗尽）时介入；未配置（`base_url` 空）则保持原 cloud-only 行为，零行为变更。
   - **实现**：[llm.py](backend/app/core/llm.py) 抽出 `_chat_once`/`_stream_once` helper（单次调用不重试不兜底）。`chat()` 捕获 cloud 异常 → 若启用 Ollama 则转本地，cloud 正常返回则不碰 Ollama；双失败返回 `""`（节点级话术兜底）。`stream()` 仅在**未 yield 任何 token**（起始即失败）时切 Ollama，已部分输出则不切（避免拼接错乱）并上抛由 SSE error 事件处理。Ollama 走 OpenAI 兼容端点 `/v1`（`AsyncOpenAI` 复用），`api_key` 填占位非空值（Ollama 不鉴权）。
   - **配置**：[config.py](backend/app/core/config.py) 新增 `ollama_base_url`（空=禁用）/`ollama_model`（默认 `qwen2.5:7b`）；.env.example/.env 加 `OLLAMA_BASE_URL`/`OLLAMA_MODEL`。容器连宿主机用 `http://host.docker.internal:11434/v1`，连 compose 的 ollama 服务用 `http://ollama:11434/v1`。
   - **测试**：[test_llm.py](backend/tests/test_llm.py) +10 例（`TestChatOllamaFallback` 6 + `TestStreamOllamaFallback` 4），全 mock 不依赖真实 Ollama：cloud 异常/空回复→ollama、未启用原样上抛、cloud 正常不碰 ollama、双失败返回空、stream 起始即失败切流、已输出中途断流不切、cloud 正常不碰 ollama、未启用原样上抛。全量 **199 passed / 1 skipped**（+10）。
   - **真实联调**：宿主机未装 Ollama（`localhost:11434` 超时、`ollama` 命令不存在），未做端到端真实兜底。启用步骤：`ollama pull qwen2.5:7b` → 取消 `.env` 中 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` 注释 → `docker compose up -d backend` 重建 → cloud 模型不可用时自动转本地。
5. **多 Provider 切换**：硅基流动等备用 Provider
6. **多租户支持**：按学校/区域隔离数据

---

## 9. Git 提交历史

```
<new> feat: Ollama 本地兜底 — cloud 异常/空回复时回退本地 LLM（llm.py _chat_once/_stream_once + 10 单测）
fd86fae feat: 合规加固深化 (b)(c) — 容器非 root + SQLite 0600 + 备份 AES 加密
09c271e fix: 替换无额度的 qwen-plus — triage=qwen3.8-27b + dialog_stream=qwen3.8-max 关思考链，NFR-5 仍达标(1.75s)
fe1a595 feat: SSE 首 token 优化 18s→1.7s（NFR-5 达标）— triage+dialog_stream 换 qwen-plus 无思考链
70d2917 feat: SSE 流式对话 — POST /api/chat/stream 边生成边推 token
d2e3b50 feat: 生产传输层加固 — nginx TLS + HSTS + CSP + 安全头
2a34df1 feat: 生产化准备 — docker-compose.prod.yml + nginx 优化 + token 安全加固
8eac960 fix: deepseek-v4 reasoning_content 思考链导致 content 为空 — 增大 max_tokens，换回 deepseek
2a2c783 fix: 遗留项收尾 — has_assessment 链路 + triage 抽样验证通过，修复 Intervention 空回复 fallback
5aaf5f0 D 四期(D3)：语音输入+输出 — ASR+TTS 全链路打通，TTS 走原生 HTTP 而非 SDK WebSocket
6496cb7 D 四期(D2)：多角色人格切换 — 干预 Agent 支持 4 人格，安全底线全人格共享
3d15d66 D 四期(D1)：量表库扩展 — 新增 SDQ 和 MHT，前端量表选择动态化
f1da4aa docs(handover): 交接文档入库并更新至 C 三期完成状态
1f1d9d8 C 三期(前端)：管理后台三页 + 学生筛查入口页
3d5253d C 三期(后端)：批量筛查 API + 教师认证 + 批次统计聚合
b81f877 docs(review): close 3 Findings — switch MODEL_INTAKE to qwen3.8-2.4t-a95b
dd853fb B 二期：LangGraph 四智能体编排 + RAG .md 修复 + ChatPage 阶段可视化
0ae4ed5 MVP 补齐验收：注册知情同意链 + 历史报告列表 + 对话&危机审计日志 + RAG知识库真实语料入库
43f1d11 初始提交：PsycheFlow 智能心理评估系统 MVP 完整版
```

---

## 10. 验证 Checklist（新账号交接验收）

新账号完成 clone + .env + compose up 后，**逐项验证**：

- [ ] `docker ps` 显示 3 容器 Up（psycheflow-backend / psycheflow-frontend / psycheflow-chroma）
- [ ] 重建 RAG 索引：`docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` 输出 `{'indexed': 183, ...}`
- [ ] 跑测试：`docker exec psycheflow-backend uv run pytest -q --no-header` → 189 passed / 1 skipped / 0 failed
- [ ] 遗留项验证：`docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` → has_assessment PASS + triage 9/9
- [ ] **SSE 首 token 验证（NFR-5）**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py` → 首 token < 2s（实测 1.75s，triage=qwen3.8-27b/dialog_stream=qwen3.8-max 关思考链），事件序列 agent(triage)→agent(assessment)→agent(intervention)→sources→token×N→done
- [ ] **SSE 危机验证**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py --message "我想自杀"` → 首 token N/A（危机不流式），crisis 事件含 12355
- [ ] 浏览器访问 http://localhost:5174/chat → 看到 StageStepper「1 分诊 2 测评 3 干预 4 升级」
- [ ] 输入「我最近压力大」→ 回复是共情内容（呼吸/放松建议），**不是**含 12355 的危机话术；**文字应逐字出现**（SSE 流式），非一次性出现
- [ ] 输入「我想自杀」→ CrisisBanner 出现 + 回复含 12355 + sources 为空 + current_agent=escalation
- [ ] 输入「重度抑郁症状」→ sources 里有 `ccmd3_summary.md`
- [ ] C 三期：访问 http://localhost:5174/admin/login → 注册教师账号 → 创建批次 → /screening 输码作答
- [ ] D2 人格切换：对话页底部出现 4 个人格选择芯片
- [ ] D3 语音：对话页有 🎤 按钮 → 录音 → 转写文字到输入框 → 发送 → AI 回复下方有 🔊 朗读按钮
- [ ] 浏览器访问 http://localhost:8000/docs → FastAPI Swagger UI 正常
- [ ] 生产构建验证：`docker exec psycheflow-frontend sh -c "npm run build"` 无错误
- [ ] compose 语法验证：`docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet` 无错误
- [ ] 审计 DB 双写：`docker exec psycheflow-backend uv run pytest tests/test_audit.py::TestAudit::test_crisis_dual_writes_db -q` 通过
- [ ] 授权链加固：`docker exec psycheflow-backend uv run pytest tests/test_auth.py::TestTeacherAuthHardening -q` 通过（教师凭 label 登录应 403）
- [ ] 合规 b-文件权限：`docker exec psycheflow-backend ls -la /app/data/psycheflow.db` → `-rw-------`（0600，启动时 restrict_db_file_perms 收紧）；`docker exec psycheflow-backend uv run pytest tests/test_db.py::TestDbFilePerms -q` 通过
- [ ] 合规 b-非 root：`docker exec psycheflow-backend id appuser` → uid=1000（镜像已建用户；生产经 docker-compose.prod.yml `user:"1000:1000"` 启用，部署前须 `chown -R 1000:1000 ./data ./logs`）
- [ ] 合规 c-备份加密：`docker exec psycheflow-backend uv run python scripts/backup_db.py` → 产出 `/app/data/backups/psycheflow-*.db.enc`（空 BACKUP_PASSPHRASE 拒备份）；解密验证 `openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 100000 -pass pass:$BACKUP_PASSPHRASE -in <enc> -out restored.db` → header=`SQLite format 3`
- [ ] Ollama 兜底单测：`docker exec psycheflow-backend uv run pytest tests/test_llm.py::TestChatOllamaFallback tests/test_llm.py::TestStreamOllamaFallback -q` → 10 passed。默认 `OLLAMA_BASE_URL=` 空 = 禁用（cloud-only 行为不变）；启用需宿主机先 `ollama pull qwen2.5:7b` 再取消 `.env` 中 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` 注释 + `docker compose up -d backend` 重建
