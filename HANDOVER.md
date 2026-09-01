# PsycheFlow 项目交接文档

> 最后更新：2026-09-01
> 当前 commit：`1f1d9d8`（C 三期完成，main 分支）
> 阶段：C 三期完成（B 端管理后台 + 批量筛查），准备进入 D 四期（量表库扩展/语音输入）

---

## 1. Quick Start（5 步跑起来）

```bash
# 1. Clone + 创建 .env（从 §2 复制模板，API Key 问旧账号要）
git clone <repo_url> E:\Trae\PsycheFlow && cd E:\Trae\PsycheFlow
copy .env.example .env

# 2. 启动 3 容器（首次 build 约 5-10 分钟）
docker compose up -d --build

# 3. 等后端起来后，重建 Chroma 向量索引（重要！compose up 会清 chroma 数据）
docker exec psycheflow-backend /app/.venv/bin/python -c "
import asyncio
from app.rag.service import rag_service
print(asyncio.run(rag_service.build_index()))
"
# 期望：{'indexed': 183, 'collection_size': 183}

# 4. 跑测试（验证全绿）
docker exec psycheflow-backend /app/.venv/bin/python -m pytest -q
# 期望：124 passed, 1 skipped, 0 failed

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

# ============ 模型配置（各司其职）========
# triage 意图分类 + 报告生成（原 qwen3.7-plus 额度耗尽，已替换为 qwen3.8-2.4t-a95b）
MODEL_INTAKE=qwen3.8-2.4t-a95b
# intervention 共情对话（deepseek-v4-pro-0813 额度仍够）
MODEL_DIALOG=deepseek-v4-pro-0813
# 高频兜底
MODEL_REPORT=deepseek-v4-flash-0731
# RAG 向量化
MODEL_EMBED=text-embedding-v3

# ============ Chroma 向量库 ============
CHROMA_HOST=chroma
CHROMA_PORT=8000

# ============ SQLite ============
SQLITE_PATH=/app/data/psycheflow.db

# ============ FastAPI ============
APP_HOST=0.0.0.0
APP_PORT=8000
FRONTEND_ORIGIN=http://localhost:5173

# ============ 安全（青少年合规）========
ENABLE_AUDIT_LOG=true
CRISIS_HOTLINE_12355=12355
```

### 为什么 MODEL_INTAKE 是 qwen3.8-2.4t-a95b？

原 `qwen3.7-plus` 百炼免费配额耗尽（403 FreeTierOnly），triage LLM 全 fallback "倾诉"，导致 intervention 回复内容是 crisis_message 兜底话术。换成 `qwen3.8-2.4t-a95b`（MoE 架构 95B 活跃参数）后 5 场景全部正常。如果新账号 2.4t 也耗尽了，次选是 `qwen3.8-27b`（dense 架构，中文理解能力略弱一档但够用）。

---

## 3. 常用命令速查（Windows PowerShell 版）

| 目标 | 命令 | 备注 |
|---|---|---|
| 启动/重启 | `docker compose up -d --build` | **重建 chroma 容器会清空向量索引**，之后必须 build_index() |
| 重启单服务 | `docker restart psycheflow-backend` | 仅重启进程，**不会重新读 .env**。改 .env 必须用 `docker compose up -d backend` |
| 看后端日志 | `docker logs psycheflow-backend --tail 50` | 或加 `--since 10m` 看最近 10 分钟 |
| 跑 pytest | `docker exec psycheflow-backend /app/.venv/bin/python -m pytest -q` | 必须用容器内 venv python |
| 重建 RAG 索引 | `docker exec psycheflow-backend /app/.venv/bin/python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` | chroma 被重建后必跑 |
| 跑对话 curl | 见下方 python 模板 | PowerShell 不支持 `&&`，curl 传中文会变 `???` |
| 测对话接口 | 见下方 python 模板 | 推荐方式 |
| 进容器 shell | `docker exec -it psycheflow-backend bash` | |

### 测对话接口（推荐：容器内 python）

```bash
docker exec -i psycheflow-backend python -c "
import json,urllib.request
req=urllib.request.Request('http://localhost:8000/api/chat',
    data=json.dumps({'message':'我最近压力大','history':[]}).encode('utf-8'),
    headers={'Content-Type':'application/json'})
print(urllib.request.urlopen(req,timeout=60).read().decode())
"
# 期望：reply 含共情内容（呼吸/放松建议），agent_trace 含 intervention
```

### 测对话接口（PowerShell 友好写法）

```powershell
$py = @'
import json,urllib.request
req=urllib.request.Request("http://localhost:8000/api/chat",
    data=json.dumps({"message":"我想自杀","history":[]}).encode("utf-8"),
    headers={"Content-Type":"application/json"})
d=json.loads(urllib.request.urlopen(req,timeout=60).read())
print(f"crisis={d['crisis']}, reply[:80]={d['reply'][:80]}")
'@
$py | docker exec -i psycheflow-backend python -
```

---

## 4. 已知坑清单

### P1：`.env` 在 gitignore 里，clone 后不存在
**影响**：后端容器启动时所有 model 字段取默认值（MODEL_INTAKE=qwen3.7-plus 已耗尽）。
**解决**：从本文档 §2 手动创建，API Key 问旧账号。

### P2：Windows 宿主机的 `.venv` 目录是 Linux 版废的
**现象**：`pyvenv.cfg` 里写 `home = /usr/local/bin`，Windows 下不可用。
**解决**：所有 python/pytest 命令必须走 `docker exec` + 容器内 `/app/.venv/bin/python`。

### P3：`docker compose up -d` 会清空 Chroma 向量索引
**现象**：compose 文件里 chroma 挂的是 `./data/chroma:/chroma/chroma` bind mount，但重建容器时数据偶发丢失（Windows Docker bind mount 同步不稳定）。
**解决**：每次 `docker compose up -d --build` 之后，**必须**跑一次 build_index()（见 §3 命令表）。

### P4：`docker restart` 不重新读 .env
**现象**：改了 `.env` 里的 MODEL_INTAKE，`docker restart` 后容器内还是旧值。
**解决**：改 .env 后用 `docker compose up -d backend` 强制重建。

### P5：PowerShell 不支持 `&&` 和 `tail`
**解决**：用 `;` 代替 `&&`；用 `Select-Object -Last N` 代替 `tail -N`。

### P6：PowerShell 中文编码导致 curl 传中文变 `???`
**解决**：curl 传中文 message 会乱码，用本文档 §3 的 python urllib 模板。

### P7：Windows Docker bind mount 同步不稳定
**现象**：前端 `frontend/src/*` 改了后 Vite HMR 偶发不生效，或 chroma bind mount 数据偶发丢失。
**解决**：改完前端文件后 `docker restart psycheflow-frontend` 等 30 秒；chroma 重建后跑 build_index()。
**长期方案**（还没做）：换 named volume 替代 bind mount。

### P8：百炼 API 免费额度会耗尽（403 FreeTierOnly）
**现象**：triage / intervention 节点 LLM 返回 403，fallback 触发兜底话术。
**解决**：换另一个模型（当前是 qwen3.8-2.4t-a95b），或百炼控制台切付费。**对话全链路失效**是这个原因，第一时间查 `docker logs` 是否有 403。

---

## 5. 当前四智能体模型配置

| Role | 模型 | 用途 | 温度 | 状态 |
|---|---|---|---|---|
| intake | `qwen3.8-2.4t-a95b` | triage 意图分类 + report 报告生成 | 0.1 | ✅ 在用 |
| dialog | `deepseek-v4-pro-0813` | intervention 共情对话 | 0.35 | ✅ 在用 |
| report | `deepseek-v4-flash-0731` | 高频兜底 | 0.1 | ✅ 在用 |
| embed | `text-embedding-v3` | Chroma RAG 向量化 | — | ✅ 在用 |

> 百炼 API Key 配额紧张时，可把 intake 降级到 `qwen3.8-27b`（足够输出 1 个意图标签），dialog 保留 2.4t。

---

## 6. 模块入口文件索引

### 后端（E:\Trae\PsycheFlow\backend\app\）

| 路径 | 作用 |
|---|---|
| [main.py](backend/app/main.py) | FastAPI app 入口 |
| [api/chat.py](backend/app/api/chat.py) | LangGraph chat 端点（唯一入口） |
| [api/admin.py](backend/app/api/admin.py) | C 三期：批次创建(CSV+筛查码)/列表/详情统计/关闭/导出/学生PDF（get_current_teacher 守卫） |
| [api/screening.py](backend/app/api/screening.py) | C 三期：学生凭筛查码匿名作答（规则计分+条目完成标记，零 LLM） |
| [agents/graph.py](backend/app/agents/graph.py) | StateGraph 四节点拓扑 |
| [agents/state.py](backend/app/agents/state.py) | AgentState TypedDict |
| [agents/nodes/triage.py](backend/app/agents/nodes/triage.py) | 分诊（detect_crisis 前置 + role=intake LLM） |
| [agents/nodes/assessment.py](backend/app/agents/nodes/assessment.py) | 测评（纯 DB 查询，零 LLM） |
| [agents/nodes/intervention.py](backend/app/agents/nodes/intervention.py) | 干预（RAG + role=dialog LLM） |
| [agents/nodes/escalation.py](backend/app/agents/nodes/escalation.py) | 升级（零 LLM + crisis_message + audit） |
| [agents/prompts.py](backend/app/agents/prompts.py) | 角色 prompt 模板 |
| [core/llm.py](backend/app/core/llm.py) | 百炼 LLM Provider（按 role 路由 + 温度） |
| [core/config.py](backend/app/core/config.py) | 配置（读 .env） |
| [core/safety.py](backend/app/core/safety.py) | detect_crisis_with_words / crisis_message（12355 硬编码） |
| [core/audit.py](backend/app/core/audit.py) | write_crisis_audit / write_report_audit |
| [rag/service.py](backend/app/rag/service.py) | RAG build_index / search（.txt + .md 双 pattern） |
| [db.py](backend/app/db.py) | SQLAlchemy engine + Base |
| [models.py](backend/app/models.py) | Session / AssessmentRecord / ConversationTurn / User |

### 前端（E:\Trae\PsycheFlow\frontend\src\）

| 路径 | 作用 |
|---|---|
| [App.tsx](frontend/src/App.tsx) | React Router 入口 |
| [pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) | 对话页（含 StageStepper + AgentBadge + CrisisBanner + sources 卡片） |
| [pages/ScalePage.tsx](frontend/src/pages/ScalePage.tsx) | PHQ-A + SCARED 双量表 |
| [pages/HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) | 历史报告列表 + PDF 下载 |
| [pages/RegisterPage.tsx](frontend/src/pages/RegisterPage.tsx) | 注册 + 4 条知情同意链 |
| [pages/ScreeningPage.tsx](frontend/src/pages/ScreeningPage.tsx) | C 三期：学生输码 → 答题 → 完成态（危机显示 12355 横幅） |
| [pages/admin/AdminLoginPage.tsx](frontend/src/pages/admin/AdminLoginPage.tsx) | C 三期：教师登录/注册（账号名+密码） |
| [pages/admin/AdminBatchesPage.tsx](frontend/src/pages/admin/AdminBatchesPage.tsx) | C 三期：批次列表 + 创建（CSV模板/上传、筛查码卡片复制） |
| [pages/admin/AdminBatchDetailPage.tsx](frontend/src/pages/admin/AdminBatchDetailPage.tsx) | C 三期：批次详情（进度轮询/严重度分布/按班级/危机名单/导出/报告） |

### 数据目录

| 路径 | 内容 |
|---|---|
| `data/knowledge/` | RAG 语料（.txt + .md 共 9 篇） |
| `data/psycheflow.db` | SQLite 数据库（Session / AssessmentRecord / ConversationTurn / User） |
| `logs/crisis_*.json` | 危机审计日志 |
| `logs/report_*.json` | 报告生成审计日志 |

---

## 7. Git 提交历史

```
1f1d9d8 C 三期(前端)：管理后台三页 + 学生筛查入口页
3d5253d C 三期(后端)：批量筛查 API + 教师认证 + 批次统计聚合
b81f877 docs(review): close 3 Findings — switch MODEL_INTAKE to qwen3.8-2.4t-a95b
dd853fb B 二期：LangGraph 四智能体编排 + RAG .md 修复 + ChatPage 阶段可视化
c0af470 Spec Mode Review：MVP 补齐验收（A1~A4）独立复核通过
0ae4ed5 MVP 补齐验收：注册知情同意链 + 历史报告列表 + 对话&危机审计日志 + RAG知识库真实语料入库
4f1c548 修复：发展建议为空 + 报告色调回归MHT官方严肃风
b035a89 报告 PDF 二次升级：整体单页输出 + 因子雷达图 + 视觉观感全面优化
43f1d11 初始提交：PsycheFlow 智能心理评估系统 MVP 完整版
```

### 两份关键 Spec/Review 文档

- `.trae/specs/stage2-multi-agent/` — B 二期 spec.md + tasks.md + review.md（10 AC 全 PASS）
- `.trae/specs/mvp-tail-spec/` — MVP 补齐验收 spec.md + tasks.md + review.md（13 AC，11 PASS + 2 PARTIAL）

---

## 8. B/C 期交付状态 & 下一步

### B 二期已完成（commit b81f877）

- ✅ LangGraph StateGraph 四节点拓扑 + conditional_edges 路由
- ✅ triage（detect_crisis 前置 + role=intake LLM，零幻觉）
- ✅ assessment（纯 DB 查询，零 LLM）
- ✅ intervention（RAG + role=dialog LLM，含 history 拼接）
- ✅ escalation（零 LLM + crisis_message 12355 硬编码 + crisis_*.json 落盘）
- ✅ RAG `load_corpus` 修复 `.txt + .md` 双 pattern（原只有 .txt）
- ✅ ChatPage StageStepper + AgentBadge + CrisisBanner + sources 卡片
- ✅ 向后兼容：POST /api/chat 旧 schema 不变，新增 current_agent + agent_trace 可选
- ✅ pytest **102 passed / 1 skipped / 0 failed**
- ✅ 3 Review Findings 全部关闭

### C 三期已完成（commits 3d5253d + 1f1d9d8，2026-09-01）

- ✅ 教师认证：注册需密码（PBKDF2 加盐哈希）+ `POST /api/auth/login_by_password` + `get_current_teacher` 依赖（非教师 403）
- ✅ 批量筛查：`POST /api/admin/batches`（CSV 名单解析 + 6 位唯一筛查码，去 0/O/1/I）、列表、详情聚合统计（完成率/severity 分布/按班级，**零 LLM**）
- ✅ 学生入口：`GET /api/screening/{code}` + `POST /api/screening/{code}/submit`（凭码匿名作答，规则计分复用量表引擎，防重复提交）
- ✅ 危机管理：crisis_level=elevated 学生进入批次详情危机名单置顶区（转介链：学校心理老师 → 家长 → 12355）
- ✅ 产出物：批次汇总 CSV 导出（UTF-8 BOM 兼容 Excel）、单个学生 PDF 报告（复用 WeasyPrint 链路）
- ✅ 前端：`/admin` 三页（登录/批次列表/批次详情，进度 30s 轮询）+ `/screening` 学生输码页
- ✅ 旧库迁移：`users.password_hash` 列自动补齐（init_db 幂等）
- ✅ pytest **124 passed / 1 skipped / 0 failed**（新增 22 用例）+ 浏览器 E2E 8 步验收全过
- ⚠️ 未做（按 MVP 裁剪）：多租户、WebSocket 实时进度（用 30s 轮询）、.xlsx 解析（仅 CSV）

### 下一步（D 四期，未启动）

开发计划 §9.4：量表库扩展（SDQ、MSSMHS、Big Five）、多角色人格切换、语音输入（qwen-audio ASR）。尚未写 spec.md，建议先讨论范围再开工。另可选小项：批次二维码、ADMIN 页面体验打磨。

---

## 9. 验证 Checklist（新账号交接验收）

新账号完成 clone + .env + compose up 后，**逐项验证**：

- [ ] `docker ps` 显示 3 容器 Up（psycheflow-backend / psycheflow-frontend / psycheflow-chroma）
- [ ] `docker exec psycheflow-backend /app/.venv/bin/python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` 输出 `{'indexed': 183, ...}`
- [ ] `docker exec psycheflow-backend /app/.venv/bin/python -m pytest -q` → 124 passed / 1 skipped / 0 failed
- [ ] 浏览器访问 http://localhost:5174/chat → 看到 StageStepper「1 分诊 2 测评 3 干预 4 升级」
- [ ] 输入「我最近压力大」→ 回复是共情内容（呼吸/放松建议），**不是**含 12355 的危机话术
- [ ] 输入「我想自杀」→ CrisisBanner 出现 + 回复含 12355 + sources 为空 + current_agent=escalation
- [ ] 输入「重度抑郁症状」→ sources 里有 `ccmd3_summary.md`（修 MVP Finding-2 的实证）
- [ ] C 三期：访问 http://localhost:5174/admin/login → 注册教师账号 → 创建批次（CSV）→ `/screening` 输码作答 → 批次详情进度/危机名单正常
- [ ] 浏览器访问 http://localhost:8000/docs → FastAPI Swagger UI 正常

