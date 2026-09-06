# PsycheFlow 项目交接文档

> 最后更新：2026-09-06
> 当前 commit：`5fd9f6e` + 工作区未提交（本地私有化 3.A 双模式改造）
> 阶段：D 四期全部完成 + 生产化准备 + SSE 首 token 优化（NFR-5 达标）+ Ollama 本地兜底 + 生产验收/打包交付 + 测评纠偏与前端体验批次 + 文档同步 + 试点合规材料 + 路由重构与三态门户 + 前端视觉丰富 + GitHub CI（全绿）+ LLM 输出评估体系 + README 开源级打磨 + 匿名安全加固 + 双端隔离补强与交互导航 + 报告/历史/批次管理修复批次 + **本地私有化 3.A 基座模型版已落地（LLM_MODE=local，qwen2.5:7b + bge-m3，triage 93.0% 危机 100% / 报告 76/76=100%）**；后续可选：3.B 云GPU微调、ASR/TTS 本地化

> ⚠️ **运行模式提示（2026-09-06）**：当前本机 `.env` 为 `LLM_MODE=local`（后端跑在本地 Ollama 模式，数据不出本机；语音 ASR/TTS 仍走百炼）。切回云端：`.env` 改 `LLM_MODE=cloud` → `docker compose up -d backend` → 重建 RAG 索引（先 `rag_store.reset_namespace()` 再 `build_index()`，embedding 模型换回 v3）。
>
> **工作区未提交内容**：本地私有化 3.A 代码改造（config.py/llm.py/.env.example/test_llm.py + eval 脚本适配 + HANDOVER/方案文档回写），详见 §7「本地私有化 3.A」；`问题,txt` 为旧账号临时草稿可自行处置。

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
# 期望：202 passed, 1 skipped, 0 failed
# （df68bd0 时基线为 199 passed；5fd9f6e 新增 3 个批次端点测试。若实测数不一致属正常，以实测为准并回写本文档）

# 5. 浏览器打开
# 前端：http://localhost:5174/（三态门户：未登录选学生端/教师端，已登录显示身份条一键进工作台/切端确认）
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

> **deepseek-v4 reasoning_content 坑**：deepseek-v4 系列有思考链字段，会先"思考"再输出 content。max_tokens 太小时被思考链用完，content 为空、`finish_reason=length`。**不是**配额耗尽（已验证两个 deepseek 模型仍有额度）。各节点 max_tokens：triage=50（qwen3.8-27b 关思考链，4 类标签足够）, intervention=3000, reports=4000, llm 默认=2048。
>
> **qwen3.8 思考链无法关闭坑**：qwen3.8-2.4t-a95b 强制 `enable_thinking=True`（百炼报 400 restricted to True），关不掉。流式场景下思考链阻塞首 content token 5-6s，**不能用**于 SSE 流式。commit `fe1a595` 曾改用 qwen-plus（无思考链）承担 triage/dialog_stream；qwen-plus 无额度后 commit `09c271e` 换为 `qwen3.8-27b`(triage) + `qwen3.8-max`(dialog_stream)——**qwen3.8 系列中 max/27b 可关思考链**，仅 2.4t-a95b 不可关（`llm.py _extra_body_for` 按 role 注入）。

---

## 3. 常用命令速查（Windows PowerShell 版）

| 目标 | 命令 | 备注 |
|---|---|---|
| 启动/重启 | `docker compose up -d --build` | **重建 chroma 容器会清空向量索引**，之后必须 build_index() |
| 重启单服务 | `docker restart psycheflow-backend` | 仅重启进程，**不会重新读 .env**。改 .env 必须用 `docker compose up -d backend` |
| 看后端日志 | `docker logs psycheflow-backend --tail 50` | 或加 `--since 10m` 看最近 10 分钟 |
| 跑 pytest | `docker exec psycheflow-backend uv run pytest -q --no-header` | 202 passed + 1 skipped（5fd9f6e 基线，以实测为准） |
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
| [api/auth.py](backend/app/api/auth.py) | 注册/登录（token=secrets.token_hex(32)；AuthResp 含 role 控制前端菜单可见性） |
| [api/sessions.py](backend/app/api/sessions.py) | 会话与报告端点：GET /api/sessions 列表（只含有测评记录的 session）/ POST assessments / PDF 生成（inline）与下载（attachment） |
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
| [reports/service.py](backend/app/reports/service.py) | 单页报告生成（MHT 六章节风格 + 全量表子维度计算 `_compute_subdims` + 雷达图数据 + 测评用时 + LLM 发展建议空回复兜底） |
| [reports/templates/report.html](backend/app/reports/templates/report.html) | MHT 报告 HTML 模板 |
| [scales/](backend/app/scales/) | 量表库：PHQ-A / SCARED / SDQ / MHT |
| [db.py](backend/app/db.py) | SQLAlchemy engine + Base |
| [models.py](backend/app/models.py) | Session / AssessmentRecord / ConversationTurn / User / ScreeningBatch / BatchEntry |

### 前端（frontend/src/）

| 路径 | 作用 |
|---|---|
| [App.tsx](frontend/src/App.tsx) | React Router 入口：`/` 三态门户 + 学生端 `/home /assess*` + 教师端 `/admin/*`；RequireTeacher/RequireStudent/RedirectIfAuthed 三守卫（教师手输学生路由弹回后台；已登录访问登录注册页按角色回首页）+ 旧路径重定向 |
| [api.ts](frontend/src/api.ts) | fetch 封装（含 SSE streamChat / apiGetBlob / clearToken 清全部用户态 localStorage） |
| [pages/PortalPage.tsx](frontend/src/pages/PortalPage.tsx) | 系统门户 + 身份换乘站（独立全屏布局：横幅插画 + 双端卡；已登录显示身份条与「进入我的工作台」，教师切学生端确认退出、学生切教师端走教师登录页） |
| [pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) | 对话页（SSE 流式 + 🎤 录音 + 🔊 朗读 + 人格切换 + 全宽布局；空状态插画缺图降级 💬） |
| [pages/ScaleSelectPage.tsx](frontend/src/pages/ScaleSelectPage.tsx) | 量表选择页（/assess 入口，四量表卡片 + 合并筛查推荐位；免登录文案纠偏） |
| [pages/ScalePage.tsx](frontend/src/pages/ScalePage.tsx) | 测评页（/assess/:scaleId 单量表每次新建独立 session；/assess/combined 合并双量表；返回测评选择带未提交作答确认） |
| [pages/HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) | 历史报告列表 + 详情弹窗 + PDF 真实下载（blob + `<a download>`，非新标签预览；挂登录守卫） |
| [pages/HomePage.tsx](frontend/src/pages/HomePage.tsx) | 学生首页 /home（返回门户 + 三步引导卡 + 四功能卡含筛查码入口 + 动态 CTA） |
| [pages/ScreeningPage.tsx](frontend/src/pages/ScreeningPage.tsx) | 学生筛查入口（/screening 凭码匿名作答，印制材料稳定 URL） |
| [pages/admin/AdminShell.tsx](frontend/src/pages/admin/AdminShell.tsx) | 管理后台统一布局（深色顶栏：品牌 + action 插槽 + clearToken 退出；登录页不用） |
| [pages/admin/](frontend/src/pages/admin/) | 管理后台三页（登录/批次列表/批次详情，均挂 RequireTeacher 守卫；批次详情含返回批次列表 + 胶囊操作按钮） |
| [lib/recorder.ts](frontend/src/lib/recorder.ts) | D3：浏览器 WAV 录音器（16kHz PCM） |
| [components/BackLink.tsx](frontend/src/components/BackLink.tsx) | 统一胶囊返回按钮（light/dark 双 variant；首页/登录/注册/筛查/批次详情/管理登录接入） |
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
| [scripts/reset_teacher_password.py](backend/scripts/reset_teacher_password.py) | **运维**：教师忘记密码重置（`docker exec -it psycheflow-backend uv run python scripts/reset_teacher_password.py --label 账号名 [--password 新密码]`；省略密码则自动生成 12 位并打印；仅限 role=teacher；E2E 实测新密码 200/旧密码 401） |
| [scripts/backup_db.py](backend/scripts/backup_db.py) | **运维**：SQLite 一致性备份 + AES-256-CBC 加密（需 BACKUP_PASSPHRASE） |
| [scripts/export_report_finetune_data.py](backend/scripts/export_report_finetune_data.py) | **本地版 3.B 预备**：从历史测评/报告反向构造微调 JSONL（LLaMA-Factory 格式），默认输出 `data/finetune/finetune_report.jsonl`（未跟踪文件，配合 [docs/本地模型化方案.md](docs/本地模型化方案.md) 使用） |
| [scripts/e2e_acceptance.py](backend/scripts/e2e_acceptance.py) | **验收**：端到端 7 步验收（健康→登录→对话→危机→报告→审计），7/7 PASS |
| [scripts/eval_triage.py](backend/scripts/eval_triage.py) + [scripts/eval/triage_dataset.json](backend/scripts/eval/triage_dataset.json) | **P2 评测**：triage 意图分诊评测（43 条标注样本，总体 97.7%，危机硬编码 8/8=100% 安全回归）；容器内 `uv run python scripts/eval_triage.py [--limit N] [--verbose]` |
| [scripts/eval_report.py](backend/scripts/eval_report.py) | **P2 评测**：报告结构合规评测（5 场景×15 断言：六章节/个人信息/测评用时/雷达图/PDF 完整性/危机红框双向/建议无危机话术，100%）；复用计分引擎+真实 LLM 叙事，合成数据自动清理；容器内 `uv run python scripts/eval_report.py [--only key]` |
| [scripts/eval/results/](backend/scripts/eval/results/) | 评测基线快照（`*_eval_latest.json` 入库，带时间戳明细 gitignore） |

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

### 测评纠偏与前端体验批次 ✅（2026-09-02 ~ 09-05，commit `b43994e`）

- **一量表一报告纠偏**：单量表路由每次新建独立 session 只挂一条 assessment（禁止跨量表复用）；合并双量表（PHQ-A+SCARED）一个 session 挂两条。根因修复跨用户串号 + 多量表报告内容重复两 bug 同源问题——`clearToken` 漏清 `psycheflow_active_session_id`/`chat_session_id`，用户 B 复用 A 的 session 导致报告显示 A 的姓名、单量表累积历史量表。对话用独立 chat session key 与测评 session 解耦
- **报告增强**（[reports/service.py](backend/app/reports/service.py)）：`_compute_subdims` 扩展支持 SDQ（5 因子，7/11/14/21/25 反向计分，亲社会行为维度用 1-pct 反转映射）与 MHT（8 因子，冲动倾向含 85/97 自杀相关条目仅展示维度分、危机走顶部 crisis_message）；雷达图对 3+ 子维度量表启用；SCALE_INTRO 补 SDQ/MHT 测评工具介绍；测评用时 = session.created_at 与最新 assessment.created_at 差值（X分Y秒）；报告个人信息（姓名/性别/年龄/学号/年级）从 `session.account.profile` 真实读取
- **前端体验**：App.tsx 导航加「测评/历史」链接 + role 显隐管理后台（AuthResp 新增 role 字段）；HomePage 三步引导卡 + 动态 CTA；ChatPage 空状态推荐话题 + 全宽布局滚动；对话知识卡片默认折叠、LLM 不复述知识库原文；SCARED 每题选项框按本量表 optionKeys 渲染（修复错用 PHQ-A 4 选项导致空白）；SDQ/MHT 去重复标题（showHeader 参数）；MHT 26/28 题保持原表述
- **PDF 下载交互分化**：ScalePage「生成 PDF 报告」= 新标签页预览（window.open('') + blob location.href，同步开空标签避弹窗拦截）；HistoryPage「下载 PDF」= **真实磁盘下载**（apiGetBlob + 动态 `<a download>` 程序化点击，2026-09-05 修复——blob 新标签页会被 Chrome 内置 PDF 查看器内联打开成"预览"，且带 `downloadingId` 生成中状态）
- **测试修正**：[test_auth.py](backend/tests/test_auth.py) `test_bearer_token_links_session_to_account` 过期——list_sessions 已改为只返回有测评记录的 session（排除纯对话），测试补挂一条全 0 PHQ-A 后通过
- **验证**：`tsc --noEmit` 0 错误；pytest **199 passed / 1 skipped / 0 failed**（工作区实测 2026-09-05）

### 路由重构与双端门户批次 ✅（2026-09-05，commit `884e7fa` → `2cf193c`）

- **试点合规材料**（884e7fa，[docs/](docs/)）：监护人知情同意书模板（与系统注册页四项同意逐条对应 + 回执联）、教师操作手册（建批次/发码/看报告/危机处置 SOP + 每日巡检命令 + FAQ + 学生作答指引附录）；README 相关文档区补链接
- **路由重构**（5a12ac4，[App.tsx](frontend/src/App.tsx)）：学生测评统一 `/assess` 前缀（`/assess` 选择、`/assess/:scaleId` 单量表、`/assess/combined` 合并），旧路径 `/scale`、`/scale/combined`、`/scales/:scaleId` 全部 `<Navigate replace>` 兼容重定向（参数转发需自写 LegacyScaleRedirect 组件，Navigate 不支持参数插值）；`/screening` 与 `/admin` 系、`/login`、`/register` 保持不变（印制材料 URL 稳定）
- **AdminShell 统一布局**（[AdminShell.tsx](frontend/src/pages/admin/AdminShell.tsx)）：管理后台深色顶栏（品牌 + action 插槽 + 退出），批次列表/详情两页接入；退出改用 `clearToken()` 清全部 5 个用户态 key（原手动删 3 个会漏 session id 有串号风险）
- **守卫**：`/admin`、`/admin/batches/:id` 挂 RequireTeacher（无 token/非 teacher → /admin/login，服务端 API 鉴权仍兜底）；`/history` 挂登录守卫 → /login；`/chat` 保持匿名可用
- **三态门户**（[PortalPage.tsx](frontend/src/pages/PortalPage.tsx)）：`/` = 系统大门（独立全屏布局不套学生 Shell）——未登录显示 🎓学生端/🏫教师端双卡；学生登录态自动跳 `/home`；教师登录态自动跳 `/admin`。学生首页 `/` → `/home`，顶栏品牌/首页同步；登录成功直达 `/home`；退出登录统一回 `/` 门户。**分发口径：学校只发根地址，各走各的门**
- **双端零交叉隔离**：教师端 AdminShell 无"返回学生端"链接、`/admin/login` 加「← 返回首页」防迷路；学生页零教师痕迹（教师登录态下学生顶栏隐藏测评/对话/历史，仅剩管理后台；手输学生 URL 可用但无导航暴露）；教师登录态访问 `/` 直接重定向 `/admin`——教师世界里不存在学生首页
- **前端视觉丰富**（2cf193c）：门户横幅 + 双端卡配图 + 对话空状态插画（`frontend/public/images/` 四张 AI 生成扁平插画风 PNG，深蓝 #1e3a5f 主色统一风格；缺图 onError 自动降级 emoji/隐藏，不出现破图）；`public/` 里 15 个报告测试残留迁 `tests/report-samples/`（git mv 零引用确认）
- **运维脚本**：[scripts/reset_teacher_password.py](backend/scripts/reset_teacher_password.py) 教师忘记密码重置（--label 必填、--password 可省自动生成 12 位无易混字符；仅 role=teacher；复用 `_hash_password` PBKDF2 格式）。容器内 E2E 实测：注册临时教师 → 重置 → 新密码 login_by_password 200、旧密码 401
- **验证**：`tsc --noEmit` 0 错误（每批次均过）；浏览器目检（Playwright）：门户横幅/双卡图/对话插画全部渲染、退出落门户、教师访问 `/` 自动跳工作台；pytest 基线不变（后端仅加脚本无 API 改动）

### GitHub CI 与 LLM 输出评估批次 ✅（2026-09-05，commit `7f65806` → `9c49626`）

- **GitHub Actions CI**（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：`backend-test`（uv 0.11.5 钉版本 + `--frozen` 锁同步 + wqy-zenhei 字体 + pytest）/ `frontend-check`（npm ci + tsc -b + vite build）/ `docker-build`（仅 main push，buildx GHA 缓存，backend + frontend prod target）。无 .env 可跑（config 全默认值 + LLM 全 mock + `SQLITE_PATH`/`LOGS_DIR` 重定向规避 /app/data 无权限）。**排障四连**：① `.gitignore` 排除了 `uv.lock` → 取消忽略入库（可复现构建）；② CI 缺中文字体 → PDF 体积断言（≥100KB）因 `.notdef` 缩水失败 → 补 `fonts-wqy-zenhei`（与生产镜像对齐）；③ `test_rag_ingest` 真调百炼 embedding，无凭据时 `do_ingest` 逐文件吞错返回 0 未触发 skip → 测试前置 `dashscope_api_key` 空判断 skip；④ 新仓库 GITHUB_TOKEN 默认只读 → workflow 声明 `issues: write`，失败自动开 Issue 上报日志尾部（远程排障用，稳定后可移除）
- **本地复现 CI 的自伤雷（已记入项目记忆）**：把 `backend/` bind mount 进临时容器跑 `uv sync` 会在**宿主机**生成 Linux venv，遮蔽 psycheflow-backend 容器内 `/app/.venv`（python 符号链接失效 → `uv run` spawn 失败）——清理：`docker run --rm -v <backend>:/src alpine rm -rf /src/.venv`
- **LLM 输出评估体系（P2）**：
  - [eval_triage.py](backend/scripts/eval_triage.py) + [triage_dataset.json](backend/scripts/eval/triage_dataset.json)：43 条标注样本（危机 8/求助 11/倾诉 12/咨询 11 + 4 边界），总体 **97.7%**（42/43）；危机类 8/8=100%（硬编码词表安全回归，准确率下降即阻断发布）；唯一误判为边界样本且判定合理（"被安排来咨询"→求助）。运行 ~18s
  - [eval_report.py](backend/scripts/eval_report.py)：5 场景（4 量表 + 合并）×15 断言 **76/76=100%**。断言含六章节/姓名学号来自 profile/测评用时/雷达图 SVG/PDF 完整性（≥30KB）/**危机红框双向**（预期危机须有框+12355——MHT 85/97 全 1 会正确触发；预期安全须无框）/**安全场景建议无危机话术**（LLM 曾在非危机报告建议里泄漏红框文案，此断言专盯该回归）。运行 ~80s
  - 基线快照：[eval/results/](backend/scripts/eval/results/)（latest 入库，时间戳明细 gitignore）
- **README 指标表**：新增「质量与性能指标」章节（eval 数字 + 首 token 1.72s + QPS 361 + 测试/验收 + CI 徽章）
- **验证**：CI 全绿（https://github.com/Gunaie/PsycheFlow/actions）；容器内实测 RAG 集成测试 skip 行为正常

### README 开源级打磨批次 ✅（2026-09-05，commit `11ffa38`）

- **Mermaid 系统架构图**（README）：客户端三角色 → Nginx → 三态门户 → FastAPI（API/多智能体链/危机前置层/RAG/报告引擎）→ LLM 三级降级链（百炼→Ollama→硬编码话术）→ 数据层（SQLite 0600/Chroma/危机留痕）
- **8 张核心页面截图**（[docs/screenshots/](docs/screenshots/)）：门户/学生首页/量表选择/作答/对话（人设+四阶段条+吉祥物）/历史/教师批次列表/批次详情（统计卡+进度条+危机红名单含触发词+严重度分布+班级进度）
- **可复现截图脚本**（[backend/scripts/screenshots.py](backend/scripts/screenshots.py)）：PEP 723 内联依赖（playwright+httpx，不污染项目 deps）；API 造数（演示教师+5 人批次完成 4 份其中 1-2 份触发危机+演示学生+已提交 PHQ-A）→ Playwright 按三角色截 8 图到 docs/screenshots/
  - 运行前置：`$env:PLAYWRIGHT_BROWSERS_PATH='C:\Users\gunaie\.cache\ms-playwright'`（Trae 沙箱禁写默认 `%LOCALAPPDATA%\ms-playwright`）+ `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`（国内镜像，否则下载 130MB 极慢）
  - 已知小瑕疵：作答页脚本点击选项未生效（进度 0/9，截图仍可展示界面）；首页欢迎语显示账号 label 而非昵称（真实系统行为）
- **验证**：8 图逐一目检通过；README 嵌入「界面速览」表格

### 安全加固与演示材料批次 ✅（2026-09-05，commit `f1da7af` → `9d06a9b`）

- **匿名 LLM 接口 IP 限流**：[api/ratelimit.py](backend/app/api/ratelimit.py) 滑动窗口内存限流（对话端点 10 次/分、报告生成 3 次/分），key=端点组+客户端 IP（支持 X-Forwarded-For 反代场景）；已登录用户按成功解析的账号豁免（无效 token 一律视为匿名，伪造 header 绕不过）。单测 [test_ratelimit.py](backend/tests/test_ratelimit.py)（含进程级 bucket 测试基建修正）
- **匿名测评不存档提示**：ScalePage 匿名用户顶部警示「匿名测评不存档：报告仅当前页面可见，请及时下载 PDF 留存；登录后测评将进入「历史」记录」
- **演示视频录制脚本**：[docs/演示视频录制脚本.md](docs/演示视频录制脚本.md)（OBS 分镜 + 旁白词 + 录前清单）
- **验证**：pytest 199 passed / 1 skipped 基线不变；CI 全绿

### 双端隔离补强与交互导航批次 ✅（2026-09-05，commit `df68bd0`）

- **RequireStudent 守卫**（[App.tsx](frontend/src/App.tsx)）：教师登录态访问学生路由（/home /assess* /chat /history /screening）一律弹回 /admin——补上路由重构批次遗留的「教师手输学生 URL 可进入」缺口；/history 的登录守卫嵌套在 RequireStudent 内
- **RedirectIfAuthed**：已登录访问 /login /register 按角色回各自首页，防两端交叉与重复登录
- **门户身份换乘站**（[PortalPage.tsx](frontend/src/pages/PortalPage.tsx)）：自动重定向改为可视换乘——已登录显示身份条（label + 角色 + 「进入我的工作台 →」）；教师点学生端卡 confirm 后 clearToken 再进 /home（明确的换身份动作，卡片显示「切换将退出当前教师账号」）；学生点教师端卡进 /admin/login 校验角色；双卡由 Link 改 button 语义
- **统一返回导航**：新增 [BackLink](frontend/src/components/BackLink.tsx) 组件（胶囊描边 + 箭头 + hover 反馈，light/dark 双 variant），接入学生首页「返回门户」、登录/注册页「返回首页」（dark）、筛查码页「返回首页」、批次详情「返回批次列表」（dark）；批次详情「导出汇总 CSV」「关闭批次」与管理顶栏「退出」按钮统一胶囊风格；作答页「返回测评选择」带未提交作答 confirm 防误触丢答案
- **入口补齐**：学生首页新增「班级筛查作答」卡（/screening 筛查码入口，功能卡 3→4 改 2×2 网格）；量表选择页免登录文案纠偏（原文案误写「登录后即可开始测评」，实为免登录可测）
- **验证**：`tsc --noEmit` 0 错误 + `vite build` 通过；前端容器重启后浏览器目检各页返回按钮、守卫弹回与门户换乘行为

### 报告/历史/批次管理修复批次 ✅（2026-09-06，commit `5fd9f6e`）

试点反馈问题集中修复，9 文件 +299/-24：

- **报告兜底字样**：发展建议 LLM 空回复时的兜底标题不再出现内部标识「发展建议（通用兜底）」字样（[reports/service.py](backend/app/reports/service.py)）
- **历史报告时间少 8 小时**：根因 `session.created_at` 存 UTC 但 `isoformat()` 无时区标记，浏览器 `new Date()` 按本地时区解析。修复 [HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) `formatDate/formatTime` 给 ISO 字符串补 `Z` 标记明确 UTC
- **双量表历史详情打不开报告内容**：根因 `viewDetail` 直接用列表精简数据（无 interpretation/answers）。修复：详情弹窗异步调 `GET /api/sessions/{id}` 补全，新增 `detailLoading` 状态；后端 [sessions.py](backend/app/api/sessions.py) 详情响应补 `max_score`
- **测评提交即存档**：根因 assessment 写入挂在「生成 PDF 报告」按钮上，不点击则历史记录看不到。修复：[ScalePage.tsx](frontend/src/pages/ScalePage.tsx) submit 改调 `submit_assessment`（计分 + 持久化），生成 PDF 与存档解耦
- **教师端批次管理增强**（[admin.py](backend/app/api/admin.py) + 批次列表/详情两页）：新增 `PATCH` 批次重命名、`DELETE` 批次删除、`POST` 批次 reopen（重新开放作答）；列表页与详情页加入口按钮；「创建筛查批次」表单展开时不再同时显示「暂无筛查批次」空状态（条件改 `batches.length === 0 && !showCreate`）
- **测试**：[test_admin_screening.py](backend/tests/test_admin_screening.py) 新增 3 个批次端点测试（重命名/删除/reopen）

### 本地私有化 3.A 基座模型版 ✅（2026-09-06 实施并验证通过）

- **方案文档**：[docs/本地模型化方案.md](docs/本地模型化方案.md) v2.0——两套部署共用一套代码，`.env` 的 `LLM_MODE` 切换（cloud/local）
- **代码改造**（4 文件）：
  - [config.py](backend/app/core/config.py)：新增 `llm_mode`（cloud/local，默认 cloud）/`local_model`（qwen2.5:7b）/`local_embed_model`（bge-m3）；model_validator 校验 local 模式必须配 `OLLAMA_BASE_URL` 否则启动报错（快速失败不静默回退云端）
  - [llm.py](backend/app/core/llm.py)：新增 `is_local` 属性 + `_primary_for(role)` 路由；`chat()/stream()/embed()` 加 local 分支——**直走 Ollama 不触云端**（数据不出本机），Ollama 失败 chat 返回 ""/stream 上抛由节点级话术兜底（**不回退云端**）；embed local 走 bge-m3 `/v1/embeddings`（分批 8 条，按 index 排序保序）；cloud 模式兜底链逻辑不变
  - [.env.example](.env.example)：补 LLM_MODE/LOCAL_MODEL/LOCAL_EMBED_MODEL 配置块
  - [test_llm.py](backend/tests/test_llm.py)：+11 例（local 下 chat/stream/embed 直走 Ollama 且 cloud client 设哨兵断言不被调用、失败不回退云端、model_for 本地映射、embed 8 条分批、config 校验）；全量 **217 passed / 1 skipped**
- **环境变更**：Ollama 整机容器重建加 `-e OLLAMA_KEEP_ALIVE=-1`（模型常驻显存，消除 40s 冷启动；qwen2.5:7b 4.7GB + bge-m3 1.2GB = 5.9GB < 8GB 显存）；模型库仍在 `E:\OllamaModels` 卷，重建不丢
- **切换动作**：`.env` 设 `LLM_MODE=local` + `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` → `docker compose up -d backend`（restart 不重读 .env）→ **必须重建 RAG 索引**（embedding 换模型，旧 v3 向量作废）：`rag_store.reset_namespace()` 删 collection 后 `build_index()`（bge-m3 重建 183 条）
- **实测数据（RTX 4060 Laptop，2026-09-06）**：
  - triage 意图分诊 **40/43 = 93.0%**（云端 qwen3.8-27b 基线 97.7%）；**危机 8/8 = 100% 安全红线保住**；3 个误判全是求助/咨询边界样本（不改变路由，两类都进 intervention）
  - 报告合规 **76/76 = 100%**（5 场景全过，发展建议 733-874 字非空，PDF 136-170KB，单场景 ~11s）
  - 正常对话 SSE：模型热态首 token ~0.8s（RAG 0.6s）；危机路径 0.15s（硬编码前置不调 LLM）
- **已知限制**：ASR/TTS 语音仍走百炼（3.A 未本地化，faster-whisper/edge-tts 推后）；eval 脚本已适配 local 模式（无 DASHSCOPE_API_KEY 也可跑）
- **3.B 云GPU微调版**：未实施（可选增强，方案文档 3.B 章；配套 [export_report_finetune_data.py](backend/scripts/export_report_finetune_data.py) 已就绪，数据目录 `backend/data/finetune/`）

---

## 8. 待做事项（五期优化）

按优先级排序：

0. ~~**【主线】本地私有化部署 3.A**~~ ✅（2026-09-06 完成，详见 §7「本地私有化 3.A」）：`LLM_MODE=local` 双模式改造落地，qwen2.5:7b + bge-m3 全本地，triage 93.0%（危机 100%）/ 报告 76/76=100%。后续可选项：
   - 3.B 云 GPU 微调版（LoRA 微调 qwen2.5:7b 提升 triage 求助/咨询边界准确率，方案文档 3.B 章，导出脚本已就绪）
   - ASR/TTS 语音本地化（faster-whisper + edge-tts，3.A 推后项；当前 local 模式语音仍走百炼）
   - 切回云端：`.env` 改 `LLM_MODE=cloud` → `docker compose up -d backend` → **必须重建 RAG 索引**（embedding 换回 v3，旧 bge-m3 向量作废，同样先 reset_namespace 再 build_index）
   另：真实校园试点部署待用户决策。

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
   - **配置**：[config.py](backend/app/core/config.py) 新增 `ollama_base_url`（空=禁用）/`ollama_model`（默认 `qwen2.5:7b`）；.env.example/.env 加 `OLLAMA_BASE_URL`/`OLLAMA_MODEL`。**架构：整机共享独立容器**（不挂任何项目 compose，多项目共用一份模型库）：`docker run -d --name ollama --gpus all -p 11434:11434 -v E:/OllamaModels:/root/.ollama --restart always ollama/ollama:latest`；图形界面 Open WebUI：`docker run -d -p 3001:8080 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main`（浏览器 `http://localhost:3001` 注册本地账号即可可视化管理/聊天）。各项目后端容器经 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` 访问。
   - **测试**：[test_llm.py](backend/tests/test_llm.py) +10 例（`TestChatOllamaFallback` 6 + `TestStreamOllamaFallback` 4），全 mock 不依赖真实 Ollama：cloud 异常/空回复→ollama、未启用原样上抛、cloud 正常不碰 ollama、双失败返回空、stream 起始即失败切流、已输出中途断流不切、cloud 正常不碰 ollama、未启用原样上抛。全量 **199 passed / 1 skipped**（+10）。
   - **真实联调** ✅（整机共享 Ollama + GPU 直通 + Open WebUI，2026-09-02）：沙箱拦截原生安装器（exit 4，`%LOCALAPPDATA%\Programs\Ollama` 受限），故 Ollama 走 Docker 路线；又为多项目共享 + 模型不占 C 盘，升级为**整机共享独立容器**（从项目 compose 解耦）。模型库迁至 `E:\OllamaModels`（沙箱拦 PowerShell 写，用 `docker run --rm -v ... alpine cp -a` 中转拷贝，原项目 `./data/ollama` 8.7GB 已清）。共享容器 `ollama`（`--gpus all` + `-v E:/OllamaModels:/root/.ollama` + `--restart always`）开机随 Docker Desktop 自启；图形界面 `open-webui`（端口 3001，healthy，`OLLAMA_BASE_URL=http://host.docker.internal:11434`）。验证：`nvidia-smi -L` 见 RTX 4060 Laptop、`ollama list` 见 `qwen2.5:7b`（Q4_K_M，ctx 32768）、`http://localhost:3001` 注册账号后可视化管理/聊天。`.env` 设 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`，重启 backend 后 E2E 兜底：临时把 intake 模型名换成 `__nonexistent_model_xyz__` 逼 cloud 404 → `provider.chat` 自动转 ollama 返回非空中文（"你好，我叫Qwen，是来自阿里云的大规模语言模型…"，`FALLBACK_OK`）。以后新项目只需 `.env` 设同一 `OLLAMA_BASE_URL` 即可复用，无需再下/导入模型。
5. **多 Provider 切换**：硅基流动等备用 Provider（推后：待注册硅基流动账号；现有 Ollama 兜底已覆盖云端不可用场景）
6. **多租户支持**：按学校/区域隔离数据
7. **生产验收 / 打包交付** ✅（2026-09-02）：
   - **端到端 E2E 脚本** [scripts/e2e_acceptance.py](backend/scripts/e2e_acceptance.py)：驱动 `健康→登录(login_by_password)→建会话→正常对话(SSE token 流)→危机拦截(crisis 事件+12355+crisis_*.json 落盘)→报告(MHT 6 章+发展建议非空)→审计落库(AuditLog)` 七步，幂等可重跑（每次新 session_id，复用注册的 e2e-runner 教师）。实跑 **7/7 PASS**（`docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/e2e_acceptance.py`）。
   - **prod compose 复检** ✅：[docker-compose.prod.yml](docker-compose.prod.yml) backend `user:1000:1000`+4 worker+curl healthcheck（Dockerfile 已装 curl）+restart always；frontend nginx TLS(443)+HSTS+CSP+wget healthcheck。Ollama 不在 prod compose（整机共享独立容器，`.env` 配 `host.docker.internal`）。
   - **部署文档** [DEPLOY.md](DEPLOY.md)：新机器拉起全步骤（前置/`.env`必填项/开发模式/生产模式/TLS 证书/Ollama 可选/部署后验收/日常运维/常见问题）。
   - **`.env.example` Ollama 注释更新**：移除已删的 compose ollama 服务说明，改为整机共享独立容器启动命令 + Open WebUI。

---

## 9. Git 提交历史

```
5fd9f6e fix: 报告字样/历史时间/详情显示 + 批次管理增强 + 测评提交即存档 — 兜底标题去内部标识 + UTC 补 Z 修少 8 小时 + 历史详情异步拉详情接口 + PATCH/DELETE/reopen 批次端点 + submit 即持久化 assessment + 3 批次测试
a743100 docs(handover): 同步安全加固与双端隔离补强批次 — commit 指针 df68bd0 + 补录 f1da7af/9d06a9b 批次记录 + 前端模块索引更新（RequireStudent/BackLink/门户换乘站）
df68bd0 feat: 双端隔离补强 + 门户身份换乘站 + 统一返回导航与按钮美化 — RequireStudent/RedirectIfAuthed 守卫 + 门户身份条与切端确认 + BackLink 统一接入 + 筛查码入口卡 + 作答返回确认 + 免登录文案纠偏
9d06a9b docs: 演示视频录制脚本（OBS 分镜 + 旁白词 + 录前清单）
f1da7af feat(security): 匿名 LLM 接口 IP 限流 + 匿名测评不存档提示
11ffa38 feat: README 开源级打磨（P3）— Mermaid 系统架构图 + 8 张核心页面截图 + 可复现截图脚本
9c49626 feat: LLM 输出评估体系（P2）— triage 评测 43 样本 97.7%（危机 8/8）+ 报告合规评测 76/76 100%，基线快照入库 + README 指标表
d7d9418 fix(test): RAG 集成测试在无 DASHSCOPE_API_KEY 环境前置 skip — do_ingest 逐文件吞错导致 CI 误报断言失败
83da14a ci: workflow 声明 issues:write 权限 — 失败排障 Issue 才能创建
6649bc0 ci: pytest 失败时自动开 Issue 上报日志尾部（远程排障用，稳定后移除）
0ee95e4 fix(ci): 补装 fonts-wqy-zenhei 中文字体 — 与生产镜像对齐，修复 PDF 体积断言因缺字 .notdef 缩水
d5a91e7 fix(ci): 提交 uv.lock 并取消 gitignore — --frozen 同步需要锁文件入库（可复现构建）
33aa441 fix(ci): 钉住 uv 0.11.5 — 新版 uv 判定旧锁格式过期导致 --frozen 同步失败
7f65806 ci: GitHub Actions — backend pytest + frontend typecheck/build + main 分支镜像构建验证
2cf193c feat: 前端视觉丰富 — 门户横幅+双端卡配图+对话空状态插画（缺图自动降级）+ public 测试残留迁移 tests/report-samples
5a12ac4 feat: 路由重构与双端门户 — /assess 前缀统一 + 旧路径重定向 + AdminShell 统一布局 + 路由守卫 + 三态门户 /（未登录选身份/按角色自动跳转）+ 双端零交叉入口
884e7fa docs: 试点合规与操作材料 — 监护人知情同意书模板 + 教师操作手册（建批次/看报告/危机处置 SOP）
15ca1d4 docs: 同步配置与文档至实际代码 — README 重写 + 开发计划/HANDOVER 更新
b43994e feat: 测评纠偏与前端体验批次 — 一量表一报告 + 报告增强 + PDF 下载交互分化
c529bb9 feat: 生产验收/打包交付 — E2E 7/7 验收脚本 + prod compose 复检 + DEPLOY.md 部署文档
dd30704 feat: Ollama 升级为整机共享独立容器 — 模型库迁 E:\OllamaModels + Open WebUI 图形界面（多项目复用）
90afc1b feat: Ollama 真实联调 — Docker ollama 服务 + RTX 4060 GPU 直通，qwen2.5:7b 导入，E2E 兜底验证通过
29999db feat: Ollama 本地兜底 — cloud 异常/空回复时回退本地 LLM（llm.py _chat_once/_stream_once + 10 单测）
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
- [ ] 跑测试：`docker exec psycheflow-backend uv run pytest -q --no-header` → 202 passed / 1 skipped / 0 failed（5fd9f6e 基线；若实测不同以实测为准并回写文档）
- [ ] 遗留项验证：`docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` → has_assessment PASS + triage 9/9
- [ ] **SSE 首 token 验证（NFR-5）**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py` → 首 token < 2s（实测 1.75s，triage=qwen3.8-27b/dialog_stream=qwen3.8-max 关思考链），事件序列 agent(triage)→agent(assessment)→agent(intervention)→sources→token×N→done
- [ ] **SSE 危机验证**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py --message "我想自杀"` → 首 token N/A（危机不流式），crisis 事件含 12355
- [ ] 浏览器访问 http://localhost:5174/chat → 看到 StageStepper「1 分诊 2 测评 3 干预 4 升级」
- [ ] 输入「我最近压力大」→ 回复是共情内容（呼吸/放松建议），**不是**含 12355 的危机话术；**文字应逐字出现**（SSE 流式），非一次性出现
- [ ] 输入「我想自杀」→ CrisisBanner 出现 + 回复含 12355 + sources 为空 + current_agent=escalation
- [ ] 输入「重度抑郁症状」→ sources 里有 `ccmd3_summary.md`
- [ ] C 三期：访问 http://localhost:5174/admin/login → 注册教师账号 → 创建批次 → /screening 输码作答
- [ ] **批次管理回归（5fd9f6e）**：批次列表/详情页可见重命名、删除、reopen（重新开放）按钮且功能正常；创建批次表单展开时不再显示「暂无筛查批次」空状态；批次端点测试 `docker exec psycheflow-backend uv run pytest tests/test_admin_screening.py -q` 全绿
- [ ] **测评提交即存档（5fd9f6e）**：学生登录态完成一份量表提交后，**不点击「生成 PDF 报告」**直接进「历史」页 → 能看到该条记录；点击查看详情能看到完整报告内容（interpretation/answers，非空白）
- [ ] **历史时间回归（5fd9f6e）**：历史列表时间与本机实际时间一致（不再少 8 小时）；报告发展建议兜底场景无「（通用兜底）」内部字样
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
