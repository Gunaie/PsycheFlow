# 步骤 6 实施计划：报告 PDF + 深度危机兜底 + SQLite 会话持久化

## Context

步骤 5 把评估与对话串成可用流程，但刻意保持 stateless：评估结果算完即弃、对话不带记忆、无报告产物。步骤 6 补齐三块：① SQLite 持久化评估记录（会话维度）；② WeasyPrint 生成「心理评估报告」PDF（含 LLM 撰写的建议段）；③ 把硬编码危机兜底接入报告链，确保危机在评分→报告全链路不可被 LLM 抹平。

现状就绪度：Dockerfile 已装 WeasyPrint 系统库（cairo/pango/gdk-pixbuf）；pyproject 已有 sqlalchemy/weasyprint/jinja2/markdown；config 已有 sqlite_path=/app/data/psycheflow.db、model_report=deepseek-v4-flash-0731、temp_report=0.1、crisis_hotline_12355=12355、enable_audit_log=True。**无任何 DB 层、无 templates 目录**，全部从零建。

## 复用现有能力

- `app/scales/registry.py` 的 `get_scale(id).score(answers)` —— 会话内评分复用
- `app/scales/base.py` 的 `ScoreResult.to_dict()` —— 评估记录字段直接来自它
- `app/core/safety.py` 的 `crisis_message()`/`detect_crisis()` —— 报告危机兜底
- `app/core/llm.py` 的 `provider.chat(role="report", ...)` —— 报告建议段生成
- 前端 `api.ts` 的 `apiGet/apiPost` —— 报告按钮复用

---

## 后端改动

### 1. DB 层（新文件 `app/db.py` + `app/models.py`）

`app/db.py`
- `engine = create_engine(settings.sqlite_path, connect_args={...})`
- `SessionLocal`（sessionmaker factory）
- `Base`（declarative_base）
- `init_db()`：`Base.metadata.create_all(engine)`，幂等建表

`app/models.py`（SQLAlchemy 2.x 声明式）
- `Session`：id(str uuid4)、label(str 可空，匿名昵称/编号，不收真实姓名)、created_at
- `AssessmentRecord`：id、session_id(FK CASCADE)、scale_id、scale_name、total_score、severity、crisis_level、crisis_triggers(JSON)、interpretation、answers(JSON，留档可审计)、created_at

`app/main.py`：启动时 `init_db()`（FastAPI lifespan 或模块级调用，幂等）

### 2. 会话与报告 API（新文件 `app/api/sessions.py`）

- `POST /api/sessions` body {label?} → {session_id, created_at}
- `POST /api/sessions/{session_id}/assessments` body {scale_id, answers}
  - 复用 `get_scale(scale_id).score(answers)`（int 键转换逻辑同 scales.py）
  - 持久化 `AssessmentRecord`，返回 `ScoreResult.to_dict()` + `assessment_id`
  - ValueError→422，KeyError→404
- `GET /api/sessions/{session_id}` → session + assessments 列表
- `POST /api/sessions/{session_id}/report` → 生成 PDF，`Response(media_type="application/pdf", content=bytes)`，`Content-Disposition: inline`（浏览器内打开）

### 3. 报告服务（新文件 `app/reports/service.py` + `app/reports/templates/report.html`）

`generate_report_pdf(session, assessments) -> bytes`
1. 收集上下文：标题、生成时间、session.label、各评估（严重度色阶/标签、总分、解释、危机触发项）
2. **危机兜底**：若任一 `assessment.needs_crisis_escalation`，顶部硬编码渲染红色危机框（`safety.crisis_message()` + 12355），**不依赖 LLM**
3. **LLM 建议段**：`provider.chat(role="report", messages=[system+user])`，user 给出评估摘要（量表/总分/严重度/危机）；prompt 约束：共情、给可操作建议、中重度以上建议求助专业人员、危机时强调转介。返回文本经 markdown→HTML 注入「评估建议」段
4. Jinja2 渲染 `report.html` → WeasyPrint `HTML(string=...).write_pdf()` 得 bytes
5. 页脚：非医疗器械声明 + 生成时间 + 模型版本

`report.html`（Jinja2）：A4、中文 print 字体、严重度色块、危机红框、建议段、页脚声明

### 4. 深度危机兜底

- 评分阶段危机由 `Scale.score` 硬规则决定（已就绪，base.py）
- 报告阶段：`needs_crisis_escalation` 为真 → 报告顶部强制渲染硬编码危机框（零 LLM），LLM 建议段 prompt 同步约束「必须转介」
- 即便 LLM 建议文本软化，硬框仍不可绕过 —— 双层兜底

### 5. 路由挂载（`app/main.py`）

`include_router(sessions_router)`；启动 `init_db()`

---

## 前端改动（`frontend/src/`）

- `api.ts`：新增 `apiPostBlob(path, body) -> Promise<Blob>`（处理 application/pdf 二进制响应）
- `pages/ScalePage.tsx`：结果区新增「生成 PDF 报告」按钮 → 调 `generateReport(scaleId, answers)` 辅助函数（链式：create session → submit assessment → report blob → `window.open(URL.createObjectURL(blob))`）
- 不改动首页/对话页（报告由评估结果触发）

---

## 测试

延续纪律（当前 56 测全绿）：
- `tests/test_db.py`：建表幂等；创建 Session + 2 条 AssessmentRecord；查询回读字段一致（含 JSON 字段）
- `tests/test_api_sessions.py`（TestClient，mock provider）：
  - create session → submit phq_a（item9>0）→ GET session 返回含 1 条 elevated 记录
  - POST report → status 200、content-type application/pdf、bytes 非空
- `tests/test_reports.py`（mock provider.chat role="report"）：
  - 危机评估 → 渲染 HTML 含 "12355"、provider.chat 被调（role="report"）
  - 非危机评估 → HTML 不含危机框、含「评估建议」段
  - 一次 WeasyPrint 实出 PDF bytes（确认系统库链路通）

## 端到端验证

1. `docker exec psycheflow-backend uv run pytest` —— 全绿
2. Python httpx 实调：create session → submit phq_a（item9=1）→ POST report → 返回 PDF（content-type application/pdf，长度>0）
3. 浏览器 5174：ScalePage 提交 PHQ-A → 「生成 PDF 报告」→ 新标签打开 PDF，含总分/严重度/建议段；item9>0 时报告顶部含红色 12355 危机框

## 不在步骤 6 范围

- 对话历史持久化（chat 仍 stateless；报告为评估维度，不引对话）
- 学生真实姓名 PII（仅匿名 label）
- alembic 迁移（用 create_all 幂等建表，MVP 够用）
- 报告历史列表页（仅按需生成，不做列表 UI）
