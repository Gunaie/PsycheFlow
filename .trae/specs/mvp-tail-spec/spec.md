# PsycheFlow MVP 补齐验收（A1~A4）- PRD

## Overview
- **Summary**: 补齐开发计划 §9.1「MVP 验收标准 6 条」中尚未真正达成的 4 项缺口：注册登录 + 监护人授权链；历史报告列表；对话留痕 + 危机独立审计日志；RAG 心理知识库真实语料入库。
- **Purpose**: 让 PsycheFlow 从「能演示报告生成的 Demo」升级为「学校心理老师真实可用、合规留痕、对话质量有知识支撑」的 MVP 闭环产品。
- **Target Users**: 中小学生（被测评者）、学校心理老师/德育处（接收报告、看历史）、家长（签字同意）。

## Goals
- 学生注册登录时必须经过「未成年人 + 监护人/学校授权 + 非医疗器械免责」三重知情同意；
- 任意学生的历史测评报告可列表、可检索、可一键重新下载 PDF；
- 对话每一轮持久化不可篡改；危机命中必须落盘独立 JSON 审计日志（对应 §11 审计留痕原则）；
- RAG 有真实中文心理语料（CCMD-3 摘要 / 卫健委热线指南 / CBT 入门），对话时能引用知识来源。

## Non-Goals
- 不做 JWT / OAuth2 / 多租户 RBAC 等复杂鉴权；MVP 使用轻量 opaque token（account_id），Authorization: Bearer {id} 模式；
- 不做学生信息加密存储 AES-GCM 等合规深层改造（最小化；后续三期做），但会做到「最小化字段 + 匿名可选」；
- 不做 SoulChatCorpus 微调和 LangGraph 四智能体；留在二期；
- 不做新量表 SDQ/MSSMHS（四期）；
- 不做 B 端校方管理后台、批量筛查（三期）。

## Background & Context
- 现状 6 Steps 完成但 §9.1 MVP 验收 6 条真实缺口：①注册登录 ❌ ②对话上下文记忆无持久化 + RAG 语料空 ⚠️ ③危机审计日志独立文件 ❌ ④历史报告列表 ❌；
- 开发计划 §3.2 P0：「学生账号注册（监护人/学校授权）」「危机关键词拦截 + 审计」「历史报告管理」均属 P0，当前均未闭环；
- 开发计划 §11 安全设计原则：危机升级记 `logs/session_{id}.json`，含时间戳与触发词；
- 开发计划 §7.2 RAG 心理知识库要求：CCMD-3、DSM-5 公开摘要、WHO 心理健康指南、国家卫健委《心理援助热线技术指南》、CBT/DBT 技术手册公开版 → 切片入库并标注来源可溯源；
- Docker：psycheflow-backend 已挂 ./data:/app/data，新增 logs、knowledge、SQLite 用户表都走此卷自动持久化；
- 既有后端单测：7 reports tests 全绿（Step 6 成果），不得被新改动破坏。

## Functional Requirements
- **FR-A1-1（注册页与同意链）**：前端新增 `RegisterPage` 路由 `/register`，页内含四个强制勾选的复选框：
  1. 我已阅读并同意「PsycheFlow 仅为心理筛查辅助工具，**非医疗器械，不替代专业诊疗/诊断/开药**」；
  2. 我是学生家长/监护人或学校授权人员，同意被测学生（未成年人）使用本系统并允许生成筛查报告；
  3. 我理解 14 周岁以下未成年人个人信息受《个人信息保护法》特别保护，系统仅收集测评必要字段；
  4. 我同意危机升级时为保护学生生命安全，系统可打破保密限制，直接将危机信号通知 12355 + 学校心理老师链路。
  缺失任一项勾选则「提交」按钮不可用。
- **FR-A1-2（学生档案字段）**：注册页可填档案字段（**全部可空、鼓励匿名**）：学生姓名、学号、年级、班级、性别、年龄、监护人手机号、学校名、心理老师联系邮箱。
- **FR-A1-3（Account 生成与登录）**：提交后后端 POST `/api/auth/register` 返回 `{account_id, token, label}`，前端存 `localStorage.psycheflow_token`；再新增轻量「登录」：`/api/auth/login` 支持用 token 或 label 查询返回 account_id（MVP 不做密码；登录即「粘贴 token」或「选择匿名 Label 继续」两种路径）。
- **FR-A1-4（接口绑定 user）**：所有既有的 `/api/sessions/*`、`/api/chat`、`/api/report` 支持 `Authorization: Bearer <token>` 头并关联到 `account_id`；**头缺失时维持完全向后兼容（匿名）**，不得破坏现有 7 reports pytest 和之前的 curl 链路。
- **FR-A2-1（历史列表接口）**：新增 `GET /api/sessions?account_id=&cursor=&page_size=` 返回 `{items: [{session_id, created_at, label, has_crisis, assessments:[{scale_id,severity,score,max_score}] }], next_cursor}`。匿名无 account_id 时返回最近的 50 条（无分页 token 兜底）。
- **FR-A2-2（历史列表详情接口）**：新增 `GET /api/sessions/{session_id}` 返回该会话完整详情（所有 assessments 的 answers/子维度得分/解释）。
- **FR-A2-3（HistoryPage 前端）**：新增 `pages/HistoryPage.tsx` 路由 `/history`。Header 顶部 Tab：Home / Scale / Chat / **History**。卡片列表按时间倒序，卡片上展示：日期、label、量表得分徽章、危机红标。点击卡片：① `下载 PDF` 按钮调用 `/api/sessions/{id}/report`；② `查看长图` 按钮（可选，直接前端展示后端 PDF 转 PNG 链接或内联 iframe PDF）。
- **FR-A3-1（对话留痕表）**：新建 SQLAlchemy 模型 `ConversationTurn(id, session_id|null, account_id|null, role, content, sources_json, crisis_hit_bool, created_at)`；每次 POST `/api/chat` 成功或危机命中均落 1~2 条（user + assistant）。
- **FR-A3-2（危机独立审计日志文件）**：每次 `detect_crisis(user_input)` 命中时，除原有的 crisis 响应外，**额外写 `./data/logs/crisis_{session_id}_{YYYYMMDD-HHMMSS-fff}.json`**（由 docker 卷挂载自动持久化），JSON Schema：`{ts, session_id, account_id, trigger_words, user_input_raw, crisis_reply, referred_12355_bool, assessment_context}`。目录不存在时自动 `makedirs`，写失败不阻断接口响应（try/except 吞掉 + backend log warning）。
- **FR-A3-3（报告 PDF 触发审计）**：每次 POST `/report` 生成 PDF 后，写 `./data/logs/report_{session_id}_{ts}.json`：`{ts, session_id, account_id, has_crisis, scores: {scale: {score, severity}}, file_size_bytes, narrative_len}`。
- **FR-A4-1（语料组织）**：在仓库新增 `data/knowledge/` 目录（docker 卷挂载区域），放 3 类 Markdown 公开语料：
  1. `ccmd3_summary.md`：CCMD-3 公开摘要（抑郁发作/焦虑障碍/品行障碍 核心条目 + ICD 编码摘要，非全文）；
  2. `nhc_hotline_guide.md`：国家卫健委《心理援助热线技术指南（试行）》公开摘要（倾听/共情/危机转介流程）；
  3. `cbt_intro.md`：CBT 认知行为疗法入门科普（自动化思维/认知三角/4-2-6 呼吸/行为激活 等可公开技术摘要）。
  每篇 Markdown 顶部 `## 来源《标题》 - 发布方（公开摘要）` 作为 source 标签。**严格只放公开摘要、不侵犯版权**。
- **FR-A4-2（ingest CLI 脚本）**：新增 `backend/app/rag/cli_ingest.py`：`uv run python -m app.rag.cli_ingest --dir ../../data/knowledge/ --chunk 300 --overlap 50`，循环读 md→切 chunk→text-embedding-v3→写入 Chroma（Chroma metadata 带 source 文件 basename + chunk_id）。**幂等**：带 `--reset` 则删除该 namespace 集合再重写。
- **FR-A4-3（RAG 引用来源展示）**：ChatPage 的「知识参考」卡片显示每一条的 `来源：《xxx》片段#N`，不是只显示 text。sources 有值时卡片必显示，空时隐藏。
- **FR-A4-4（首次启动自动 ingest）**：后端 `main.py` `lifespan` 中，当 Chroma 向量库当前 doc_count < 50 时，自动调用 `cli_ingest` 的核心函数做一次注入（MVP 冷启动兜底；失败降级）。

## Non-Functional Requirements
- **NFR-1（向后兼容）**：不带 Authorization 头匿名模式下，所有已有端点的响应 schema **一字节不变**（除新增可空 user_id 不影响已有 payload）；7 reports pytest 必须 100% 通过；
- **NFR-2（日志/语料卷持久化）**：`./data/logs/` 和 `./data/knowledge/` 通过 docker compose 卷 `/app/data` 自动持久化；容器重启不丢失；
- **NFR-3（单测 ≥ 4 个新）**：新增 pytest 至少 4 个：A1 注册缺勾选被拒、A2 sessions 列表按时间倒序、A3 crisis 命中写 logs .json、A4 ingest 后 rag.search 返回带 source 的非空结果；
- **NFR-4（接口性能）**：注册/历史列表/日志写入 < 200 ms；ingest 3 文档 < 30 s（取决于百炼 embedding 速率，timeout 60 s 兜底）；
- **NFR-5（对话首 token 时延）**：对话 P95 首 token < 2 s（RAG 失败降级时仍满足）；
- **NFR-6（合规展示强提醒）**：所有页面 footer 固定一行免责声明小字：「PsycheFlow 为心理筛查辅助工具，非医疗器械，不替代专业诊疗/诊断。危机信号请拨打 12355 青少年热线」。

## Constraints
- **Technical**: Windows + Docker Desktop；前端 Vite :5174 / 后端 FastAPI :8000 / Chroma :8001 固定端口不得改；
- **Business**: 不得引入"医疗器械/诊疗/开药"表述；危机升级 12355 硬编码零 LLM 原则不得破；
- **Dependencies**: 百炼 4 模型 key 在 `backend/.env`；不得切换其它平台；WeasyPrint / pymupdf 已安装于后端容器。

## Assumptions
- 百炼 text-embedding-v3 可正常调用（HTTP 200，RPM ≥ 60）；
- Docker 卷 `./data/` 当前有 psycheflow.db，不会被误操作删；
- 心理老师/学生不会真的把真实姓名/手机号作为必填字段（MVP 鼓励匿名）。

## Open Questions
- [x] 登录 MVP 是否密码？→ 否，只支持 token/label 登录，不引入密码体系（降低合规与开发成本）。
- [x] 真实 RAG 语料是否允许？→ 允许，只使用公开摘要并标注来源，拒绝受版权保护全文。
- [ ]（保留）心理老师角色是否单独区分？→ MVP 保留为同一账号（account.role 字段预留 enum=['student','teacher','parent']，默认 student，前端不暴露切换 UI）。

---

## Acceptance Criteria

### AC-A1：注册知情同意链缺勾选不能提交
- **Type**: `rule`
- **Given**: 用户打开 `/register` 页面，四个强制复选框至少一个未勾
- **When**: 点击「提交注册」按钮
- **Then**: 前端按钮 disabled 或点击拦截弹出"请先完成所有知情同意勾选"，**不会**向 `/api/auth/register` 发起请求
- **Pass Condition**: 后端 `/api/auth/register` endpoint 对缺 consent 字段的请求返回 422，前端在未勾选时不触发请求
- **Evidence**: ① pytest `test_register_missing_consent_rejected` 单测断言 422；② 浏览器 RegisterPage 四框未全勾时点提交按钮 network 无 register 请求（Chrome DevTools Network 抓包截图或 curl 手工验证）

### AC-A2：注册成功返回 token 并持久化 account
- **Type**: `rule`
- **Given**: 用户 POST `/api/auth/register` 带 `{consents: {a: true, b: true, c: true, d: true}, profile: {...}}`
- **When**: 百炼正常，数据库可写
- **Then**: 返回 200，JSON 含 `account_id(32hex), token, label`；新 account 写入 SQLite `users` 表；`token` 等于 `account_id`（MVP 轻量 opaque token）
- **Pass Condition**: curl 注册响应 200 + 紧接着 `Authorization: Bearer <token>` 调 `/api/sessions` 能正确关联 `account_id`
- **Evidence**: pytest `test_register_success_and_linked_sessions` + curl 输出 200 + `select id, label from users` 有新行

### AC-A3：历史列表接口按时间倒序 + account 过滤
- **Type**: `rule`
- **Given**: account A 创建 3 个 sessions，account B 创建 2 个
- **When**: `GET /api/sessions` 带 A 的 token
- **Then**: items 长度 3，仅 A 的；按 `created_at` DESC；分页 page_size=2 时分 2 页；has_crisis / severity / score / max_score 字段齐全
- **Pass Condition**: 断言 items[0].created_at > items[-1].created_at；断言 B 的 session_id 不出现在结果集
- **Evidence**: pytest `test_sessions_list_desc_and_account_filtered` + curl JSON 输出

### AC-A4：HistoryPage 列表展示 + 下载 PDF 按钮可用
- **Type**: `rule`
- **Given**: 登录账号 A，已有 1 个带危机的 session
- **When**: 访问 `/history` 页面
- **Then**: 卡片显示该 session 危机红标 + 量表得分徽章；点「下载 PDF」按钮 → 新标签页 / 下载事件触发 Blob type=application/pdf
- **Pass Condition**: 前端点击后 network 出现 `/sessions/{id}/report` 200 response; PDF size > 160KB
- **Evidence**: 浏览器 UI 操作截图 + network 面板 + PDF 大小验证（BrowserUse agent 或手工）

### AC-A5：对话命中危机 → 落盘独立 JSON 审计日志
- **Type**: `rule`
- **Given**: `POST /api/chat {message: "我不想活了，想跳楼", history: [], session_id: X}`
- **When**: 接口返回 200 crisis=true
- **Then**: `./data/logs/crisis_X_*.json` 存在，JSON 内 `trigger_words` 非空、`referred_12355_bool == true`、`user_input_raw` 含原句
- **Pass Condition**: glob 1 个匹配文件；json parse 成功；5 字段全部符合断言
- **Evidence**: pytest `test_crisis_hit_writes_audit_json` + 目录 ls + 文件内容断言

### AC-A6：正常对话每轮持久化 ConversationTurn
- **Type**: `rule`
- **Given**: 2 轮对话 send/receive
- **When**: 完成后查 DB
- **Then**: `conversation_turns` 表行数 = 4（2 user + 2 assistant）；account_id/session_id 正确关联；crisis_hit 字段正确
- **Pass Condition**: 行数断言 + 角色交替顺序
- **Evidence**: pytest `test_chat_turns_persisted`

### AC-A7：报告生成写 report_{sid}.json 审计日志
- **Type**: `rule`
- **Given**: 走 create session → 2 assessments → /report 完整链路
- **When**: PDF 返回 200
- **Then**: `./data/logs/report_*.json` 存在；scores dict 含 phq_a/scared；narrative_len > 200；file_size_bytes > 100K
- **Pass Condition**: 文件 JSON 解析成功；3 字段都在阈值内
- **Evidence**: pytest `test_report_generated_writes_audit_json`（直接调 generate_report_pdf service 函数）

### AC-A8：知识库 ingest 成功后 RAG search 返回非空 + 带 source
- **Type**: `rule`
- **Given**: 执行完 `cli_ingest` 3 篇 md
- **When**: `rag_service.search("重度抑郁有哪些症状", top_k=2)`
- **Then**: 返回 >= 1 条；每条含 `text` 和 `source` 字段；`source` 形如 `ccmd3_summary.md`
- **Pass Condition**: len >= 1 且全部 source 非空
- **Evidence**: pytest `test_rag_ingest_then_search`（测试内 run ingest，reset 隔离）

### AC-A9：ChatPage 知识参考区展示 source 标签
- **Type**: `rule`
- **Given**: 发起一次能命中 RAG 的问题（如"抑郁症状有什么"）
- **When**: assistant 回复 + sources 有值
- **Then**: ChatPage DOM 出现 `来源：《ccmd3_summary.md》` 字样的卡片；sources 为空时卡片不显示
- **Pass Condition**: 浏览器快照或 textContent 含"来源："字样
- **Evidence**: BrowserUse 访问 ChatPage 发送问题后的快照 / 或 curl chat 接口返回 sources[].source 非空 + ChatPage 代码有 `<p>来源：...</p>` 渲染分支

### AC-A10：7 原有 reports 单测不被破坏
- **Type**: `rule`
- **Given**: A1~A4 代码改动完成
- **When**: `uv run pytest tests/test_reports.py -v`
- **Then**: 7/7 PASSED，0 failures
- **Pass Condition**: 终端输出 `7 passed`
- **Evidence**: pytest 输出文本

### AC-U11：注册 + 知情同意链合规性说明充分性
- **Type**: `rubric`
- **Dimension**: 合规信息透明与可感知性
- **Scale**: 1-5
- **Anchors**: 1 = 没有免责/同意文字；3 = 有简短免责但无四连勾选 + 未成年人特别条款；5 = 四条强勾选 + 每条有短句展开解释 + 页面 footer 再次出现免责横条，家长/心理老师一眼能看懂风险边界
- **Pass Threshold**: >= 4
- **Evidence**: RegisterPage 源码 DOM 结构 + 浏览器截图

### AC-U12：历史列表浏览体验流畅度
- **Type**: `rubric`
- **Dimension**: 历史报告列表可用性
- **Scale**: 1-5
- **Anchors**: 1 = 纯 JSON 原始输出，无卡片；3 = 仅有按时间倒序列表，无得分徽章、无危机标、无下载按钮；5 = 卡片设计（时间、标签、双量表得分彩色胶囊、危机红边标、双按钮：下载 PDF / 查看详情），响应式宽度适配
- **Pass Threshold**: >= 4
- **Evidence**: HistoryPage 源码 + 浏览器 UI 截图

### AC-U13：对话知识引用 UI 清晰
- **Type**: `rubric`
- **Dimension**: RAG 引用来源透明度
- **Scale**: 1-5
- **Anchors**: 1 = sources 完全不显示；3 = 有 sources 但没标来源文件，仅显示文字；5 = 每一条 sources 独立卡片，头一行灰字"来源《xxx.md》片段#N"，正文引用内容，样式明显与 assistant 正文区分
- **Pass Threshold**: >= 4
- **Evidence**: ChatPage 回复后有 sources 的页面快照
