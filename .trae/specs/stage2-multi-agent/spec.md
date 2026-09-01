# B 二期 · 多智能体编排 Spec

## 1. 目标

将 `POST /api/chat` 从单次 LLM 调用升级为 **LangGraph 四智能体状态机**：分诊→测评→干预→升级，让对话具备意图路由 + 上下文感知 + 阶段可视化能力。

## 2. 范围

- **In scope**：后端 agents 模块（LangGraph StateGraph + 4 节点 + 角色 prompt）；chat.py 内部替换为 graph.invoke；RAG 知识库扩展 5 篇 CBT/DBT 全量语料；前端 ChatPage 加阶段 stepper + Agent 徽章；3 模块单测；4 场景浏览器端到端验收
- **Out of scope**：模型微调（用户已确认不做，靠 prompt + RAG 兜底）；量表库扩展（C 三期）；B 端管理后台（C 三期）

## 3. 核心约束（永不违反）

- **NFR-1 向后兼容**：`POST /api/chat` 请求 schema 不变（仍接受 `{message, session_id, history}`）；响应新增 `current_agent` + `agent_trace` 字段为**可选**（旧前端不崩）；89 旧 pytest 全绿
- **§11 安全原则**：Escalation Agent **永不调用 LLM**（零幻觉风险）；`detect_crisis_with_words` 前置扫描在所有 LLM 调用前；12355 硬编码；crisis_*.json 审计落盘不丢；量表计分与 LLM 隔离
- **温度保守**：分诊 role=intake temp=0.1 / 干预 role=dialog temp=0.35
- Docker 端口 5174/8000/8001 + ./data 卷路径不改

## 4. 四智能体分工

| Agent | LLM | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| Triage 分诊 | ✅ role=intake temp=0.1 | 意图分类（求助/倾诉/咨询/危机）+ 前置 detect_crisis 硬编码短路 | user_message | triage_intent, is_crisis, detected_words |
| Assessment 测评 | ❌ 纯 DB 查询 | 查 Session.assessments 是否已填量表，提取 severity/crisis_level 作上下文 | session_id | has_assessment, assessment_context |
| Intervention 干预 | ✅ role=dialog temp=0.35 | RAG 检索 + 共情回应 + CBT/DBT 技巧引用 | triage_intent, assessment_context, rag_sources | final_reply, sources |
| Escalation 升级 | ❌ **零 LLM** | 硬编码 crisis_message() + 12355 转介 + write_crisis_audit 落盘 | detected_words | final_reply=crisis_message, crisis=true |

## 5. Acceptance Criteria

### AC-B1 Triage 意图分类（4 case）
- 输入「我想做测评」→ triage_intent = "求助"
- 输入「我感觉最近压力大」→ triage_intent = "倾诉"
- 输入「什么是抑郁」→ triage_intent = "咨询"
- 输入「我想自杀」→ is_crisis=true，跳过 LLM 意图分类直接进 escalation
- Pass: 4/4 case 路由正确

### AC-B2 危机前置短路（零 LLM）
- detect_crisis_with_words 命中 → is_crisis=true → 路由到 escalation
- escalation 节点不调用 provider.chat()（mock 验证 call_count=0）
- 返回 crisis_message() 硬编码话术含「12355」
- crisis_*.json 落盘（文件名 `crisis_<sid>_<ts>.json`）
- Pass: mock provider.chat 不被调用 + 落盘文件存在

### AC-B3 Assessment 上下文提取
- Session 无 AssessmentRecord → has_assessment=false，回复中建议用户去 /scale
- Session 有 AssessmentRecord → has_assessment=true，assessment_context = {scale_id, severity, crisis_level, total_score}
- Pass: 两种 case 上下文正确

### AC-B4 Intervention RAG sources
- 普通倾诉 → rag_service.search() 返回 ≥ 1 条 sources
- sources 字段含 `source`（文件名）+ `text`（片段内容）
- 新增 5 篇 md 后，「重度抑郁症状」检索命中 cbt_techniques.md 或 dbt_skills.md（修 MVP Finding-2）
- Pass: sources 结构合规 + 关键查询命中 CBT/DBT 语料

### AC-B5 agent_trace 审计
- 每次响应含 `agent_trace: list[str]`（如 `["triage","assessment","intervention"]`）
- 危机场景 `["triage","escalation"]`
- Pass: trace 路径完整且符合实际路由

### AC-B6 前端阶段可视化
- ChatPage 顶部 4 阶段水平 stepper：分诊→测评→干预→升级
- 每条 AI 回复旁显示 Agent 徽章（如「from: 干预 Agent」）
- 根据 current_agent 高亮当前阶段；危机时 escalation 阶段红色高亮
- Pass: 浏览器 snapshot 验证 4 阶段 + 徽章可见

### AC-B7 向后兼容 NFR-1
- POST /api/chat 旧字段 reply/sources/crisis 不变（仅新增 current_agent/agent_trace）
- 89 旧 pytest + 3 新 agent pytest 全绿 → 92 passed / 1 skipped / 0 failed
- 7 reports pytest 仍全绿（量表计分与 LLM 隔离）
- Pass: 0 regression

## 6. Rubric（1-5 分打分，≥4 即 PASS）

### AC-U21 四智能体编排合理性
- 5 分：4 节点分工清晰、状态机拓扑正确、conditional_edges 路由逻辑无歧义、复用 safety/rag/llm/audit 现有模块
- 4 分：基本合理但节点边界模糊或某节点过度耦合

### AC-U22 安全合规充分性
- 5 分：Escalation 零 LLM mock 严格验证 + detect_crisis 前置 + 12355 硬编码 + crisis_*.json 落盘 + agent_trace 完整审计
- 4 分：核心安全约束满足但审计 trace 字段缺失或 mock 不严

### AC-U23 前端可视化用户感知度
- 5 分：stepper 阶段实时高亮 + Agent 徽章清晰 + 危机红色对比强烈 + 响应式适配
- 4 分：可视化基本达标但缺响应式或徽章样式不明显
