# PsycheFlow MVP 补齐验收（A1~A4）- Implementation Plan

任务依赖拓扑：
Task 1 (DB 数据层 User/ConversationTurn + 安全目录) → 并行 [Task 2 (A1 注册登录 API)、Task 5 (A3 审计日志 + 对话留痕实现)] → Task 3 (A2 历史列表 API) → Task 4 (A2 前端 HistoryPage) + Task 6 (A1 前端 RegisterPage) + Task 7 (A3 接口串联 + Footer 免责)
Task 8 (A4 语料) 与 Task 1~7 几乎独立，可最早与 Task 1 并行。
Task 9 (单测)、Task 10 (浏览器验收)、Task 11 (git commit) 在所有实现之后。

---

## Task 1: 新增 User + ConversationTurn 数据模型 + 目录创建器
- **Status**: `pending`
- **Priority**: high
- **Depends On**: None
- **Description**:
  - `backend/app/models.py` 新增 `User` 表：`id(32hex PK)`、`label(String64 unique)`、`role(Enum student/teacher/parent, 默认 student)`、`profile(JSON, nullable)`、`consents(JSON 四键布尔, nullable 但运行期强制 True 全)`、`token(String64 unique)`、`created_at(DateTime)`
  - 新增 `ConversationTurn` 表：`id(32hex PK)`、`session_id(nullable String32, FK sessions.id)`、`account_id(nullable String32, FK users.id)`、`role(user/assistant/system)`、`content(Text)`、`sources_json(JSON, nullable)`、`crisis_hit(Boolean default False)`、`created_at(DateTime)`
  - Session 表新增可选 `account_id` FK→users.id；AssessmentRecord 不变；
  - `backend/app/core/config.py` 新增 `logs_dir`（=`<data_dir>/logs`），`rag_knowledge_dir`（=`<data_dir>/knowledge`），app 启动时 makedirs；
- **Acceptance Criteria Addressed**: AC-A2, AC-A6, NFR-2
- **Test Requirements**:
  - `rule` TR-1.1: `init_db()` 后 `users`, `conversation_turns` 两张表存在于 sqlite_master（`SELECT name FROM sqlite_master WHERE type='table'` 断言）
  - `rule` TR-1.2: `logs_dir` 与 `rag_knowledge_dir` 自动创建（启动后 os.path.isdir True）
  - `rule` TR-1.3: User 表 label/unique 约束生效（同 label INSERT 两次 → IntegrityError 第二次）

## Task 2: A1 注册/登录后端 API
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1
- **Description**:
  - 新建 `backend/app/api/auth.py`（router=`/api/auth` tags=["auth"]）：
    - `POST /register {consents: {tool:true, guardian:true, privacy14:true, crisis:true}, profile: {...}, role?: 'student'|'teacher'|'parent'}` → 校验 4 个 consent 全 True（否则 422 `detail="missing_consents"`+ missing 数组）→ create User，token=account_id（MVP 轻量）→ 返回 `{account_id, token, label}`
    - `POST /login_by_token {token}` → 查 user 返回 account（404 不匹配）
    - `POST /login_by_label {label}` → 查 user 返回（404）
  - 新增 `backend/app/api/deps.py` `get_current_account(authorization_header, db)`：Authorization Bearer {token} → 返回 User or None；无 Bearer 时返回 None（**不 throw**，保证匿名兼容 NFR-1）
  - sessions.py 的 `POST /api/sessions`：若有 current_account → Session.account_id = account.id
  - chat.py 的 `POST /api/chat`：接收可选 `session_id`、`account_id` 字段（或 Bearer 里取 account）；写 ConversationTurn 时填充
- **Acceptance Criteria Addressed**: AC-A1, AC-A2
- **Test Requirements**:
  - `rule` TR-2.1: 缺任一 consent → 422 `missing_consents`（单测：4 次逐个缺 1 个调用均返回 422）
  - `rule` TR-2.2: 全 consent → 200 且 `len(account_id)==32`，`token==account_id`，DB users 有新行
  - `rule` TR-2.3: 创建 session 带 Bearer → session.account_id == user.id（assert select 成功）
  - `rule` TR-2.4: 不带 Bearer 创建 session，向后兼容（account_id 为 NULL，reports pytest 不破坏）

## Task 3: A2 历史列表 + 详情 + per-session 报告导出 API
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - sessions.py 新增：
    - `GET /api/sessions`：`account_id?`（Bearer 无则不填），cursor(created_at iso string, default None)，`page_size=20`（max 100）→ 返回 `items: [{session_id, created_at, label, has_crisis: bool, assessments:[{scale_id, scale_name, score, max_score, severity}]}, ...]`，`next_cursor`（下一页第一个的 created_at - 1μs 或 None）
    - `GET /api/sessions/{session_id}`：返回会话完整详情 + answers + 子维（复用 `render_report_html` 上下文，可走 JSON 不带 HTML 字符串）
    - `GET /api/sessions/{session_id}/report`：调用 `generate_report_pdf(session_id)` → `StreamingResponse(bytes, media_type='application/pdf', headers={'Content-Disposition': f'attachment; filename="report_{id}.pdf"'})`
  - 权限：若该 session.account_id != NULL，要求 Bearer 匹配；若 account_id IS NULL（匿名），任何人可查看（MVP 简化）
- **Acceptance Criteria Addressed**: AC-A3
- **Test Requirements**:
  - `rule` TR-3.1: account A 3 个 + B 2 个；A 的 Bearer GET /sessions 返回仅 A，items[0].created_at > items[-1].created_at（倒序）
  - `rule` TR-3.2: `page_size=2` 分页返回 `next_cursor` 非空，第二次 cursor 请求返回第 3 条，最后一页 cursor=None
  - `rule` TR-3.3: `GET /sessions/{id}/report` HTTP 200，Content-Type application/pdf，size >= 160_000

## Task 4: A2 前端 HistoryPage 实现
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 3
- **Description**:
  - `frontend/src/pages/HistoryPage.tsx` 路由 `/history`：
    - 顶部 Header Tab（和 Home/Scale/Chat 保持同类样式，深蓝 #1e3a5f）→ 新增「历史报告」选中态
    - 加载时调 `GET /api/sessions`（带 localStorage token）
    - 每张卡片：日期（YYYY-MM-DD HH:MM）+ label + 双量表 severity 胶囊色标（按 severity 颜色：灰/黄/橙/红）+ crisis 红边 + 右上角「危机 ⚠ 12355」标；
    - 每张卡片底部按钮：「下载 PDF」→ `apiGetBlob('/api/sessions/'+id+'/report')` 触发下载（保留「先同步开空窗再赋值」手势保护模式）；「查看详情」→ 弹模态展示量表分 / 子维分简要
    - 空态：显示「暂无测评记录，前往测评」按钮跳转 ScalePage
  - `frontend/src/App.tsx` 加路由 `/history`
  - `frontend/src/api.ts` 加 `apiGetBlob(url)` + `apiGet<T>(url)` 封装
- **Acceptance Criteria Addressed**: AC-A4, AC-U12
- **Test Requirements**:
  - `rule` TR-4.1: 空态 /history 返回「暂无测评记录」文案（前端快照测试或直接 DOM 断言）
  - `rule` TR-4.2: 有危机 session 时卡片含「12355」文本
  - `rubric` TR-4.3: 历史列表浏览体验；scale 1-5；anchors 1=纯JSON无结构/3=基本列表无徽章/5=卡片+徽章+红标+双按钮；threshold >= 4；evidence HistoryPage.tsx DOM 结构 + 浏览器截图

## Task 5: A3 审计日志目录与写文件服务（危机 + 报告）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1（logs_dir 定义）
- **Description**:
  - 新建 `backend/app/core/audit.py`：
    - `ensure_logs_dir()`（启动时或首次写）
    - `write_crisis_audit(session_id, account_id, trigger_words, user_input_raw, crisis_reply, assessment_context=None)` → 写 `crisis_{sid}_{YYYYMMDD-HHMMSS-fff}.json`
    - `write_report_audit(session_id, account_id, has_crisis, scores_dict, file_size_bytes, narrative_len)` → 写 `report_{sid}_{ts}.json`
  - chat.py：crisis 命中分支里（原有 `return {"reply": crisis_message(), "sources": [], "crisis": True}` 之前）加 try/except 调用 write_crisis_audit（写失败 warning.log 不阻断）
  - reports/service.py `generate_report_pdf` 函数在返回 bytes 前：try/except write_report_audit（不阻断返回）
  - safety.py `detect_crisis` 同时返回 `(bool, trigger_words: list[str])`（原返回 bool，兼容：保留 bool 语义 + 新增 named tuple 或返回两个值，调用点改）
- **Acceptance Criteria Addressed**: AC-A5, AC-A7
- **Test Requirements**:
  - `rule` TR-5.1: crisis 消息请求后，glob `crisis_*.json` 返回 1 个新文件；json 内 `trigger_words` len>=1；`referred_12355_bool == True`
  - `rule` TR-5.2: generate_report_pdf 调用后 `report_*.json` 文件存在；`scores.phq_a.score` 类型 int；`narrative_len > 200`
  - `rule` TR-5.3: 审计日志写入权限异常（只读目录 mock）时接口仍 200 不失败（monkeypatch logs_dir 指向不存在子路径，assert response 200 且不抛）

## Task 6: A1 前端 RegisterPage 与简易 Login
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2
- **Description**:
  - 新建 `pages/RegisterPage.tsx` 路由 `/register`：
    - Header "PsycheFlow · 注册 / 知情同意"
    - 四个强制勾选的 Checkbox 行，每条配说明文字（100-200 字每条，AC-U11 rubric）：
      - [ ] 工具非医疗器械（含「不诊断、不开药、不替代精神科诊疗」具体措辞）
      - [ ] 监护人/学校授权（说明被测为未成年人，本人是家长或学校授权人员）
      - [ ] 14 周岁以下特别保护（说明仅收集最少信息，并可匿名）
      - [ ] 危机打破保密（说明 12355 + 老师转介链路）
    - 档案字段表单（全部可空 placeholder 鼓励匿名）：姓名/学号/年级/班级/性别/年龄/监护人手机/学校/心理老师邮箱
    - 「生成账号」按钮：`disabled = !four_checked`；点击调 `POST /api/auth/register` → 成功显示 `account_id` + token（带"复制到剪贴板"按钮），提示「请妥善保存该 token 下次登录用」，localStorage 写入 token 并自动跳 ScalePage
  - 新建简易 `pages/LoginPage.tsx` 路由 `/login`：两个 Tab 「按 Token」/「按标签 Label」，调对应 login 接口；成功存 token 跳 Home
  - HomePage 导航栏加按钮：「注册」/「登录」/「退出」（退出即删 localStorage token）
  - Footer `components/FooterDisclaimer.tsx` 全局抽离（NFR-6 合规小字），插入所有页面底部
- **Acceptance Criteria Addressed**: AC-A1, AC-U11, NFR-6
- **Test Requirements**:
  - `rule` TR-6.1: 未勾满四框，RegisterPage 按钮 disabled=true（DOM 断言）
  - `rule` TR-6.2: 勾选四框后点击 → POST register → localStorage.psycheflow_token 存在（len 32）
  - `rubric` TR-6.3: 知情同意链说明充分；scale 1-5；anchors 1=无文字/3=短免责无逐条说明/5=四复选框每条 100+ 字具体解释 + footer 再重申；threshold >= 4；evidence DOM 文本快照

## Task 7: A3 对话留痕（chat.py 落 ConversationTurn）+ 授权 header 贯通
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 2（deps.getCurrentAccount）+ Task 5（audit）
- **Description**:
  - chat.py ChatRequest 新增两个可空字段：`session_id: str|None`, `account_id: str|None`（也从 Bearer deps 优先取 account）
  - 在调用 LLM 前后：先写 user turn，后写 assistant turn；crisis 命中时 crisis_hit=True + sources=[]
  - 所有既有用 chats 的历史 payload 不传这俩字段 → 全默认 None 完全向后兼容
  - ScalePage/ChatPage：传 localStorage token 到所有请求（axios/fetch `Authorization: Bearer ${token}`），如果 token 不存在就不写 header
- **Acceptance Criteria Addressed**: AC-A6
- **Test Requirements**:
  - `rule` TR-7.1: 两轮对话后 conversation_turns 表 4 行，ROLE 序列 [user, assistant, user, assistant]
  - `rule` TR-7.2: 危机命中轮 crisis_hit=True 1 行；sources 字段保留原返回 sources
  - `rule` TR-7.3: 不带 session_id/account_id 的旧请求（旧 curl 链路）仍 200 OK，对应字段为 NULL

## Task 8: A4 心理知识库语料 + CLI ingest + 首次启动自动注入 + ChatPage 来源 UI
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: None（几乎完全独立，可并行最早）
- **Description**:
  - 新建公开摘要三篇（严格公开知识，非受版权保护全文）：
    - `data/knowledge/ccmd3_summary.md`：CCMD-3 抑郁发作 F32、广泛性焦虑 F41.1、社交焦虑 F40.1、惊恐障碍 F41.0 核心症状条目 + 诊断要点（摘要级文字，标注「中国精神障碍分类与诊断标准第3版 公开摘要」）
    - `data/knowledge/nhc_hotline_guide.md`：卫健委《心理援助热线技术指南》摘要：倾听技术（复述/共情/不评判）、危机信号识别、转介 120/110/12355 流程（公开摘要）
    - `data/knowledge/cbt_intro.md`：CBT 入门摘要：认知三角（想法-感受-行为）、自动化思维、4-2-6 腹式呼吸法、行为激活小步原则、三栏记录表（公开科普级）
  - `backend/app/rag/store.py`：`ingest_markdown(file_path: str, chunk_size=300, overlap=50, namespace='psycheflow_knowledge')` 方法 + `reset_namespace(namespace)` 方法
  - `backend/app/rag/cli_ingest.py`：`__main__` 读 `--dir --chunk --overlap --reset` 参数，循环处理 *.md
  - `main.py lifespan`：启动后若 `rag_service.doc_count('psycheflow_knowledge') < 50` → `cli_ingest.do_ingest(default_dirs, reset=False)`（失败 warn）
  - ChatPage：sources 卡片样式改成灰底小卡，每张顶部显示「**来源**：《{source}》片段 #{chunk_id+1}」（chunk_id 从 RAG store 返回 metadata 带）
  - rag/service.py search 返回结果 metadata 里补充 `source: basename` 给前端
- **Acceptance Criteria Addressed**: AC-A8, AC-A9, AC-U13
- **Test Requirements**:
  - `rule` TR-8.1: `do_ingest` 后 Chroma `count()` >= 10
  - `rule` TR-8.2: `search("重度抑郁")` 返回 1 条且 `source == "ccmd3_summary.md"`
  - `rule` TR-8.3: `search("什么是腹式呼吸")` 返回包含 `cbt_intro.md`
  - `rule` TR-8.4: ChatPage 回复 DOM（有 sources）出现"来源："字样

## Task 9: 所有新单测落地 + 回归跑通
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 1-8 全 done
- **Description**:
  - 新建 `backend/tests/test_auth.py`：TR-2.1 ~ TR-2.4
  - 新建 `backend/tests/test_sessions_history.py`：TR-3.1 ~ TR-3.3
  - 新建 `backend/tests/test_audit.py`：TR-5.1 ~ TR-5.3 + TR-7.1 ~ TR-7.3
  - 新建 `backend/tests/test_rag_ingest.py`：TR-8.1 ~ TR-8.3
  - 跑 `uv run pytest backend/tests -v`（注意 tests 路径要正确；或者 `docker exec psycheflow-backend uv run pytest tests -v`）
  - 同时原有 reports 7 tests 必须全绿（AC-A10）
- **Acceptance Criteria Addressed**: AC-A10, NFR-3
- **Test Requirements**:
  - `rule` TR-9.1: 新增 >= 4 pytest 通过（实际应 14+ 条），`pytest summary` 0 failed
  - `rule` TR-9.2: `7 passed in *.00s` (reports tests) 仍出现在 pytest 输出

## Task 10: 浏览器端端到端验收（4 个独立页面动作）
- **Status**: `pending`
- **Priority**: high
- **Depends On**: Task 9 所有 pytest pass
- **Description**:
  - 用 BrowserUse agent 或手工：
    1. 访问 `/register`：未全勾 → 按钮 disabled → 勾满 → 点击 → localStorage 有 token → 跳 `/scale`
    2. 走一遍 ScalePage 答题（PHQ-A 27 + SCARED 23 危机集）→ 生成 PDF → 新标签页正常打开
    3. 访问 `/history`：卡片出现，危机红标 + 下载 PDF 按钮 → 点击下载 → 浏览器触发 Blob 下载（大小>160KB）
    4. 访问 `/chat`：输入"重度抑郁有哪些症状" → 回复下方 sources 卡片显示「来源：ccmd3_summary.md」字样；输入"我不想活了" → crisis banner 显示 12355，且后端容器 ls /app/data/logs/ 出现新 json
- **Acceptance Criteria Addressed**: AC-A1, AC-A4, AC-A5, AC-A9
- **Test Requirements**:
  - `rule` TR-10.1: 4 个动作全部无 4xx/5xx 报错
  - `rubric` TR-10.2: 端到端体验完整流畅；scale 1-5；anchors 1=多处404/白屏/3=功能通但样式乱/5=全流程无报错无刷新丢状态；threshold >= 4；evidence 浏览器截图集合

## Task 11: Git commit + 中文 message
- **Status**: `pending`
- **Priority**: medium
- **Depends On**: Task 9+10 均通过
- **Description**:
  - commit：6 files 组（models.py, db.py, config.py + {api/auth.py, api/deps.py, sessions.py 改, chat.py 改, reports/service.py 改} + {RegisterPage/LoginPage/HistoryPage 新建 + App.tsx 路由 + api 封装 + FooterDisclaimer 组件} + {audit.py + safety.py 改} + {RAG 语料 3 md + cli_ingest + store/service 改 + main.py 改 + ChatPage sources UI 改}）
  - 中文 commit message（建议）：
    「MVP 补齐验收：注册知情同意链 + 历史报告列表 + 对话&危机审计日志 + RAG知识库真实语料入库

    - A1 注册登录：四连知情同意(非医疗器械/监护授权/14岁以下特别保护/危机破保密) + 学生档案可空 + token轻量登录
    - A2 历史报告：GET /api/sessions 分页倒序 + /{id}/report 下载 + HistoryPage卡片(徽章+危机标+双按钮)
    - A3 审计留痕：conversation_turns 对话每轮入库 + logs/crisis_*.json 独立审计 + logs/report_*.json 生成审计
    - A4 RAG 语料：CCMD-3公开摘要+卫健委热线指南+CBT入门 入库 + ingest CLI + 启动自动inject + ChatPage 来源标签
    - 新增 4 模块单测(test_auth/test_sessions_history/test_audit/test_rag_ingest)，原有 7 reports tests 全绿」
- **Acceptance Criteria Addressed**: 流程收尾
- **Test Requirements**:
  - `rule` TR-11.1: git status 干净，working tree 无未提交更改
  - `rule` TR-11.2: commit message 含「MVP 补齐验收」中文标题
