# 步骤 5 实施计划：FastAPI 接口 + React 前端页面

## Context

步骤 1–4 已打通后端能力链：骨架可起、量表计分引擎（PHQ-A/SCARED + 单测）、百炼 Provider（4 模型按角色）、RAG 知识库（Chroma + 4 份语料 + 16 单测全绿）。但这些能力目前是"零件"，用户无法操作。

步骤 5 目标：把量表/RAG/LLM 串成两条用户可走通的主线——**量表评估流程**与**开放对话**——前后端联调，端到端可演示。报告 PDF 与深度危机兜底留到步骤 6。

已确认决策：① 对话用非流式 JSON（`provider.chat` 本就非流式，MVP 够用，后续可平滑升级 SSE）；② 评估+对话都做。

## 复用现有能力（不重写）

- `app/scales/registry.py` 的 `list_scales()` / `get_scale(id)` —— 量表发现入口
- `app/scales/base.py` 的 `Scale.score(answers:dict)->ScoreResult` —— 计分契约（含危机升级 `needs_crisis_escalation`）
- `app/core/llm.py` 的 `provider.chat(role, messages, temperature, max_tokens)->str`（async）与 `provider.embed()`
- `app/rag/service.py` 的 `rag_service.search(query, top_k)->[{text,source,distance}]`
- 前端 `react-router-dom 6.26.2`（已装未用）、vite 已把 `/api` 代理到 `backend:8000`、Tailwind primary 主题色

---

## 后端改动

### 1. 让量表自描述（小重构，给前端返回表单）

`backend/app/scales/base.py`
- `Scale` 基类新增类属性 `items: list = []`、`options: dict = {}`（子类覆盖）
- `Scale` 新增具体方法 `meta() -> dict`：返回 `{scale_id, scale_name, description, items, options}`
- `ScoreResult` 新增 `to_dict() -> dict`：把 `Severity`/`CrisisLevel` 枚举转 `.value`，产出 JSON 安全 dict（供 API 直接返回）

`backend/app/scales/phq_a.py` / `scared.py`
- 子类加 `items = PHQ_A_ITEMS` / `items = SCARED_ITEMS`，`options = PHQ_A_OPTIONS` / `SCARED_OPTIONS`
- 既有计分逻辑与单测不受影响（仅补类属性）

### 2. 量表 API（新文件 `backend/app/api/scales.py`）

- `GET /api/scales` → `[{scale_id, scale_name, description, item_count}]`
- `GET /api/scales/{scale_id}` → 完整 `meta()`（items + options，供前端渲染表单）
- `POST /api/scales/{scale_id}/score`
  - 请求体：`{"answers": {"1": 0, "2": 2, ...}}`（pydantic 模型校验）
  - 调 `get_scale(id).score(answers)` → 返回 `ScoreResult.to_dict()`
  - `ValueError`（缺题/非法分值）→ HTTP 422 + 明确 detail
  - `KeyError`（未知量表）→ HTTP 404

### 3. 危机安全模块（新文件 `backend/app/core/safety.py`）

硬编码、零 LLM、前置关键词扫描（呼应 `base.py` 设计原则）：
- `CRISIS_KEYWORDS`：自杀/自残/不想活/想死/结束生命/割腕/跳楼/了结自己/活不下去/轻生……
- `detect_crisis(text: str) -> bool`：小写化 + 子串匹配
- `crisis_message() -> str`：返回温暖但坚定的兜底话术 + `settings.crisis_hotline_12355`（12355）
- 可被 chat（步骤5）与报告兜底（步骤6）复用

### 4. 对话 API（新文件 `backend/app/api/chat.py`）

`POST /api/chat`
- 请求体：`{"message": str, "history": [{"role":"user"|"assistant","content":str}]?}`
- 响应：`{"reply": str, "sources": [{"text","source"}], "crisis": bool}`

流程：
1. `safety.detect_crisis(message)` 命中 → 返回 `{reply: crisis_message(), sources: [], crisis: True}`，**不调 LLM**
2. 否则：`rag_service.search(message, top_k=3)` 取片段 → 组装 system prompt（校园心理陪伴助手定位、非医生、共情、引用知识、严重议题建议求助专业）+ history + message → `provider.chat(role="dialog", ...)` → 返回 `{reply, sources, crisis: False}`
3. LLM/RAG 异常 → HTTP 502 + detail

### 5. 路由挂载（`backend/app/main.py`）

`include_router` 增加 `scales_router`、`chat_router`。

---

## 前端改动（`frontend/src/`）

### 1. 基础设施
- `main.tsx`：用 `<BrowserRouter>` 包裹 `<App/>`
- `App.tsx`：改为布局（顶栏标题 + 导航）+ `<Routes>`：
  - `/` → HomePage
  - `/scales/:scaleId` → ScalePage
  - `/chat` → ChatPage
- `api.ts`（新）：`apiGet(path)` / `apiPost(path, body)` 封装 fetch，`!ok` 抛带 detail 的错误，统一错误展示

### 2. 页面
- `pages/HomePage.tsx`：拉 `/api/scales`，渲染量表卡片（名称+描述+题数）跳转量表页；底部"开放对话"入口跳 `/chat`；保留"后端连通状态"小卡片（沿用现 App.tsx 的 health 展示）
- `pages/ScalePage.tsx`：
  - 拉 `/api/scales/{id}` 渲染题目 + 选项单选（PHQ-A 0-3 / SCARED 0-2）
  - 全部作答后提交 `/api/scales/{id}/score`
  - 结果区：总分 + 严重度（色阶）+ interpretation + 命中危机则显 `<CrisisBanner>`
- `pages/ChatPage.tsx`：
  - 消息列表 + 输入框，发送带 history 调 `/api/chat`
  - 展示回复；`sources` 可折叠；`crisis===true` 显 `<CrisisBanner>`

### 3. 组件
- `components/CrisisBanner.tsx`：红色醒目横幅，显示 12355 + 建议立即联系；评估页与对话页共用

沿用现有 `index.css`、Tailwind primary 主题色，不新增样式依赖。

---

## 测试

延续项目测试纪律（现有 16 测全绿）：
- `tests/test_safety.py`：`detect_crisis` 正/负例（"我想自杀"→True；"我有点难过"→False）
- `tests/test_api_scales.py`：FastAPI TestClient 测 `GET /api/scales`、`GET /api/scales/phq_a`、`POST score` 合法（返回总分+严重度）与非法（缺题→422）
- `tests/test_api_chat.py`：mock `provider.chat` + `rag_service.search`
  - 危机关键词输入 → 返回 `crisis:True` 且 **provider.chat 未被调用**
  - 普通输入 → `rag_service.search` 与 `provider.chat(role="dialog")` 各被调用一次

## 端到端验证

1. `docker exec psycheflow-backend uv run pytest` —— 全测全绿
2. curl/浏览器：
   - `GET /api/scales` → 2 个量表
   - `POST /api/scales/phq_a/score` `{"answers":{"1":3,...,"9":0}}` → 总分 + 严重度
   - `POST /api/scales/phq_a/score` 第9题>0 → `needs_crisis_escalation: true`
   - `POST /api/chat` `{"message":"我想自杀"}` → `crisis:True`、兜底话术、未耗 LLM
   - `POST /api/chat` `{"message":"最近考试压力大"}` → RAG 增强回复 + sources
3. 浏览器 `http://localhost:5174`：走完 PHQ-A 评估看结果；对话页输入危机词看红色横幅

## 不在步骤 5 范围

- 评估记录持久化（SQLite sessions）—— 留到步骤 6 报告生成时一并引入
- 对话流式 SSE —— 后续可平滑升级
- 报告 PDF 与深度危机兜底 —— 步骤 6
