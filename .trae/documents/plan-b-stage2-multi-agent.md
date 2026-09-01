# B 二期 · LangGraph 四智能体编排 实施计划

## Context

MVP A1~A4 已 PASS（注册/历史/对话+危机审计/RAG 真实语料）。当前 [chat.py](file:///e:/Trae/PsycheFlow/backend/app/api/chat.py) 是**单次 LLM 调用**链路：detect_crisis → rag_search → provider.chat() → ConversationTurn 留痕。无多智能体编排、无意图路由、无对话阶段管理。

B 二期目标（开发计划 §9.2 + §11 安全原则）：升级为 **LangGraph 四智能体状态机** 分诊→测评→干预→升级，让对话具备意图路由 + 上下文感知 + 阶段可视化。**不做微调**（用户已确认），靠角色 prompt + RAG 完善 + 状态机编排提升对话质量。

**核心约束（永不违反）**：
- NFR-1 向后兼容：`POST /api/chat` 请求/响应 schema 不破坏（新增 `current_agent` 字段为可选，旧前端不崩）；89 旧 pytest 全绿
- §11 安全原则：Escalation Agent **永不调用 LLM**；detect_crisis_with_words 前置扫描在所有 LLM 调用前；12355 硬编码零 LLM；crisis_*.json 审计落盘不丢
- 量表计分与 LLM 隔离（不变化）
- 温度保守：分诊 0.1 / 干预 0.35
- Docker 端口 5174/8000/8001 + ./data 卷路径不改

## 架构设计

### LangGraph StateGraph 拓扑

```
                ┌──────────────────┐
[START] ─────▶ │ triage_agent     │ ──is_crisis=true──▶ escalation_agent ──▶ [END]
                │ (LLM role=intake)│                          (零 LLM)
                └────────┬─────────┘
                         │ is_crisis=false
                         ▼
                ┌──────────────────┐
                │ assessment_agent │
                │ (查 Session.    │
                │  assessments)   │
                └────────┬─────────┘
                         ▼
                ┌──────────────────┐
                │intervention_agent│ ──▶ [END]
                │(LLM role=dialog │
                │ + RAG retrieval) │
                └──────────────────┘
```

### 四智能体分工

| Agent | 调用 LLM | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| **Triage 分诊** | ✅ role=intake temp=0.1 | 意图分类（求助/倾诉/咨询/危机）+ 前置 detect_crisis 硬编码短路 | user_message | triage_intent, is_crisis, detected_words |
| **Assessment 测评** | ❌ 纯 DB 查询 | 查 Session.assessments 是否已填量表，提取 severity/crisis_level 作上下文 | session_id | has_assessment, assessment_context |
| **Intervention 干预** | ✅ role=dialog temp=0.35 | RAG 检索 + 共情回应 + CBT/DBT 技巧引用 | triage_intent, assessment_context, rag_sources | final_reply, sources |
| **Escalation 升级** | ❌ **零 LLM** | 硬编码 crisis_message() + 12355 转介 + write_crisis_audit 落盘 | detected_words | final_reply=crisis_message, crisis=true |

### TypedDict 共享状态（参考 EshaRana17/mental-health-multi-agent-pipeline）

```python
class AgentState(TypedDict):
    session_id: str
    account_id: str
    user_message: str
    history: list[dict]
    detected_words: list[str]
    is_crisis: bool
    has_assessment: bool
    assessment_context: dict | None
    rag_sources: list[dict]
    triage_intent: str | None
    final_reply: str
    sources: list[dict]
    current_agent: str  # triage/assessment/intervention/escalation
    agent_trace: list[str]  # ["triage", "assessment", "intervention"] 用于审计
```

## 文件清单

### 新建文件

**后端 agents 模块**：
- `backend/app/agents/__init__.py`
- `backend/app/agents/state.py` — AgentState TypedDict
- `backend/app/agents/graph.py` — StateGraph 4 节点 + conditional_edges（is_crisis → escalation / else → assessment）
- `backend/app/agents/nodes/triage.py` — detect_crisis 前置 + LLM 意图分类
- `backend/app/agents/nodes/assessment.py` — 查 Session.assessments 提取上下文
- `backend/app/agents/nodes/intervention.py` — RAG search + role=dialog LLM + sources 渲染
- `backend/app/agents/nodes/escalation.py` — crisis_message + write_crisis_audit（零 LLM）
- `backend/app/agents/prompts.py` — 分诊/干预角色 prompt 模板

**RAG 知识库扩展（5 篇 CBT/DBT 全量语料）**：
- `data/knowledge/cbt_techniques.md` — 认知行为疗法 6 大技术（认知重构/行为激活/暴露/放松/问题解决/技能训练）
- `data/knowledge/dbt_skills.md` — 辩证行为疗法 4 模块（正念/情绪调节/人际效能/痛苦耐受）
- `data/knowledge/relaxation_exercises.md` — 渐进肌肉放松 + 腹式呼吸 + 5-4-3-2-1 grounding 详解
- `data/knowledge/safety_plan_template.md` — Stanley-Brown 安全计划模板（含 6 步：警告信号→应对策略→社会支持→安全联系人→环境安全→理由）
- `data/knowledge/sadpersons_scale.md` — 自杀风险评估量表（性别/年龄/抑郁/既往尝试/乙醇/理性/丧失/组织/自杀/否定/睡眠 10 项）

**单测**：
- `backend/tests/test_agents_triage.py` — 4 case 意图分类（求助/倾诉/咨询/危机）+ detect_crisis 前置短路
- `backend/tests/test_agents_escalation.py` — 危机命中→escalation 零 LLM 调用 mock 验证 + crisis_*.json 落盘
- `backend/tests/test_agents_integration.py` — 完整 4 智能体 happy path（普通倾诉走 triage→assessment→intervention）

**Spec 文档**：
- `.trae/specs/stage2-multi-agent/spec.md` — B 二期需求与 AC
- `.trae/specs/stage2-multi-agent/tasks.md` — 任务拆分

### 修改文件

- [backend/app/api/chat.py](file:///e:/Trae/PsycheFlow/backend/app/api/chat.py) L39-169 — POST /api/chat 内部 `provider.chat()` 调用替换为 `graph.invoke(initial_state)`；外层 API 签名不变；返回多一个 `current_agent` + `agent_trace` 字段（新增不破坏）；ConversationTurn 仍写 user/assistant 两行；crisis 落盘仍走 escalation 节点
- [frontend/src/pages/ChatPage.tsx](file:///e:/Trae/PsycheFlow/frontend/src/pages/ChatPage.tsx) — 顶部加 4 阶段水平 stepper（分诊/测评/干预/升级 圆点+连接线）+ 每条 AI 回复旁加 Agent 徽章（`from: 干预 Agent` 蓝色小标签）；进度条根据 `current_agent` 字段高亮当前阶段
- [frontend/src/api.ts](file:///e:/Trae/PsycheFlow/frontend/src/api.ts) — ChatResponse 类型加 `current_agent?: string` + `agent_trace?: string[]` 可选字段

### 复用现有代码（避免重写）

- [backend/app/core/safety.py](file:///e:/Trae/PsycheFlow/backend/app/core/safety.py) `detect_crisis_with_words()` + `crisis_message()` — 直接在 triage 节点和 escalation 节点调用
- [backend/app/rag/service.py](file:///e:/Trae/PsycheFlow/backend/app/rag/service.py) `rag_service.search()` — intervention 节点调用，返回 [{text, source, chunk_id, distance}]
- [backend/app/core/llm.py](file:///e:/Trae/PsycheFlow/backend/app/core/llm.py) `provider.chat(role="intake"|"dialog", messages, temperature)` — triage + intervention 节点调用
- [backend/app/core/audit.py](file:///e:/Trae/PsycheFlow/backend/app/core/audit.py) `write_crisis_audit()` — escalation 节点调用
- [backend/app/models.py](file:///e:/Trae/PsycheFlow/backend/app/models.py) Session/ConversationTurn/AssessmentRecord — 不改 schema
- [backend/app/rag/cli_ingest.py](file:///e:/Trae/PsycheFlow/backend/app/rag/cli_ingest.py) — 新增 5 篇 md 后 `python -m app.rag.cli_ingest` 重新入库
- [backend/app/db.py](file:///e:/Trae/PsycheFlow/backend/app/db.py) `migrate_schema()` — 如 ConversationTurn 需要加 `agent_trace` 字段，复用 ALTER TABLE 模式

### 依赖

- `langgraph` + `langchain-openai` + `langchain-community` 已在 [backend/pyproject.toml](file:///e:/Trae/PsycheFlow/backend/pyproject.toml) L17-25 装好，无需新增

## 实施步骤（建议顺序）

### Task 1：Spec 文档先行（30 min）
- 写 `.trae/specs/stage2-multi-agent/spec.md`（A1~A6 AC：意图分类/危机前置短路/测评上下文/RAG sources/agent_trace 审计/前端阶段标签）
- 写 `.trae/specs/stage2-multi-agent/tasks.md`（Task 1-9 拆分）
- 用 NotifyUser 让用户批准 spec

### Task 2：AgentState + StateGraph 骨架（1 h）
- 写 `backend/app/agents/state.py` AgentState TypedDict
- 写 `backend/app/agents/graph.py` StateGraph 4 节点 + conditional_edges（is_crisis 路由）
- 写 4 个 node 占位（pass-through），先跑通图拓扑

### Task 3：4 节点实现（2 h）
- `nodes/triage.py`：detect_crisis_with_words 前置 → 命中 is_crisis=true 跳 escalation；未命中调 provider.chat(role="intake", temp=0.1) 分类意图
- `nodes/assessment.py`：db 查 Session.assessments → 提取 has_assessment + assessment_context
- `nodes/intervention.py`：rag_service.search() + provider.chat(role="dialog", temp=0.35) 共情回应；sources 字段渲染
- `nodes/escalation.py`：crisis_message() + write_crisis_audit() 落盘；零 LLM

### Task 4：chat.py 接入 LangGraph（30 min）
- POST /api/chat 内部 `provider.chat()` → `graph.invoke(initial_state)`
- 返回新增 `current_agent` + `agent_trace` 字段（旧字段不变，NFR-1）
- ConversationTurn 仍写 2 行（user/assistant），crisis_hit 标记保留

### Task 5：RAG 知识库扩展（1 h）
- 写 5 篇 md 文件入 `data/knowledge/`
- `python -m app.rag.cli_ingest` 重新入库
- 验证「重度抑郁症状」检索命中 cbt_techniques.md 或 dbt_skills.md（修 MVP Finding-2）

### Task 6：前端 ChatPage 阶段可视化（1 h）
- 顶部 4 阶段水平 stepper（分诊/测评/干预/升级 圆点+连接线）
- 每条 AI 回复旁 Agent 徽章（蓝色小标签 `from: 干预 Agent`）
- 根据 `current_agent` 高亮当前阶段
- 危机时 escalation 阶段红色高亮

### Task 7：3 模块单测（1 h）
- `test_agents_triage.py` 4 case 意图分类
- `test_agents_escalation.py` 危机命中零 LLM 调用 mock 验证
- `test_agents_integration.py` 完整 happy path
- 回归 89 旧测试仍全绿

### Task 8：浏览器端到端验收（30 min）
- Scene 1: 「我感觉最近压力大」→ triage intent=倾诉 → intervention 走 RAG → sources 含 cbt_techniques.md
- Scene 2: 「我想自杀」→ triage 命中 → escalation → 12355 + crisis_*.json 落盘
- Scene 3: 「我想做测评」→ triage intent=求助 → assessment 检测无量表 → 回复建议去 /scale
- Scene 4: ChatPage 顶部 stepper 4 阶段 + Agent 徽章可见

### Task 9：Git commit + Review（30 min）
- commit 中文 message「B 二期：LangGraph 四智能体编排 + RAG 知识库扩展 + ChatPage 阶段可视化」
- `.trae/specs/stage2-multi-agent/review.md` 独立复核

## 验证（Verification）

### 单测
```bash
docker exec psycheflow-backend uv run pytest -v
# 期望：92 passed (89 旧 + 3 新) / 1 skipped / 0 failed
```

### curl 端到端（4 场景）
```bash
# 1. 倾诉场景
docker exec psycheflow-backend python -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8000/api/chat',
    data=json.dumps({'message':'我感觉最近压力大','session_id':'<sid>','history':[]}).encode('utf-8'),
    method='POST', headers={'Content-Type':'application/json','Authorization':'Bearer <token>'})
d = json.loads(urllib.request.urlopen(req, timeout=120).read().decode('utf-8'))
print('current_agent:', d.get('current_agent'))
print('agent_trace:', d.get('agent_trace'))
print('sources:', [s.get('source') for s in d.get('sources',[])])
"
# 期望：current_agent=intervention, agent_trace=['triage','assessment','intervention'], sources 含 cbt_techniques.md

# 2. 危机场景
docker exec psycheflow-backend python -c "
import urllib.request, json
req = urllib.request.Request('http://localhost:8000/api/chat',
    data=json.dumps({'message':'我想自杀','session_id':'<sid>','history':[]}).encode('utf-8'),
    method='POST', headers={'Content-Type':'application/json','Authorization':'Bearer <token>'})
d = json.loads(urllib.request.urlopen(req, timeout=120).read().decode('utf-8'))
print('current_agent:', d.get('current_agent'))
print('agent_trace:', d.get('agent_trace'))
print('crisis:', d.get('crisis'))
print('reply:', d.get('reply')[:150])
"
# 期望：current_agent=escalation, agent_trace=['triage','escalation'], crisis=true, reply 含 12355

# 验证 audit 落盘
docker exec psycheflow-backend ls /app/data/logs/ | grep crisis
# 期望：新增 crisis_<sid>_<ts>.json
```

### 浏览器 UI
- ChatPage 顶部 4 阶段 stepper 可见
- 每条 AI 回复旁 Agent 徽章可见
- 危机时 escalation 阶段红色高亮

### 向后兼容 NFR-1
- 旧 7 reports pytest 全绿（量表计分与 LLM 隔离）
- POST /api/chat 旧字段 reply/sources/crisis 不变（仅新增 current_agent/agent_trace）

## 风险与应对

| 风险 | 应对 |
|---|---|
| LangGraph 状态机在容器内运行有版本兼容问题 | pyproject.toml 已装，先用最小 hello world graph 跑通 |
| Triage 意图分类 LLM 幻觉导致路由错 | 前置 detect_crisis 硬编码短路兜底；意图分类失败默认走 intervention |
| RAG 检索精度仍不够（MVP Finding-2） | 新增 5 篇 md 后重新 ingest；如仍不精准，加查询改写 prompt |
| chat.py 重构破坏 NFR-1 | 保留旧字段不变 + 新增字段可选；89 旧 pytest 跑通 |
| 前端 ChatPage 改造破坏现有 sources 卡片渲染 | api.ts 加可选字段，ChatPage 条件渲染（无 current_agent 时不显 stepper） |
