# 对话功能完善 + 病例解读功能 实施方案

## 一、仓库调研结论

### 现有对话链路（已实现）

- 前端 [ChatPage.tsx](file:///e:/Trae/PsycheFlow/frontend/src/pages/ChatPage.tsx) → `POST /api/chat/stream`（SSE 流式，fetch + ReadableStream 解析）
- 后端 [chat.py](file:///e:/Trae/PsycheFlow/backend/app/api/chat.py) 手动编排：triage（[triage.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/triage.py) 关键词危机前置扫描 + LLM 5 类意图；寒暄走 0.5b 极速直达）→ escalation（[escalation.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/escalation.py) 零 LLM 硬编码 12355 + 审计落盘）/ assessment（[assessment.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/assessment.py) 纯 DB 查测评）→ intervention（[intervention.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/intervention.py) RAG 混合检索 + LLM 共情流式回复）
- 每轮双写 `ConversationTurn` 表（[models.py](file:///e:/Trae/PsycheFlow/backend/app/models.py#L61-L78)），对话用独立 chat session（localStorage `psycheflow_chat_session_id`）
- LLM 双模式（cloud 百炼 / local Ollama）+ 三级兜底（[llm.py](file:///e:/Trae/PsycheFlow/backend/app/core/llm.py)）；匿名限流 10/min；人格体系 4 个（[personas.py](file:///e:/Trae/PsycheFlow/backend/app/agents/personas.py)）

### 对话功能现存不足（6 项，均有代码实证）

1. **刷新后对话"消失"**：`ConversationTurn` 已落库，但 ChatPage 的 `turns` 是 `useState([])` 初始化，无任何历史回填接口/逻辑；且 LLM 上下文靠前端 body 上送 `history`，刷新后上下文也断。无「新对话」按钮，chat session 永久累积。
2. **测评上下文对登录用户形同虚设**：对话 session 是 label='对话' 的独立 session，[assessment.py:39](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/assessment.py#L39) 按 `session_id` 查 AssessmentRecord **永远查不到**（测评记录挂在测评 session 上），`has_assessment` 恒 false，SAFETY_BASELINE 第 7 条"重度抑郁回复更谨慎"从不生效。
3. **无法停止生成**：[api.ts:177](file:///e:/Trae/PsycheFlow/frontend/src/api.ts#L177) `streamChat` 已支持 `AbortSignal` 参数，但 ChatPage 未传、UI 无停止按钮，3000 token 回复中途只能干等。
4. **输入框是单行 `<input>`**（[ChatPage.tsx:385](file:///e:/Trae/PsycheFlow/frontend/src/pages/ChatPage.tsx#L385)）：代码里处理了 Shift+Enter 换行，但 input 元素本身不支持多行，长段倾诉体验差。
5. **无长度防护**：`ChatRequest.message` 无 max_length；`history` 全量上送无截断，长对话 token 持续膨胀（费额度）。
6. **知识来源只显示最后一轮**：`sources` 是全局单份 state，每轮 `setSources([])` 清空，历史轮次的引用来源不可追溯。

### 病例解读功能：可复用基础设施

- 文件上传：`apiPostForm`（multipart）已用于语音转写；`python-multipart` 已在依赖中
- 安全链路：`detect_crisis_with_words` 前置硬编码扫描 + `write_crisis_audit` 落盘 + `crisis_message()` 12355 话术，可直接复用
- LLM：`provider.chat(role="report", ...)` 复用 deepseek-v4-flash（便宜、有思考链质量高），本地模式自动走 Ollama，无需新增模型配置
- RAG：混合检索服务现成，补一份术语科普语料即可增强
- 落库：`ConversationTurn` 加一个 JSON 列即可承载附件元数据；[db.py:50](file:///e:/Trae/PsycheFlow/backend/app/db.py#L50) 有现成 SQLite 轻量 ALTER TABLE 迁移模式
- 依赖安装：entrypoint.sh 启动时 `uv sync --no-dev`，pyproject 加依赖后重启容器自动安装

## 二、已确认的决策（用户拍板）

| 决策点 | 结论 |
|---|---|
| 病例输入形式 | 一期仅 **PDF（文字版，pypdf 本地抽取，零 LLM 额度）+ 文本框粘贴**；图片 OCR（云 qwen-vl / 本地 VL 模型）留二期 |
| 功能入口 | **对话页内附件上传**（📎 按钮），解读结果作为对话轮次，可继续追问 |
| 对话改进范围 | 6 项全做 |

## 三、文件与模块改动清单

### 后端

| 文件 | 改动 |
|---|---|
| `backend/pyproject.toml` | 新增依赖 `pypdf>=4.0`（纯 Python，~300KB） |
| [models.py](file:///e:/Trae/PsycheFlow/backend/app/models.py) | `ConversationTurn` 加 `attachments_json` 列（JSON nullable，存 `[{kind:'pdf'|'text', name, char_count}]`） |
| [db.py](file:///e:/Trae/PsycheFlow/backend/app/db.py) | `_migrate_sqlite_columns` 增补 `conversation_turns.attachments_json` 迁移（照现有模式） |
| `backend/app/core/case_parser.py`（新建） | `extract_pdf_text(bytes) -> str`：pypdf 逐页抽取；扫描件（无文本层）抛 `NoTextLayerError`；清洗空白 + 截断 6000 字 |
| [prompts.py](file:///e:/Trae/PsycheFlow/backend/app/agents/prompts.py) | 新增 `CASE_SYSTEM` / `CASE_USER_TEMPLATE`（科普解读约束，见下）；顺手修正头部"4 类"过时注释为 5 类 |
| `backend/app/agents/nodes/case.py`（新建） | `analyze_case(text, persona_id) -> (reply, sources, is_crisis)`：危机前置扫描 → RAG 术语检索（病例前 500 字做 query，top3）→ LLM 解读（role="report"，temp=0.2，max_tokens=2000）→ 空回复兜底 |
| [chat.py](file:///e:/Trae/PsycheFlow/backend/app/api/chat.py) | ① 新增 `GET /api/chat/history?session_id=`（回填，权限校验同 sessions：非匿名 session 须本人）② 新增 `POST /api/chat/case-upload`（multipart：file/text/session_id/persona_id，限流 report 组 3/min）③ `ChatRequest.message` 加 `max_length=2000` ④ history 入库/拼接截断最近 10 轮（20 条）⑤ SSE generator 把 assistant 落库移入 finally，客户端中断也保存已生成片段 |
| [assessment.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/assessment.py) | sid 查不到时，若 `account_id` 非空，跨 session 按账号查最近 1 条 AssessmentRecord（join Session 过滤 account_id） |
| [intervention.py](file:///e:/Trae/PsycheFlow/backend/app/agents/nodes/intervention.py) | `build_intervention_messages` 中 history 切片 `[-20:]` 双保险 |
| `backend/data/knowledge/精神科术语科普.md`（新建） | 公开常识级科普语料：常见诊断（抑郁发作/焦虑障碍/双相/强迫/ADHD 等）通俗解释、常见量表分段含义、药物类别（不说具体用药）、就医流程、何时需紧急求助。**只科普不指导诊疗**；随 RAG 自动 ingest 入库 |

### 前端

| 文件 | 改动 |
|---|---|
| [ChatPage.tsx](file:///e:/Trae/PsycheFlow/frontend/src/pages/ChatPage.tsx) | ① 挂载时按 chat_session_id 拉历史回填（含 sources/附件标记）② 顶栏加「新对话」按钮（新建 session + 清空）③ 停止生成按钮（AbortController，发送中变「停止」）④ `<input>` 换自适应高度 `<textarea>`（Enter 发送/Shift+Enter 换行，2000 字字数提示）⑤ `ChatTurn` 加 `sources?`/`attachment?` 字段，来源卡片内联到对应轮次（每轮可折叠），移除全局 sources 栏 ⑥ 📎 上传按钮 + 上传小面板（选 PDF / 粘贴文本 → 解读中 loading → 结果入对话流） |
| [api.ts](file:///e:/Trae/PsycheFlow/frontend/src/api.ts) | 新增 `getChatHistory(sid)`；`streamChat` 调用处传入 signal（已有参数，无需改封装） |

## 四、病例解读 prompt 约束要点（CASE_SYSTEM）

- 定位：**医学科普解读员，不是医生**。帮助看懂诊断证明/出院小结/测评报告上的术语
- 红线：不做诊断、不评价/调整治疗方案、不指导用药停药调剂量、不预测预后；一切以主治医生判断为准
- 输出固定 5 段：① 这是什么文档 ② 关键内容逐条通俗解释（看不懂/不确定的明确标注"建议向医生确认"）③ 医嘱建议在说什么 ④ 需要关注/尽快就医的信号 ⑤ 免责声明（遵医嘱 + 12355）
- 危机内容（自伤自杀等）不解读，直接给 12355/急诊引导（由前置硬编码扫描拦截，prompt 仅作双保险）
- 250~400 字/段、通俗、面向青少年和家长

## 五、实现步骤（依赖排序）

1. **后端数据层**：models 加列 + db.py 迁移 + pypdf 依赖
2. **后端病例核心**：case_parser.py → case.py 节点 → prompts 常量
3. **后端 API**：chat.py 加 history 回填端点 + case-upload 端点 + message 限长/history 截断/finally 落库
4. **后端对话增强**：assessment.py 账号级测评回退 + intervention history 切片
5. **知识语料**：精神科术语科普.md 落位（重启后 lifespan 自动 ingest）
6. **前端对话 6 项改进**：历史回填/新对话/停止按钮/textarea/sources 挂轮
7. **前端病例上传**：📎 面板 + 调用 + 渲染
8. **容器同步与验证**：restart backend（自动 uv sync 装 pypdf + 建列迁移）→ 逐项验证

## 六、隐私与合规

- **PDF 文件不落盘**：UploadFile 读入内存解析后即释放，不写 data/ 目录；DB 只存文件名+字数（attachments_json），不存原文
- 危机审计仍按现有约定写 `logs/crisis_*.json`（命中危机会含输入文本，属合规审计需要）
- 匿名可用 + 限流 3/min（report 组）；登录用户豁免；教师端 admin API 不暴露任何病例数据
- 解读结果全程带"不替代诊疗"免责声明；FooterDisclaimer 已在页

## 七、验证

- 后端单测（`backend/tests/`，pytest）：PDF 抽取/扫描件报错、危机命中不调 LLM、message 超限 422、history 截断 20 条、assessment 账号回退查到记录、case 节点空回复兜底
- E2E（容器内手动）：① 上传文字版 PDF → 返回 5 段结构化解读 ② 粘贴文本 → 解读 ③ 上传含危机关键词文本 → 返回 12355 话术且不调 LLM ④ 刷新页面 → 历史完整回填（含附件标记/来源卡片）⑤ 流式中点「停止」→ 立即中断且部分内容落库 ⑥ 登录有测评记录的账号对话 → 日志 assessment 节点 record_found ⑦ 长文本 >2000 字被拒 ⑧ 新对话按钮 → 开新 session 页面清空
- `docker compose restart backend` 后 `/api/health` 200、日志无迁移/导入错误

## 八、风险与兜底

- **扫描版 PDF/图片病历无文本层**：pypdf 抽不到文字 → 前端明确提示"该 PDF 是扫描图片版，请复制文字粘贴到文本框"（一期不做 OCR，符合决策）
- **LLM 医学幻觉**：prompt 强约束 + 固定结构 + 不确定标注 + 免责声明；RAG 术语语料只提供公开科普；定位严格限定"科普解读"
- **病例文本过长超 token**：解析层硬截断 6000 字 + 提示
- **客户端中断导致落库不一致**：assistant 落库移入 finally，已生成片段也保存
- **pypdf 安装失败**：entrypoint uv sync 走阿里云源；若失败容器启动即报错可见，不影响现有功能（回滚只需删依赖与新文件）
- **加列迁移风险**：SQLite ALTER TABLE ADD COLUMN 幂等且只增列，不动现有数据；迁移 try/except 不阻断启动
