# PsycheFlow 项目交接文档

> 最后更新：2026-09-01
> 当前 commit：`2a34df1`（生产化准备，main 分支）
> 阶段：D 四期全部完成 + 生产化准备，准备进入五期优化（性能/合规/部署）

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
# 期望：176 passed, 1 skipped, 0 failed

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
# 分诊/结构化提取（MoE 架构，分类精准）
MODEL_INTAKE=qwen3.8-2.4t-a95b
# 开放对话/共情（deepseek-v4 有 reasoning_content 思考链，max_tokens 须够大）
MODEL_DIALOG=deepseek-v4-pro-0813
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
```

### 关键模型注意事项

| 模型 | 角色 | 注意 |
|---|---|---|
| `qwen3.8-2.4t-a95b` | intake | MoE 架构，分类精准。如果配额耗尽，次选 `qwen3.8-27b` |
| `deepseek-v4-pro-0813` | dialog | **有 `reasoning_content` 思考链**，max_tokens 须 ≥3000，否则 content 为空 |
| `deepseek-v4-flash-0731` | report | 同上，max_tokens 须 ≥4000 |
| `qwen-audio-3.0-asr-flash` | ASR | DashScope 原生 multimodal-generation HTTP，**不走** OpenAI 兼容协议 |
| `qwen-audio-3.0-tts-flash` | TTS | DashScope 原生 audio/tts HTTP，**不走** SDK WebSocket（SDK 会崩） |

> **deepseek-v4 reasoning_content 坑**：deepseek-v4 系列有思考链字段，会先"思考"再输出 content。max_tokens 太小时被思考链用完，content 为空、`finish_reason=length`。**不是**配额耗尽（已验证两个 deepseek 模型仍有额度）。各节点 max_tokens：triage=500, intervention=3000, reports=4000, llm 默认=2048。

---

## 3. 常用命令速查（Windows PowerShell 版）

| 目标 | 命令 | 备注 |
|---|---|---|
| 启动/重启 | `docker compose up -d --build` | **重建 chroma 容器会清空向量索引**，之后必须 build_index() |
| 重启单服务 | `docker restart psycheflow-backend` | 仅重启进程，**不会重新读 .env**。改 .env 必须用 `docker compose up -d backend` |
| 看后端日志 | `docker logs psycheflow-backend --tail 50` | 或加 `--since 10m` 看最近 10 分钟 |
| 跑 pytest | `docker exec psycheflow-backend uv run pytest -q --no-header` | 181 passed + 1 skipped |
| 重建 RAG 索引 | `docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` | chroma 被重建后必跑 |
| 跑验证脚本 | `docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` | has_assessment + triage 抽样 |
| 跑性能压测 | `docker exec psycheflow-backend uv run python scripts/perf_bench.py` | 50 并发 health + 10 并发 chat（脚本位于 `backend/scripts/`，容器内 `/app/scripts/`） |
| 前端生产构建 | `docker exec psycheflow-frontend sh -c "npm run build"` | 验证前端编译无错 |
| 进容器 shell | `docker exec -it psycheflow-backend bash` | |
| 生成自签 TLS 证书 | `docker run --rm -v "${PWD}/certs:/certs" alpine:latest sh -c "apk add --no-cache openssl >/dev/null 2>&1; openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /certs/privkey.pem -out /certs/fullchain.pem -subj '/CN=localhost'"` | 内网/开发用；生产放真实证书（Let's Encrypt）到 `./certs/` 同名文件 |
| 验证 nginx 配置 | `docker run --rm --add-host=backend:127.0.0.1 -v "${PWD}/frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "${PWD}/certs:/etc/nginx/certs:ro" nginx:alpine nginx -t` | 改 nginx.conf 后跑；`--add-host` 让 standalone 容器解析 `backend` upstream |
| 生产部署（HTTPS） | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` | 需先生成证书到 `./certs/`；前端 80→443 跳转，4 workers + healthcheck |

### 测对话接口（推荐：容器内 python）

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

---

## 5. 当前模型配置

| Role | 模型 | 用途 | 温度 | max_tokens | 状态 |
|---|---|---|---|---|---|
| intake | `qwen3.8-2.4t-a95b` | triage 意图分类 | 0.1 | 500 | ✅ |
| dialog | `deepseek-v4-pro-0813` | intervention 共情对话 | 0.35 | 3000 | ✅ |
| report | `deepseek-v4-flash-0731` | 报告生成 | 0.1 | 4000 | ✅ |
| embed | `text-embedding-v3` | Chroma RAG 向量化 | — | — | ✅ |
| asr | `qwen-audio-3.0-asr-flash` | 语音识别（DashScope HTTP） | — | — | ✅ |
| tts | `qwen-audio-3.0-tts-flash` | 语音合成（DashScope HTTP） | — | — | ✅ |

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

---

## 8. 待做事项（五期优化）

按优先级排序：

1. ~~**性能压测**~~ ✅（2026-09-01 跑通，commit 待补）：脚本 [backend/scripts/perf_bench.py](backend/scripts/perf_bench.py)，命令 `docker exec psycheflow-backend uv run python scripts/perf_bench.py`。验收数据：
   - `/api/health` x50 并发：50/50 (100%)，总耗时 138ms，平均 123.6ms，P50 123.4ms，P95 127.9ms，QPS 361.2 ✅ 满足 NFR「接口 < 200ms」
   - `/api/chat` x10 并发：10/10 (100%)，全 `agent=intervention`/`crisis=False`，端到端整轮平均 20.9s，P50 20.2s，总耗时 26.8s。注：此处测的是 LangGraph 三节点串行（triage→assessment→intervention）+ deepseek-v4 reasoning_content 思考链的**完整响应时延**，非首 token；NFR-5「首 token < 2s」需流式接口（SSE/streaming）改造后单独测量，当前端到端 ~21s 属已知架构特征非 bug
2. **合规加固深化** ✅（2026-09-01，字段级加密按评估结论暂缓）：
   - ✅ **审计日志双写（文件 + DB）**：新增 `AuditLog` 表（[models.py](backend/app/models.py)），[audit.py](backend/app/core/audit.py) 的 `write_crisis_audit`/`write_report_audit` 落 JSON 文件后镜像写 DB 行（`_db_write_audit`，best-effort 失败仅 warning 不阻断主业务）。单测 `test_crisis_dual_writes_db` + `test_db_write_failure_does_not_block_endpoint` 验证双写一致性 + 不阻断
   - ✅ **授权链复检**：修复 [auth.py](backend/app/api/auth.py) `login_by_label` 教师绕密漏洞——教师账号 password_hash 非空却可凭 label 直接拿 token，绕过密码。现已一律 403（`teacher_requires_password`），必须走 `/login_by_password`。单测 `TestTeacherAuthHardening` 3 例覆盖（教师 label 拒、密码通、学生不受影响）
   - ⏳ **未成年人数据加密评估**（结论：暂不实施字段级加密，当前最优是传输层+访问层加固）：
     - 敏感数据盘点：`User.profile`(name/student_no/grade/klass/gender/age/guardian_phone/school/teacher_email)、`BatchEntry`(student_no/student_name)、`ConversationTurn.content`、`AssessmentRecord.answers/interpretation`
     - 现状保护：传输层 dev HTTP，prod nginx **已上 TLS1.2/1.3 + HSTS + CSP + 全套安全头**（[nginx.conf](frontend/nginx.conf)）；静态层 SQLite `./data/psycheflow.db` Docker volume 无加密；访问层 token=token_hex(32) 分离 + 教师 PBKDF2-SHA256 加盐 + login_by_label 漏洞已修 + 审计 DB 双写
     - 结论：SQLite MVP 实施字段级加密（guardian_phone 走 Fernet/AES）需密钥管理（env 弱密钥 / KMS 过度工程）且破坏 SQL 查询，性价比低。**三步状态**：(a) ✅ 生产 nginx TLS+HSTS+CSP 已完成（2026-09-01，自签证书已生成验证，nginx -t + compose config 双绿）、(b) ⏳ `./data` volume 容器内非 root + 文件 600 权限、(c) ⏳ SQLite 备份文件加密；规模化迁 PostgreSQL 后对 guardian_phone/teacher_email 走 pgcrypto 列级加密
3. ~~**流式接口（首 token 验收前置）**~~ ✅（2026-09-01 SSE 骨架完成，首 token 优化待决策）：
   - **SSE 骨架**：新增 [POST /api/chat/stream](backend/app/api/chat.py)（保留旧 `/api/chat` 向后兼容）。架构 Option C：手动跑 triage→assessment（同步等结果），再用 `provider.stream()` 边生成边推 token；危机路径不流式，推完整 crisis_message 后 close。[llm.py](backend/app/core/llm.py) 加 `stream()`（只 yield `delta.content`，过滤 `reasoning_content` 思考链）。[intervention.py](backend/app/agents/nodes/intervention.py) 重构出 `build_intervention_messages`+`stream_intervention`+`FALLBACK_REPLY`，流式与非流式复用同一套 prompt 拼接。SSE 事件：`agent`(节点切换)/`sources`(RAG 卡片提前推)/`token`(流式)/`crisis`(完整话术)/`error`/`done`
   - **前端**：[api.ts](frontend/src/api.ts) 加 `streamChat`（fetch+ReadableStream 解析 SSE，因 EventSource 不支持 POST+auth）；[ChatPage.tsx](frontend/src/pages/ChatPage.tsx) `send` 改用 `streamChat`，边收 token 边追加到 assistant 气泡，空 assistant turn 在 loading 时显示"思考中"
   - **测试**：187 passed / 1 skipped（+6 流式单测 [test_api_chat_stream.py](backend/tests/test_api_chat_stream.py)：正常 token 序列/危机不流式/空回复 fallback/异常 fallback/未知人格回退/RAG 空不推 sources）
   - **实测首 token（脚本 [sse_first_token.py](backend/scripts/sse_first_token.py)）**：消息"最近考试压力大，睡不好"，事件序列：0.57s agent:triage → 2.96s agent:assessment（triage LLM 分类 ~2.4s）→ 3.20s sources:3 条（RAG ~0.24s）→ **18.08s 首 token** → 20.59s done（回复 198 字）
   - **瓶颈分析**：首 token 18s 远超 NFR-5「<2s」。triage ~2.4s（intake=qwen3.8-2.4t-a95b 可接受），**intervention 首 token 14.9s**（3.20→18.08）是主因——dialog=deepseek-v4-pro-0813 有 `reasoning_content` 思考链，stream 模式下先输出思考链（10-15s）再输出 content，`stream()` 只 yield content 故首 token 要等思考链跑完
   - **首 token 优化选项（待决策）**：(A) dialog 角色换无思考链轻量模型如 `qwen3.8-2.4t-a95b`，首 token 预计 <3s，但共情质量可能降；(B) `llm.py` 加 `dialog_stream` 角色专用流式（qwen3.8），非流式 `/api/chat` 仍用 deepseek-v4-pro（高质量），需 `.env` 加 `MODEL_DIALOG_STREAM`；(C) `stream()` 也推 `reasoning_content` 当"思考中"提示（但泄露内部推理，不推荐）；(D) triage 换更快模型或 detect_crisis 后并行启动（干预需 triage_intent 输入，难并行）
4. **Ollama 本地兜底**：断网/降本场景的灾备方案
5. **多 Provider 切换**：硅基流动等备用 Provider
6. **多租户支持**：按学校/区域隔离数据

---

## 9. Git 提交历史

```
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
- [ ] 跑测试：`docker exec psycheflow-backend uv run pytest -q --no-header` → 176 passed / 1 skipped / 0 failed
- [ ] 遗留项验证：`docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` → has_assessment PASS + triage 9/9
- [ ] 浏览器访问 http://localhost:5174/chat → 看到 StageStepper「1 分诊 2 测评 3 干预 4 升级」
- [ ] 输入「我最近压力大」→ 回复是共情内容（呼吸/放松建议），**不是**含 12355 的危机话术
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
