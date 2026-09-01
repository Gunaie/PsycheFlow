# B 二期 · 多智能体编排 Tasks

## Task 1: Spec 文档先行 ✅
- 写 spec.md（A1~A6 AC + U21~U23 rubric）
- 写 tasks.md（本文件）

## Task 2: AgentState + StateGraph 骨架
- 新建 `backend/app/agents/__init__.py`
- 新建 `backend/app/agents/state.py` — AgentState TypedDict（含 13 字段）
- 新建 `backend/app/agents/graph.py` — StateGraph 4 节点 + conditional_edges（is_crisis → escalation / else → assessment → intervention → END）
- 4 个 node 占位 pass-through
- 验证：`python -c "from app.agents.graph import graph; print(graph.invoke({'user_message':'hi','is_crisis':False}))"` 跑通

## Task 3: 4 节点实现
- `backend/app/agents/nodes/triage.py`：detect_crisis_with_words 前置 → 命中 is_crisis=true 直接 return；未命中调 provider.chat(role="intake", temp=0.1) 分类意图
- `backend/app/agents/nodes/assessment.py`：db 查 Session.assessments → 提取 has_assessment + assessment_context
- `backend/app/agents/nodes/intervention.py`：rag_service.search() + provider.chat(role="dialog", temp=0.35) 共情回应 + sources 字段渲染
- `backend/app/agents/nodes/escalation.py`：crisis_message() + write_crisis_audit() 落盘；零 LLM
- `backend/app/agents/prompts.py`：分诊/干预角色 prompt 模板

## Task 4: chat.py 接入 LangGraph
- [backend/app/api/chat.py](file:///e:/Trae/PsycheFlow/backend/app/api/chat.py) POST /api/chat 内部 provider.chat() → graph.invoke(initial_state)
- 返回新增 current_agent + agent_trace 字段（旧字段不变）
- ConversationTurn 仍写 user/assistant 两行；crisis 落盘仍走 escalation 节点

## Task 5: RAG 知识库扩展
- 新建 `data/knowledge/cbt_techniques.md`（CBT 6 大技术）
- 新建 `data/knowledge/dbt_skills.md`（DBT 4 模块）
- 新建 `data/knowledge/relaxation_exercises.md`（放松技术详解）
- 新建 `data/knowledge/safety_plan_template.md`（Stanley-Brown 安全计划）
- 新建 `data/knowledge/sadpersons_scale.md`（自杀风险评估）
- `python -m app.rag.cli_ingest` 重新入库
- 验证：rag_service.search('重度抑郁症状') 命中 cbt_techniques.md 或 dbt_skills.md

## Task 6: 前端 ChatPage 阶段可视化
- [frontend/src/pages/ChatPage.tsx](file:///e:/Trae/PsycheFlow/frontend/src/pages/ChatPage.tsx) 顶部加 4 阶段水平 stepper
- 每条 AI 回复旁加 Agent 徽章
- [frontend/src/api.ts](file:///e:/Trae/PsycheFlow/frontend/src/api.ts) ChatResponse 类型加 current_agent/agent_trace 可选字段
- 根据 current_agent 高亮当前阶段；危机 escalation 红色

## Task 7: 3 模块单测
- `backend/tests/test_agents_triage.py` — 4 case 意图分类
- `backend/tests/test_agents_escalation.py` — 危机命中零 LLM mock 验证 + crisis_*.json 落盘
- `backend/tests/test_agents_integration.py` — 完整 4 智能体 happy path
- 回归 89 旧测试仍全绿

## Task 8: 浏览器端到端验收
- Scene 1: 「我感觉最近压力大」→ triage intent=倾诉 → intervention 走 RAG → sources 含 cbt_techniques.md
- Scene 2: 「我想自杀」→ triage 命中 → escalation → 12355 + crisis_*.json 落盘
- Scene 3: 「我想做测评」→ triage intent=求助 → assessment 检测无量表 → 建议去 /scale
- Scene 4: ChatPage stepper 4 阶段 + Agent 徽章可见

## Task 9: Git commit + Review
- git add 所有新增/修改文件
- commit 中文 message「B 二期：LangGraph 四智能体编排 + RAG 知识库扩展 + ChatPage 阶段可视化」
- 写 `.trae/specs/stage2-multi-agent/review.md` 独立复核
