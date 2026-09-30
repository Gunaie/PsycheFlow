# PsycheFlow 项目交接文档

> 最后更新：2026-10-01
> 阶段：D 五期全部完成 + 生产化加固（TLS/HSTS/CSP）+ SSE 首 token 优化（NFR-5 达标）+ Ollama 本地化（3.A 基座 / 3.B LoRA 微调 / D5 语音全离线）+ RAG 知识库 32 文件 327 片（eval_rag 护栏 recall@3=100%）+ 检索埋点落地 + 18.2 三任务数据备料（4232 条）+ 18.3 三 LoRA 重训部署 + 18.4 分诊全规则化与本地推理提速 + **18.5 多轮对话质量评测与三级质检重试阶梯（2026-10-01，已入库）**

> 🆕 **2026-10-01：多轮对话质量评测落地，质检重试升级三级阶梯**
>
> - **新增多轮评测护栏** [eval_multiturn.py](backend/scripts/eval/eval_multiturn.py)：4 场景 × 4 轮走生产 `/api/chat` 全链路（倾诉多轮延续/情绪跟进/话题切换/求做法后追问），逐轮按生产 `check_reply_quality` 口径判定 + 跨轮复读/闭合问句/做法类别去重 + 求做法场景类别并集 ≥3；结果 **15/16（94%，修复前 11/16）**，快照 `scripts/eval/results/multiturn_eval_latest.json`。
> - **质检重试升级三级阶梯**（intervention.py，流式/非流式同源）：①hint 针对性禁令（temp 0.6，点名复读原句/上轮做法类别）→ ②rag_refresh 换检索片段（top_k=6 排除已引用 chunk，temp 0.7）→ ③context_trim 剔除历史 assistant 回复（temp 0.7）；全部失败回退首次回复，`decision.llm.retry_modes` 留痕。
> - **两处 QC 语义修复**：RETRY_HINT 去除具体做法示例（7B 会照抄示例并补完库存句，实证为复读种子）；逐字重复检测剥离呼吸参数公式（4 吸 6 呼是硬约束标准口径，跨轮重现不算复读）；做法类别新增 planning（对齐 prompt 骨架的「任务拆解」示例）。非危机回复出现 12355 属安全底线保守升级，评测记 warn 不硬卡。
> - **测试**：pytest **380 passed / 1 skipped**（+5 质检测试：呼吸公式豁免/公式外复读仍判/planning 类别/阶梯各模式）。

> 🆕 **2026-09-30 架构变更：分诊（triage）完全规则化，零 LLM；本地推理提速落地**
>
> - **triage_node 不再调用任何模型**，路由顺序：危机词表短路（安全不变）→ 寒暄静态话术 → 咨询规则（服务边界/方法问句/知识问句）→ 求助渠道静态话术 → 求助祈使（帮帮我）→ 默认倾诉。`qwen2.5:triage-lora` 资产保留但生产路径不再加载（`.env` 的 LOCAL_MODEL_TRIAGE 实际已无效，留作回退资产）。
> - **配套提速**：① llm.py 按角色传 num_ctx（triage 已无模型；dialog/report=2048），消除 32K 默认上下文导致的 KV cache 显存溢出；② Ollama 容器加 `OLLAMA_KV_CACHE_TYPE=q8_0`；③ 后端启动后台预热 dialog-lora + embed（`LOCAL_WARMUP=false` 可关，pytest 自动跳过）。
> - **实测（RTX 4060 Laptop 8GB，HTTP 真实请求）**：寒暄/求助静态话术 **0.0–0.1s**；首轮倾诉（预热后）**6.4s**（优化前 55.7s，更早为 122.9s）；后续轮 **3.7s**（优化前 56–97s）。
> - **评测**：eval_triage 重构为「分诊路由评测」（纯规则，0.0s，无需 API key），数据集扩至 **53 条（5 类，含寒暄 3）→ 53/53 = 100%**；历史模型基线（云端 42/43、triage-lora 40/43）在 README 标为历史快照。盲评 30 场景 **30/30 成功**（脚本 timeout 120s + 7s 间隔避限流），字数 49–115、闭合问句 0。
> - **回复质检**：intervention 非流式/流式均加 min_len=50 下限（与 prompt「50–100 字」对齐，寒暄快速通道不设下限）；dialog_smoke 闭合问句正则与生产统一。
> - **测试**：pytest **365 passed / 1 skipped**。


> ⚠️ **运行模式提示（2026-09-27）**：本机 `.env` 为 `LLM_MODE=local` + `VOICE_MODE=local`（对话/分诊/报告/embedding/语音全本地，数据不出机）。
>
> - **本地模型现状（18.3，2026-09-27 重训版）**：dialog/report/triage 三角色均挂独立 LoRA——`qwen2.5:dialog-lora` / `qwen2.5:report-lora` / `qwen2.5:triage-lora`（Q4_K_M，各 ~4.4GB；覆盖同名旧 tag）。验收：eval_report **76/76** ✅、eval_triage **40/43（危机 8/8）** ✅、dialog_smoke 场景一过、场景二有闭合问句/重复（生产重试纸底）。历史版本：2026-09-08 反模板 dialog/report-lora 与 7b 基座分诊（41/43）已被本次重训替代。embedding = `bge-m3-cpu`（2026-09-26 起 num_gpu 0 全 CPU 驻留，与 bge-m3 向量等价，GPU 只跑 LLM 消除换入换出）；语音 = faster-whisper + sherpa-onnx。
> - **危机/寒暄均为零 LLM 硬编码前置**：detect_crisis 危机词表 → detect_greeting 寒暄正则，命中即直接产出回复，不调 LLM；安全红线不依赖模型与 RAG。
> - **RAG 检索**：阈值按嵌入模型自适应（云端 text-embedding-v3=0.75 / 本地 bge-m3=0.95），向量 L2 主排序 + BM25 补充召回；每次 search 落检索埋点（实际位置 `data/logs/rag_search_YYYYMMDD.jsonl` = settings.logs_dir，不记用户标识）；周度聚类脚本 `scripts/analyze_rag_gaps.py` ✅（2026-09-26 落地，三类信号聚类 + 单周 ≥5 次立项门槛）。
> - **知识库**：32 文件 / 327 片（19 内部编写科普 txt + 13 具名权威来源 md），改动后必须 `POST /api/rag/build` 重建索引并跑 eval_rag（recall@3 下降即阻断）。
>
> **2026-09-11 入库批次**：18.1 RAG 扩充批次（9 科普 txt + 4 权威 md + eval_rag 护栏 + 3 检索 bug 修复 + intervention 重试回退修复）+ 检索埋点（service.py `_write_search_trace` + 3 单测）+ 文档/注释同步 + 演示名单 + 仓库清理，已全部提交（详见 §7 对应批次）。

> **2026-09-26/27 未入库改动（18.2 数据备料完成 + quick win）**：
> - `scripts/analyze_rag_gaps.py`（周度 RAG gap 聚类，零依赖 stdlib，trace 在 data/logs/rag_search_*.jsonl）；
> - **triage 训练数据 1014 条已备齐**：`scripts/finetune/gen_triage_data.py`（8 桶/teacher pro/生产规则直调审计/flash+pro 双层判官），产物 `triage_train.jsonl`（危机226/求助259/倾诉254/咨询275）+ `_provenance.jsonl` 溯源 + `triage_rejected.jsonl` 69 条分歧侧车；43 条人工集只做 test；
> - `.env`/`.env.example` 切 `LOCAL_EMBED_MODEL=bge-m3-cpu`；意外修复 Chroma 索引静默丢失（POST /api/rag/build 重建 327 片，eval_rag recall@3 恢复 100%）；
> - **report 合成测评矩阵训练数据 243 条已备齐**：`scripts/finetune/gen_report_data.py`（不读 SQLite，直接合成答案向量→生产 score()/_compute_subdims() 作唯一 oracle；243 场景 = 常规 234 + 危机 9；pro 开思考 teacher；代码断言卡字数/三节/三方面/专业干预/危机坚定/安全防过升级；全量 563 调用留 212 + 救援轮 31/31 救回；空响应自动关思考兜底），产物 `report_train.jsonl` + `report_train_provenance.jsonl`（rejected 已清空），人工抽检 9 条危机全过；
> - **dialog 数据已完成（2026-09-27 晚）**：`gen_dialog_data.py` 放量 2700 场景（BoK2/并发8/分层 teacher：consult/help 走 flash、多轮+危机+修复走 pro），teacher 6086 次（低价 3000）+判官 3914 次，保留 2483（92%）；`qc_dialog_data.py` 全量自动检（4635 gpt 轮零违规/危机末轮 100% 求助动作/零近重）+ 152 条人工通读，3 条危机暗语侧车已剔除（**净 2480**，留档 dialog_sidecar_ids.txt）。成本教训：pro ¥24/M 输出价 + 4 轮 transcript 输出量被中途低估，本跑约 ¥55–70（另有 ~¥20 调参沉没），以百炼账单为准；
> - **merge 收口 + 训练接线就绪**：`merge_datasets.py` 重写（`--emit-systems` 从生产代码导出三套 system txt；零依赖 MinHash-LSH J≥0.8 去重；`--task all`；旧 CLI 兼容），产出 **dialog_merged 2979 / report_merged 243 / triage_merged 1010**；新增 `system_{dialog,triage,report}.txt`、`train_triage.yaml`，train_report.yaml 改 report_merged，dataset_info.json 加三 sharegpt 条目；`cloud_train.sh` 改三套 merged 随包上传直训（含 triage 段 + qwen2.5-triage-lora GGUF，缺失才回退 3.B 旧流程），`import_gguf.ps1` 加 triage；
> - **18.3 已完成（2026-09-27）**：云端 4090 训练完成（dialog 1h44m / report 4m43s / triage 8m54s），三个 Q4_K_M GGUF（各 4.4GB）已回传本地并导入 Ollama；.env 配好 LOCAL_MODEL_{DIALOG,REPORT,TRIAGE}，后端重建。**验收三关结果**：eval_report 76/76 ✅ / eval_triage 40/43（危机 8/8 ✅，3 条误判均在求助/咨询/倾诉边界）/ dialog_smoke 场景一通过、场景二有闭合问句与重复回复（生产重试纸底）。**30 场景盲评口径（已核对磁盘产物，更正早前「4 成功」误记）**：原批量脚本（60s timeout、未预热）产物为 **30/30 全超时**（冷加载~1min、热态单轮实测 56–97s）；当晚预热复测（180s timeout，id 7/3/13/21/25/29）**6/6 成功**，无闭合问句、开放式收尾、做法多样（产物 blind_eval_warmcheck.json）。详见 docs/验收报告_18.2_18.3.md §5。

---

## 1. Quick Start（5 步跑起来）

```bash
# 1. Clone + 创建 .env（从 §2 复制模板，API Key 问旧账号要）
git clone <repo_url> E:\Trae\PsycheFlow && cd E:\Trae\PsycheFlow
copy .env.example .env

# 2. 启动 3 容器（首次 build 约 5-10 分钟）
docker compose up -d --build

# 3. 等后端起来后，重建 Chroma 向量索引（重要！compose up 会清 chroma 数据）
#    知识库新增文件后同样跑此步（或 POST /api/rag/build，见 §3 命令表）
docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"
# 期望：{'indexed': 327, 'collection_size': 327}（32 个知识库文件）

# 4. 跑测试（验证全绿）
docker exec psycheflow-backend uv run pytest -q --no-header
# 期望：380 passed, 1 skipped, 0 failed（2026-10-01 复测值；基线首测 2026-09-10；若实测数不一致属正常，以实测为准并回写本文档）

# 5. 浏览器打开
# 前端：http://localhost:5174/（三态门户：未登录选学生端/教师端，已登录显示身份条一键进工作台/切端确认）
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
# 结构化提取/计分辅助（MoE 架构，分类精准）
MODEL_INTAKE=qwen3.8-2.4t-a95b
# 意图分类（关思考链 enable_thinking=False，5 类标签：寒暄/求助/倾诉/咨询/危机，寒暄与危机另有硬编码前置；triage 节点专用，首 token 0.38s）
MODEL_TRIAGE=qwen3.8-27b
# 开放对话/共情（deepseek-v4 有 reasoning_content 思考链，max_tokens 须够大）
MODEL_DIALOG=deepseek-v4-pro-0813
# 流式干预专用（关思考链 enable_thinking=False，首 token ~0.58s；SSE /api/chat/stream 用此角色）
MODEL_DIALOG_STREAM=qwen3.8-max
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
# SQLite 备份加密口令（合规 c；空则 backup_db.py 拒备份）
BACKUP_PASSPHRASE=

# ============ Ollama 本地兜底（五期；空=禁用保持 cloud-only） ============
# 容器连宿主机：http://host.docker.internal:11434/v1；连 compose ollama 服务：http://ollama:11434/v1
OLLAMA_BASE_URL=
OLLAMA_MODEL=qwen2.5:7b
```

### 关键模型注意事项

| 模型 | 角色 | 注意 |
|---|---|---|
| `qwen3.8-2.4t-a95b` | intake | MoE 架构，分类精准。**有思考链**，max_tokens 须够。**不支持 `enable_thinking=False`**（百炼报 400 restricted to True）。如果配额耗尽，次选 `qwen3.8-27b` |
| `qwen3.8-27b` | triage | 关思考链后无 reasoning_content，首 content 0.38s。triage 意图分类专用（9/9 准确，与 qwen-plus 持平）|
| `qwen3.8-max` | dialog_stream | 关思考链后首 content 0.58s。SSE 流式干预专用（共情质量优先）|
| `deepseek-v4-pro-0813` | dialog | **有 `reasoning_content` 思考链**，max_tokens 须 ≥3000，否则 content 为空 |
| `deepseek-v4-flash-0731` | report | 同上，max_tokens 须 ≥4000 |
| `qwen-audio-3.0-asr-flash` | ASR | DashScope 原生 multimodal-generation HTTP，**不走** OpenAI 兼容协议 |
| `qwen-audio-3.0-tts-flash` | TTS | DashScope 原生 audio/tts HTTP，**不走** SDK WebSocket（SDK 会崩） |

> **deepseek-v4 reasoning_content 坑**：deepseek-v4 系列有思考链字段，会先"思考"再输出 content。max_tokens 太小时被思考链用完，content 为空、`finish_reason=length`。**不是**配额耗尽（已验证两个 deepseek 模型仍有额度）。各节点 max_tokens：triage=50（qwen3.8-27b 关思考链，4 类标签足够）, intervention=3000, reports=4000, llm 默认=2048。
>
> **qwen3.8 思考链无法关闭坑**：qwen3.8-2.4t-a95b 强制 `enable_thinking=True`（百炼报 400 restricted to True），关不掉。流式场景下思考链阻塞首 content token 5-6s，**不能用**于 SSE 流式。commit `fe1a595` 曾改用 qwen-plus（无思考链）承担 triage/dialog_stream；qwen-plus 无额度后 commit `09c271e` 换为 `qwen3.8-27b`(triage) + `qwen3.8-max`(dialog_stream)——**qwen3.8 系列中 max/27b 可关思考链**，仅 2.4t-a95b 不可关（`llm.py _extra_body_for` 按 role 注入）。

---

## 3. 常用命令速查（Windows PowerShell 版）

| 目标 | 命令 | 备注 |
|---|---|---|
| 启动/重启 | `docker compose up -d --build` | **重建 chroma 容器会清空向量索引**，之后必须 build_index() |
| 重启单服务 | `docker restart psycheflow-backend` | 仅重启进程，**不会重新读 .env**。改 .env 必须用 `docker compose up -d backend` |
| 看后端日志 | `docker logs psycheflow-backend --tail 50` | 或加 `--since 10m` 看最近 10 分钟 |
| 跑 pytest | `docker exec psycheflow-backend uv run pytest -q --no-header` | 380 passed + 1 skipped（2026-10-01 复测值，基线首测 09-10，以实测为准） |
| 重建 RAG 索引 | `docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` | chroma 被重建后必跑；知识库新增/改动文件后也必跑 |
| HTTP 重建索引 | `curl.exe -s -X POST http://localhost:8000/api/rag/build` | PowerShell 下必须用 `curl.exe`（裸 `curl` 是 Invoke-WebRequest 别名）；返回 indexed 片数 |
| **RAG 检索护栏评测** | `docker exec psycheflow-backend uv run python scripts/eval_rag.py` | 65 条 query→期望文件：recall@3 应 100%、32/32 文件覆盖；结果写 `scripts/eval/results/rag_eval_latest.json` |
| **多轮对话质量评测** | `docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/eval/eval_multiturn.py` | 4 场景×4 轮真实 LLM（约 3 分钟）：逐轮生产质检口径 + 跨轮复读/做法去重；基线 15/16；脚本自动预热 + 每请求 sleep 7s 避限流，结果写 `results/multiturn_eval_latest.json` |
| 跑验证脚本 | `docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` | has_assessment + triage 抽样 |
| 跑性能压测 | `docker exec psycheflow-backend uv run python scripts/perf_bench.py` | 50 并发 health + 10 并发 chat（脚本位于 `backend/scripts/`，容器内 `/app/scripts/`） |
| **SSE 首 token 实测** | `docker exec psycheflow-backend uv run python scripts/sse_first_token.py` | NFR-5 验证：首 token 应 < 2s（寒暄路径 < 0.5s，对话路径 ~1.2s） |
| **流式 chunk 诊断** | `docker exec psycheflow-backend uv run python scripts/diag_stream.py` | 对比各模型首 content token 时间 + chunk delta 字段结构 |
| 前端生产构建 | `docker exec psycheflow-frontend sh -c "npm run build"` | 验证前端编译无错 |
| 进容器 shell | `docker exec -it psycheflow-backend bash` | |
| 生成自签 TLS 证书 | `docker run --rm -v "${PWD}/certs:/certs" alpine:latest sh -c "apk add --no-cache openssl >/dev/null 2>&1; openssl req -x509 -nodes -days 365 -newkey rsa:2048 -keyout /certs/privkey.pem -out /certs/fullchain.pem -subj '/CN=localhost'"` | 内网/开发用；生产放真实证书（Let's Encrypt）到 `./certs/` 同名文件 |
| 验证 nginx 配置 | `docker run --rm --add-host=backend:127.0.0.1 -v "${PWD}/frontend/nginx.conf:/etc/nginx/conf.d/default.conf:ro" -v "${PWD}/certs:/etc/nginx/certs:ro" nginx:alpine nginx -t` | 改 nginx.conf 后跑；`--add-host` 让 standalone 容器解析 `backend` upstream |
| 生产部署（HTTPS） | `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build` | 需先生成证书到 `./certs/`；前端 80→443 跳转，4 workers + healthcheck |

### 测对话接口

**非流式（旧 /api/chat，向后兼容）**：

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

**流式（SSE /api/chat/stream，推荐）**：

```bash
docker exec psycheflow-backend uv run python scripts/sse_first_token.py
# 期望：首 token < 2s（实测 1.72s），事件序列 agent(triage)→agent(assessment)→agent(intervention)→sources→token×N→done
# 危机消息：docker exec psycheflow-backend uv run python scripts/sse_first_token.py --message "我想自杀"
# 期望：首 token N/A（危机不流式），事件 agent(triage)→crisis→done，crisis reply 含 12355
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

### P12：qwen3.8 思考链无法关闭，SSE 首 token 阻塞（**重要，NFR-5 关键**）
**现象**：SSE 流式 `/api/chat/stream` 首 token 实测 18.08s，远超 NFR-5「首 token < 2s」。瓶颈：triage（intake=qwen3.8-2.4t-a95b）+ intervention（dialog=deepseek-v4-pro-0813）两节点都有 `reasoning_content` 思考链，stream 模式下先输出思考链（5-15s）再输出 content，`stream()` 只 yield content 故首 token 要等思考链跑完。
**误判 1**：以为 `qwen3.8` 可用 `extra_body={"enable_thinking": False}` 关思考——百炼报 400 `The value of the enable_thinking parameter is restricted to True`，**qwen3.8 强制开启思考**。
**误判 2**：以为关了思考后首 token 4.80s 达标——实际是 `stream()` 抛 BadRequestError 被 `stream_intervention` 捕获走 `FALLBACK_REPLY`，4.80s 是 fallback 一次性 yield 时间（triage 4.43s + 0.37s），非真实流式首 token。
**解决**（commit `fe1a595`）：triage 和 dialog_stream 两个角色都换 **`qwen-plus`**（无思考链模型），实测首 content ~0.5s。triage `max_tokens` 500→50（4 类标签）。`llm.py stream()` 移除 `extra_body`（对 qwen3.8 报 400，qwen-plus 不需要）。实测首 token **1.72s**（triage 0.65s + RAG 0.16s + stream 首 token 0.32s），NFR-5 达标。非流式 `/api/chat` 仍用 deepseek-v4-pro（高质量），流式用 qwen-plus（首 token 快）。
**诊断**：`docker exec psycheflow-backend uv run python scripts/diag_stream.py` 对比各模型 chunk 结构 + 首 content 时间；`scripts/sse_first_token.py` 跑端到端 SSE 首 token 实测。

---

## 5. 当前模型配置

| Role | 模型 | 用途 | 温度 | max_tokens | 状态 |
|---|---|---|---|---|---|
| intake | `qwen3.8-2.4t-a95b` | 结构化提取/计分辅助（有思考链） | 0.1 | 2048 | ✅ |
| triage | `qwen3.8-27b` | triage 意图分类（**关思考链，首 token 0.38s**） | 0.1 | 50 | ✅ |
| dialog | `deepseek-v4-pro-0813` | intervention 非流式共情对话（有思考链） | 0.6 | 3000 | ✅ |
| dialog_stream | `qwen3.8-max` | intervention **SSE 流式**共情对话（关思考链，首 token 0.58s） | 0.6 | 3000 | ✅ |
| report | `deepseek-v4-flash-0731` | 报告生成（有思考链） | 0.1 | 4000 | ✅ |
| embed | `text-embedding-v3` | Chroma RAG 向量化 | — | — | ✅ |
| asr | `qwen-audio-3.0-asr-flash` | 语音识别（DashScope HTTP） | — | — | ✅ |
| tts | `qwen-audio-3.0-tts-flash` | 语音合成（DashScope HTTP） | — | — | ✅ |

> **triage 角色分工演进**：原 intake（qwen3.8）承担 triage 意图分类 + 结构化提取两职，但 qwen3.8 有思考链阻塞 SSE 首 token。commit `fe1a595` 拆分出独立 `triage` 角色配 qwen-plus（无思考链），intake 仍保留 qwen3.8 给结构化提取/计分辅助。后续 qwen-plus 无额度，本次替换为 qwen3.8-27b(triage)+qwen3.8-max(dialog_stream)，两者均经 `_extra_body_for` 关 `enable_thinking=False`（qwen3.8-2.4t-a95b 不可关），实测首 token 1.75s、triage 9/9 准确，NFR-5 仍达标。

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
| [api/auth.py](backend/app/api/auth.py) | 注册/登录（token=secrets.token_hex(32)；AuthResp 含 role 控制前端菜单可见性） |
| [api/sessions.py](backend/app/api/sessions.py) | 会话与报告端点：GET /api/sessions 列表（只含有测评记录的 session）/ POST assessments / PDF 生成（inline）与下载（attachment） |
| [agents/graph.py](backend/app/agents/graph.py) | StateGraph 四节点拓扑 |
| [agents/state.py](backend/app/agents/state.py) | AgentState TypedDict |
| [agents/nodes/triage.py](backend/app/agents/nodes/triage.py) | 分诊（2026-09-30 起全规则零 LLM：detect_crisis 危机词表 → detect_greeting 寒暄 → 咨询规则（服务/方法/知识问句）→ 求助渠道话术 → 求助祈使 → 默认倾诉） |
| [agents/nodes/assessment.py](backend/app/agents/nodes/assessment.py) | 测评（纯 DB 查询 has_assessment） |
| [agents/nodes/intervention.py](backend/app/agents/nodes/intervention.py) | 干预（RAG + LLM，空回复 fallback；质检不合格走三级重试阶梯 hint→rag_refresh→context_trim，最多 3 次且每次重新质检，全失败保留首次回复；呼吸参数公式剥离后判复读，做法类别含 planning） |
| [agents/nodes/case.py](backend/app/agents/nodes/case.py) | 案例上传分析（api/chat.py 直接调用，不走 LangGraph；RAG 检索 caller="case"） |
| [agents/nodes/escalation.py](backend/app/agents/nodes/escalation.py) | 升级（零 LLM + crisis_message + audit） |
| [agents/personas.py](backend/app/agents/personas.py) | D2：4 人格定义（default/sister/senior/listener） |
| [agents/prompts.py](backend/app/agents/prompts.py) | 角色 prompt 模板 |
| [core/llm.py](backend/app/core/llm.py) | LLM Provider（cloud 百炼 / local Ollama 双模式，按 role 路由） |
| [core/voice.py](backend/app/core/voice.py) | ASR/TTS 云端实现（DashScope HTTP，按 voice_mode 路由） |
| [core/voice_local.py](backend/app/core/voice_local.py) | D5：本地语音（faster-whisper ASR + sherpa-onnx TTS，VOICE_MODE=local 全离线） |
| [core/config.py](backend/app/core/config.py) | 配置（读 .env） |
| [core/safety.py](backend/app/core/safety.py) | detect_crisis_with_words / crisis_message / CRISIS_KEYWORDS（危机判定单一事实源） |
| [core/audit.py](backend/app/core/audit.py) | write_crisis_audit / write_report_audit（文件 + DB 双写） |
| [rag/service.py](backend/app/rag/service.py) | RAG build_index / search（自适应阈值 + 向量主排序/BM25 补召回 + 检索埋点） |
| [rag/store.py](backend/app/rag/store.py) | Chroma 向量库封装（reset_namespace 等） |
| [api/rag.py](backend/app/api/rag.py) | RAG 调试端点（POST /api/rag/build 重建索引等） |
| [reports/service.py](backend/app/reports/service.py) | 单页报告生成（MHT 六章节风格 + 全量表子维度计算 `_compute_subdims` + 雷达图数据 + 测评用时 + LLM 发展建议空回复兜底） |
| [reports/templates/report.html](backend/app/reports/templates/report.html) | MHT 报告 HTML 模板 |
| [scales/](backend/app/scales/) | 量表库：PHQ-A / SCARED / SDQ / MHT |
| [db.py](backend/app/db.py) | SQLAlchemy engine + Base |
| [models.py](backend/app/models.py) | Session / AssessmentRecord / ConversationTurn / User / ScreeningBatch / BatchEntry |

### 前端（frontend/src/）

| 路径 | 作用 |
|---|---|
| [App.tsx](frontend/src/App.tsx) | React Router 入口：`/` 三态门户 + 学生端 `/home /assess*` + 教师端 `/admin/*`；RequireTeacher/RequireStudent/RedirectIfAuthed 三守卫（教师手输学生路由弹回后台；已登录访问登录注册页按角色回首页）+ 旧路径重定向 |
| [api.ts](frontend/src/api.ts) | fetch 封装（含 SSE streamChat / apiGetBlob / clearToken 清全部用户态 localStorage） |
| [pages/PortalPage.tsx](frontend/src/pages/PortalPage.tsx) | 系统门户 + 身份换乘站（独立全屏布局：横幅插画 + 双端卡；已登录显示身份条与「进入我的工作台」，教师切学生端确认退出、学生切教师端走教师登录页） |
| [pages/ChatPage.tsx](frontend/src/pages/ChatPage.tsx) | 对话页（SSE 流式 + 🎤 录音 + 🔊 朗读 + 人格切换 + 全宽布局；空状态插画缺图降级 💬） |
| [pages/ScaleSelectPage.tsx](frontend/src/pages/ScaleSelectPage.tsx) | 量表选择页（/assess 入口，四量表卡片 + 合并筛查推荐位；免登录文案纠偏） |
| [pages/ScalePage.tsx](frontend/src/pages/ScalePage.tsx) | 测评页（/assess/:scaleId 单量表每次新建独立 session；/assess/combined 合并双量表；返回测评选择带未提交作答确认） |
| [pages/HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) | 历史报告列表 + 详情弹窗 + PDF 真实下载（blob + `<a download>`，非新标签预览；挂登录守卫） |
| [pages/HomePage.tsx](frontend/src/pages/HomePage.tsx) | 学生首页 /home（返回门户 + 三步引导卡 + 四功能卡含筛查码入口 + 动态 CTA） |
| [pages/ScreeningPage.tsx](frontend/src/pages/ScreeningPage.tsx) | 学生筛查入口（/screening 凭码匿名作答，印制材料稳定 URL） |
| [pages/admin/AdminShell.tsx](frontend/src/pages/admin/AdminShell.tsx) | 管理后台统一布局（深色顶栏：品牌 + action 插槽 + clearToken 退出；登录页不用） |
| [pages/admin/](frontend/src/pages/admin/) | 管理后台三页（登录/批次列表/批次详情，均挂 RequireTeacher 守卫；批次详情含返回批次列表 + 胶囊操作按钮） |
| [lib/recorder.ts](frontend/src/lib/recorder.ts) | D3：浏览器 WAV 录音器（16kHz PCM） |
| [components/BackLink.tsx](frontend/src/components/BackLink.tsx) | 统一胶囊返回按钮（light/dark 双 variant；首页/登录/注册/筛查/批次详情/管理登录接入） |
| [components/CrisisBanner.tsx](frontend/src/components/CrisisBanner.tsx) | 危机横幅组件 |

### 诊断/验证脚本（backend/scripts/，容器内 /app/scripts/）

| 路径 | 作用 |
|---|---|
| [scripts/sse_first_token.py](backend/scripts/sse_first_token.py) | **NFR-5 验证**：SSE /api/chat/stream 首 token 实测（实测 1.72s） |
| [scripts/diag_stream.py](backend/scripts/diag_stream.py) | 各模型 stream chunk 结构 + 首 content token 对比（诊断思考链阻塞） |
| [scripts/perf_bench.py](backend/scripts/perf_bench.py) | 性能压测：50 并发 health + 10 并发 chat |
| [scripts/verify_leftovers.py](backend/scripts/verify_leftovers.py) | 遗留项验证：has_assessment 链路 + triage 意图抽样 |
| [scripts/voice_api_e2e.py](backend/scripts/voice_api_e2e.py) | D3 语音 ASR/TTS 端到端验证 |
| [scripts/voice_probe.py](backend/scripts/voice_probe.py) | D3 语音 API 单点探测 |
| [scripts/tts_http_diag.py](backend/scripts/tts_http_diag.py) | TTS HTTP API 诊断（DashScope 原生端点） |
| [scripts/diag_deepseek.py](backend/scripts/diag_deepseek.py) | deepseek-v4 reasoning_content 思考链诊断 |
| [scripts/reset_teacher_password.py](backend/scripts/reset_teacher_password.py) | **运维**：教师忘记密码重置（`docker exec -it psycheflow-backend uv run python scripts/reset_teacher_password.py --label 账号名 [--password 新密码]`；省略密码则自动生成 12 位并打印；仅限 role=teacher；E2E 实测新密码 200/旧密码 401） |
| [scripts/backup_db.py](backend/scripts/backup_db.py) | **运维**：SQLite 一致性备份 + AES-256-CBC 加密（需 BACKUP_PASSPHRASE） |
| [scripts/export_report_finetune_data.py](backend/scripts/export_report_finetune_data.py) | **本地版 3.B 预备**：从历史测评/报告反向构造微调 JSONL（LLaMA-Factory 格式），默认输出 `data/finetune/finetune_report.jsonl`（未跟踪文件，配合 [docs/本地模型化方案.md](docs/本地模型化方案.md) 使用） |
| [scripts/e2e_acceptance.py](backend/scripts/e2e_acceptance.py) | **验收**：端到端 7 步验收（健康→登录→对话→危机→报告→审计），7/7 PASS |
| [scripts/dialog_smoke.py](backend/scripts/dialog_smoke.py) | **对话质量回归**：复用生产 build_intervention_messages（含 RAG + 逐轮 history），场景化 4 轮（独立咨询/倾诉转咨询），自动检查闭合问句与空历史幻觉归因；`DIALOG_SMOKE_TEMP` 可调温 |
| [scripts/eval_triage.py](backend/scripts/eval_triage.py) + [scripts/eval/triage_dataset.json](backend/scripts/eval/triage_dataset.json) | **P2 评测**：triage 意图分诊评测（43 条标注样本，危机 8/咨询 22/倾诉 12/求助 1，含 6 条边界标注；云端 qwen3.8-27b 基线 97.7%=42/43，2026-09-27 本地 triage-lora 40/43，危机硬编码 8/8=100% 安全回归）；容器内 `uv run python scripts/eval_triage.py [--limit N] [--verbose]` |
| [scripts/eval_report.py](backend/scripts/eval_report.py) | **P2 评测**：报告结构合规评测（5 场景×15 断言：六章节/个人信息/测评用时/雷达图/PDF 完整性/危机红框双向/建议无危机话术，100%）；复用计分引擎+真实 LLM 叙事，合成数据自动清理；容器内 `uv run python scripts/eval_report.py [--only key]` |
| [scripts/eval_rag.py](backend/scripts/eval_rag.py) + [scripts/eval/rag_eval_dataset.json](backend/scripts/eval/rag_eval_dataset.json) | **18.1 检索护栏**：65 条 query→期望文件（expect 可为数组，多可接受文件），跑 `rag_service.search(top_k=3)` 统计 hit@1/recall@3/MRR/文件覆盖；2026-09-09 基线 **recall@3=100%（65/65）、hit@1=76.9%、MRR=0.874、32/32 文件覆盖**；知识库每次扩充后必跑，recall@3 下降即阻断 |
| [scripts/eval/eval_multiturn.py](backend/scripts/eval/eval_multiturn.py) | **18.5 多轮对话护栏**：4 场景×4 轮（倾诉多轮延续/情绪跟进/话题切换/求做法后追问）走生产 HTTP 全链路，逐轮 check_reply_quality（min_len=50/闭合问句/跨轮复读/幻觉归因/同类做法）+ 求做法场景类别并集 ≥3 + 非危机安全断言（12355 出现记 warn）；import 生产函数保证口径不漂移；2026-10-01 基线 **15/16 = 94%**（修复前 11/16），快照 `results/multiturn_eval_latest.json`；跑真实 LLM 需预热 + 180s timeout + 请求间隔 7s |
| [scripts/eval/results/](backend/scripts/eval/results/) | 评测基线快照（`*_eval_latest.json` 入库，带时间戳明细 gitignore） |

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

### SSE 流式 + 首 token 优化 ✅（commit 70d2917 → fe1a595 → 本地极致优化）
- **SSE 骨架**（commit 70d2917）：POST /api/chat/stream（保留旧 /api/chat 向后兼容）。手动跑 triage→assessment 同步等结果，再 provider.stream() 边生成边推 token；危机路径不流式推完整 crisis_message 后 close；审计双写不破坏。SSE 事件：agent/sources/token/crisis/error/done。前端 streamChat（fetch+ReadableStream 解析 SSE，不用 EventSource 因不支持 POST+auth）+ ChatPage 边收 token 边显示
- **首 token 优化**（commit fe1a595，NFR-5 达标）：首 token **18.08s → 1.72s**
- **本地极致优化**（2026-09-06 起）：
  - **寒暄闪电直达**：`detect_greeting` 白名单正则在 LLM 分类前硬编码识别纯寒暄短句（你好/你是谁/你是机器人吗等，混有其他内容不命中，>30 字排除），零 LLM 直接产出 `final_reply` 结束 graph；LLM 分类出的寒暄走同一快速通道。
  - **UI 精简**：彻底移除前端 `StageStepper` 流程条，界面回归纯净对话（仅保留每条消息的 AgentBadge 标识）。
  - **RAG 检索演进**：0.70 固定阈值 + 关键词加权方案已废弃，最终为阈值按嵌入模型自适应（云端 0.75/本地 bge-m3 0.95）+ 向量 L2 主排序 + BM25 补充召回，详见 §7「18.1 RAG 知识库扩充」批次。
  - 瓶颈定位：triage（intake=qwen3.8）+ intervention（dialog=deepseek-v4-pro）两节点都有 reasoning_content 思考链，stream 模式下先输出思考链 5-15s 再输出 content
  - 误判排查：qwen3.8 不支持 `enable_thinking=False`（百炼报 400 restricted to True）；以为关思考后 4.80s 达标，实际是 stream() 抛 BadRequestError 被 stream_intervention 捕获走 FALLBACK_REPLY 的假象
  - 解决：triage + dialog_stream 两个角色都换 **qwen-plus**（无思考链，首 content ~0.5s）。新增 `model_triage`/`temp_triage` 配置项 + role="triage" 映射。triage max_tokens 500→50。llm.py stream() 移除 extra_body
  - 时序分解（1.72s）：triage 0.65s + RAG 0.16s + stream 首 token 0.32s
  - 浏览器实测 PASS：agent stepper 分诊→测评→干预实时更新，3 个知识卡片渲染，回复边生成边显示；危机消息红色 banner + 12355 + 无 token 流式
  - 187 passed / 1 skipped（+6 流式单测 test_api_chat_stream.py）
- **模型替换**（2026-09-02，qwen-plus 无额度）：triage→`qwen3.8-27b`、dialog_stream→`qwen3.8-max`，均经 `llm.py._extra_body_for` 关 `enable_thinking=False`（qwen3.8-2.4t-a95b 不可关，max/27b 可关）。实测首 token **1.75s**、triage 准确率 9/9（无退化）、187 passed。NFR-5 仍达标

### 测评纠偏与前端体验批次 ✅（2026-09-02 ~ 09-05，commit `b43994e`）

- **一量表一报告纠偏**：单量表路由每次新建独立 session 只挂一条 assessment（禁止跨量表复用）；合并双量表（PHQ-A+SCARED）一个 session 挂两条。根因修复跨用户串号 + 多量表报告内容重复两 bug 同源问题——`clearToken` 漏清 `psycheflow_active_session_id`/`chat_session_id`，用户 B 复用 A 的 session 导致报告显示 A 的姓名、单量表累积历史量表。对话用独立 chat session key 与测评 session 解耦
- **报告增强**（[reports/service.py](backend/app/reports/service.py)）：`_compute_subdims` 扩展支持 SDQ（5 因子，7/11/14/21/25 反向计分，亲社会行为维度用 1-pct 反转映射）与 MHT（8 因子，冲动倾向含 85/97 自杀相关条目仅展示维度分、危机走顶部 crisis_message）；雷达图对 3+ 子维度量表启用；SCALE_INTRO 补 SDQ/MHT 测评工具介绍；测评用时 = session.created_at 与最新 assessment.created_at 差值（X分Y秒）；报告个人信息（姓名/性别/年龄/学号/年级）从 `session.account.profile` 真实读取
- **前端体验**：App.tsx 导航加「测评/历史」链接 + role 显隐管理后台（AuthResp 新增 role 字段）；HomePage 三步引导卡 + 动态 CTA；ChatPage 空状态推荐话题 + 全宽布局滚动；对话知识卡片默认折叠、LLM 不复述知识库原文；SCARED 每题选项框按本量表 optionKeys 渲染（修复错用 PHQ-A 4 选项导致空白）；SDQ/MHT 去重复标题（showHeader 参数）；MHT 26/28 题保持原表述
- **PDF 下载交互分化**：ScalePage「生成 PDF 报告」= 新标签页预览（window.open('') + blob location.href，同步开空标签避弹窗拦截）；HistoryPage「下载 PDF」= **真实磁盘下载**（apiGetBlob + 动态 `<a download>` 程序化点击，2026-09-05 修复——blob 新标签页会被 Chrome 内置 PDF 查看器内联打开成"预览"，且带 `downloadingId` 生成中状态）
- **测试修正**：[test_auth.py](backend/tests/test_auth.py) `test_bearer_token_links_session_to_account` 过期——list_sessions 已改为只返回有测评记录的 session（排除纯对话），测试补挂一条全 0 PHQ-A 后通过
- **验证**：`tsc --noEmit` 0 错误；pytest **199 passed / 1 skipped / 0 failed**（工作区实测 2026-09-05）

### 路由重构与双端门户批次 ✅（2026-09-05，commit `884e7fa` → `2cf193c`）

- **试点合规材料**（884e7fa，[docs/](docs/)）：监护人知情同意书模板（与系统注册页四项同意逐条对应 + 回执联）、教师操作手册（建批次/发码/看报告/危机处置 SOP + 每日巡检命令 + FAQ + 学生作答指引附录）；README 相关文档区补链接
- **路由重构**（5a12ac4，[App.tsx](frontend/src/App.tsx)）：学生测评统一 `/assess` 前缀（`/assess` 选择、`/assess/:scaleId` 单量表、`/assess/combined` 合并），旧路径 `/scale`、`/scale/combined`、`/scales/:scaleId` 全部 `<Navigate replace>` 兼容重定向（参数转发需自写 LegacyScaleRedirect 组件，Navigate 不支持参数插值）；`/screening` 与 `/admin` 系、`/login`、`/register` 保持不变（印制材料 URL 稳定）
- **AdminShell 统一布局**（[AdminShell.tsx](frontend/src/pages/admin/AdminShell.tsx)）：管理后台深色顶栏（品牌 + action 插槽 + 退出），批次列表/详情两页接入；退出改用 `clearToken()` 清全部 5 个用户态 key（原手动删 3 个会漏 session id 有串号风险）
- **守卫**：`/admin`、`/admin/batches/:id` 挂 RequireTeacher（无 token/非 teacher → /admin/login，服务端 API 鉴权仍兜底）；`/history` 挂登录守卫 → /login；`/chat` 保持匿名可用
- **三态门户**（[PortalPage.tsx](frontend/src/pages/PortalPage.tsx)）：`/` = 系统大门（独立全屏布局不套学生 Shell）——未登录显示 🎓学生端/🏫教师端双卡；学生登录态自动跳 `/home`；教师登录态自动跳 `/admin`。学生首页 `/` → `/home`，顶栏品牌/首页同步；登录成功直达 `/home`；退出登录统一回 `/` 门户。**分发口径：学校只发根地址，各走各的门**
- **双端零交叉隔离**：教师端 AdminShell 无"返回学生端"链接、`/admin/login` 加「← 返回首页」防迷路；学生页零教师痕迹（教师登录态下学生顶栏隐藏测评/对话/历史，仅剩管理后台；手输学生 URL 可用但无导航暴露）；教师登录态访问 `/` 直接重定向 `/admin`——教师世界里不存在学生首页
- **前端视觉丰富**（2cf193c）：门户横幅 + 双端卡配图 + 对话空状态插画（`frontend/public/images/` 四张 AI 生成扁平插画风 PNG，深蓝 #1e3a5f 主色统一风格；缺图 onError 自动降级 emoji/隐藏，不出现破图）；`public/` 里 15 个报告测试残留迁 `tests/report-samples/`（git mv 零引用确认）
- **运维脚本**：[scripts/reset_teacher_password.py](backend/scripts/reset_teacher_password.py) 教师忘记密码重置（--label 必填、--password 可省自动生成 12 位无易混字符；仅 role=teacher；复用 `_hash_password` PBKDF2 格式）。容器内 E2E 实测：注册临时教师 → 重置 → 新密码 login_by_password 200、旧密码 401
- **验证**：`tsc --noEmit` 0 错误（每批次均过）；浏览器目检（Playwright）：门户横幅/双卡图/对话插画全部渲染、退出落门户、教师访问 `/` 自动跳工作台；pytest 基线不变（后端仅加脚本无 API 改动）

### GitHub CI 与 LLM 输出评估批次 ✅（2026-09-05，commit `7f65806` → `9c49626`）

- **GitHub Actions CI**（[.github/workflows/ci.yml](.github/workflows/ci.yml)）：`backend-test`（uv 0.11.5 钉版本 + `--frozen` 锁同步 + wqy-zenhei 字体 + pytest）/ `frontend-check`（npm ci + tsc -b + vite build）/ `docker-build`（仅 main push，buildx GHA 缓存，backend + frontend prod target）。无 .env 可跑（config 全默认值 + LLM 全 mock + `SQLITE_PATH`/`LOGS_DIR` 重定向规避 /app/data 无权限）。**排障四连**：① `.gitignore` 排除了 `uv.lock` → 取消忽略入库（可复现构建）；② CI 缺中文字体 → PDF 体积断言（≥100KB）因 `.notdef` 缩水失败 → 补 `fonts-wqy-zenhei`（与生产镜像对齐）；③ `test_rag_ingest` 真调百炼 embedding，无凭据时 `do_ingest` 逐文件吞错返回 0 未触发 skip → 测试前置 `dashscope_api_key` 空判断 skip；④ 新仓库 GITHUB_TOKEN 默认只读 → workflow 声明 `issues: write`，失败自动开 Issue 上报日志尾部（远程排障用，稳定后可移除）
- **本地复现 CI 的自伤雷（已记入项目记忆）**：把 `backend/` bind mount 进临时容器跑 `uv sync` 会在**宿主机**生成 Linux venv，遮蔽 psycheflow-backend 容器内 `/app/.venv`（python 符号链接失效 → `uv run` spawn 失败）——清理：`docker run --rm -v <backend>:/src alpine rm -rf /src/.venv`
- **LLM 输出评估体系（P2）**：
  - [eval_triage.py](backend/scripts/eval_triage.py) + [triage_dataset.json](backend/scripts/eval/triage_dataset.json)：43 条标注样本（危机 8 / 咨询 22 / 倾诉 12 / 求助 1，其中 6 条带「边界」标注），云端基线 **97.7%**（42/43）；危机类 8/8=100%（硬编码词表安全回归，准确率下降即阻断发布）；当时唯一误判为边界样本（"被安排来咨询"→求助）。运行 ~18s。**2026-09-27 本地 triage-lora 复跑为 40/43（危机仍 8/8，详见 docs/验收报告_18.2_18.3.md）**
  - [eval_report.py](backend/scripts/eval_report.py)：5 场景（4 量表 + 合并）共 76 项断言（前 4 场景各 15、合并场景 16）**76/76=100%**。断言含六章节/姓名学号来自 profile/测评用时/雷达图 SVG/PDF 完整性（≥30KB）/**危机红框双向**（预期危机须有框+12355——MHT 85/97 全 1 会正确触发；预期安全须无框）/**安全场景建议无危机话术**（LLM 曾在非危机报告建议里泄漏红框文案，此断言专盯该回归）。运行 ~80s
  - 基线快照：[eval/results/](backend/scripts/eval/results/)（latest 入库，时间戳明细 gitignore）
- **README 指标表**：新增「质量与性能指标」章节（eval 数字 + 首 token 1.72s + QPS 361 + 测试/验收 + CI 徽章）
- **验证**：CI 全绿（https://github.com/Gunaie/PsycheFlow/actions）；容器内实测 RAG 集成测试 skip 行为正常

### README 开源级打磨批次 ✅（2026-09-05，commit `11ffa38`）

- **Mermaid 系统架构图**（README）：客户端三角色 → Nginx → 三态门户 → FastAPI（API/多智能体链/危机前置层/RAG/报告引擎）→ LLM 三级降级链（百炼→Ollama→硬编码话术）→ 数据层（SQLite 0600/Chroma/危机留痕）
- **8 张核心页面截图**（[docs/screenshots/](docs/screenshots/)）：门户/学生首页/量表选择/作答/对话（人设+四阶段条+吉祥物）/历史/教师批次列表/批次详情（统计卡+进度条+危机红名单含触发词+严重度分布+班级进度）
- **可复现截图脚本**（[backend/scripts/screenshots.py](backend/scripts/screenshots.py)）：PEP 723 内联依赖（playwright+httpx，不污染项目 deps）；API 造数（演示教师+5 人批次完成 4 份其中 1-2 份触发危机+演示学生+已提交 PHQ-A）→ Playwright 按三角色截 8 图到 docs/screenshots/
  - 运行前置：`$env:PLAYWRIGHT_BROWSERS_PATH='C:\Users\gunaie\.cache\ms-playwright'`（Trae 沙箱禁写默认 `%LOCALAPPDATA%\ms-playwright`）+ `PLAYWRIGHT_DOWNLOAD_HOST=https://cdn.npmmirror.com/binaries/playwright`（国内镜像，否则下载 130MB 极慢）
  - 已知小瑕疵：作答页脚本点击选项未生效（进度 0/9，截图仍可展示界面）；首页欢迎语显示账号 label 而非昵称（真实系统行为）
- **验证**：8 图逐一目检通过；README 嵌入「界面速览」表格

### 安全加固与演示材料批次 ✅（2026-09-05，commit `f1da7af` → `9d06a9b`）

- **匿名 LLM 接口 IP 限流**：[api/ratelimit.py](backend/app/api/ratelimit.py) 滑动窗口内存限流（对话端点 10 次/分、报告生成 3 次/分），key=端点组+客户端 IP（支持 X-Forwarded-For 反代场景）；已登录用户按成功解析的账号豁免（无效 token 一律视为匿名，伪造 header 绕不过）。单测 [test_ratelimit.py](backend/tests/test_ratelimit.py)（含进程级 bucket 测试基建修正）
- **匿名测评不存档提示**：ScalePage 匿名用户顶部警示「匿名测评不存档：报告仅当前页面可见，请及时下载 PDF 留存；登录后测评将进入「历史」记录」
- **演示视频录制脚本**：[docs/演示视频录制脚本.md](docs/演示视频录制脚本.md)（OBS 分镜 + 旁白词 + 录前清单）
- **验证**：pytest 199 passed / 1 skipped 基线不变；CI 全绿

### 双端隔离补强与交互导航批次 ✅（2026-09-05，commit `df68bd0`）

- **RequireStudent 守卫**（[App.tsx](frontend/src/App.tsx)）：教师登录态访问学生路由（/home /assess* /chat /history /screening）一律弹回 /admin——补上路由重构批次遗留的「教师手输学生 URL 可进入」缺口；/history 的登录守卫嵌套在 RequireStudent 内
- **RedirectIfAuthed**：已登录访问 /login /register 按角色回各自首页，防两端交叉与重复登录
- **门户身份换乘站**（[PortalPage.tsx](frontend/src/pages/PortalPage.tsx)）：自动重定向改为可视换乘——已登录显示身份条（label + 角色 + 「进入我的工作台 →」）；教师点学生端卡 confirm 后 clearToken 再进 /home（明确的换身份动作，卡片显示「切换将退出当前教师账号」）；学生点教师端卡进 /admin/login 校验角色；双卡由 Link 改 button 语义
- **统一返回导航**：新增 [BackLink](frontend/src/components/BackLink.tsx) 组件（胶囊描边 + 箭头 + hover 反馈，light/dark 双 variant），接入学生首页「返回门户」、登录/注册页「返回首页」（dark）、筛查码页「返回首页」、批次详情「返回批次列表」（dark）；批次详情「导出汇总 CSV」「关闭批次」与管理顶栏「退出」按钮统一胶囊风格；作答页「返回测评选择」带未提交作答 confirm 防误触丢答案
- **入口补齐**：学生首页新增「班级筛查作答」卡（/screening 筛查码入口，功能卡 3→4 改 2×2 网格）；量表选择页免登录文案纠偏（原文案误写「登录后即可开始测评」，实为免登录可测）
- **验证**：`tsc --noEmit` 0 错误 + `vite build` 通过；前端容器重启后浏览器目检各页返回按钮、守卫弹回与门户换乘行为

### 报告/历史/批次管理修复批次 ✅（2026-09-06，commit `5fd9f6e`）

试点反馈问题集中修复，9 文件 +299/-24：

- **报告兜底字样**：发展建议 LLM 空回复时的兜底标题不再出现内部标识「发展建议（通用兜底）」字样（[reports/service.py](backend/app/reports/service.py)）
- **历史报告时间少 8 小时**：根因 `session.created_at` 存 UTC 但 `isoformat()` 无时区标记，浏览器 `new Date()` 按本地时区解析。修复 [HistoryPage.tsx](frontend/src/pages/HistoryPage.tsx) `formatDate/formatTime` 给 ISO 字符串补 `Z` 标记明确 UTC
- **双量表历史详情打不开报告内容**：根因 `viewDetail` 直接用列表精简数据（无 interpretation/answers）。修复：详情弹窗异步调 `GET /api/sessions/{id}` 补全，新增 `detailLoading` 状态；后端 [sessions.py](backend/app/api/sessions.py) 详情响应补 `max_score`
- **测评提交即存档**：根因 assessment 写入挂在「生成 PDF 报告」按钮上，不点击则历史记录看不到。修复：[ScalePage.tsx](frontend/src/pages/ScalePage.tsx) submit 改调 `submit_assessment`（计分 + 持久化），生成 PDF 与存档解耦
- **教师端批次管理增强**（[admin.py](backend/app/api/admin.py) + 批次列表/详情两页）：新增 `PATCH` 批次重命名、`DELETE` 批次删除、`POST` 批次 reopen（重新开放作答）；列表页与详情页加入口按钮；「创建筛查批次」表单展开时不再同时显示「暂无筛查批次」空状态（条件改 `batches.length === 0 && !showCreate`）
- **测试**：[test_admin_screening.py](backend/tests/test_admin_screening.py) 新增 3 个批次端点测试（重命名/删除/reopen）

### 本地私有化 3.A 基座模型版 ✅（2026-09-06 实施并验证通过）

- **方案文档**：[docs/本地模型化方案.md](docs/本地模型化方案.md) v2.0——两套部署共用一套代码，`.env` 的 `LLM_MODE` 切换（cloud/local）
- **代码改造**（4 文件）：
  - [config.py](backend/app/core/config.py)：新增 `llm_mode`（cloud/local，默认 cloud）/`local_model`（qwen2.5:7b）/`local_embed_model`（bge-m3）；model_validator 校验 local 模式必须配 `OLLAMA_BASE_URL` 否则启动报错（快速失败不静默回退云端）
  - [llm.py](backend/app/core/llm.py)：新增 `is_local` 属性 + `_primary_for(role)` 路由；`chat()/stream()/embed()` 加 local 分支——**直走 Ollama 不触云端**（数据不出本机），Ollama 失败 chat 返回 ""/stream 上抛由节点级话术兜底（**不回退云端**）；embed local 走 bge-m3 `/v1/embeddings`（分批 8 条，按 index 排序保序）；cloud 模式兜底链逻辑不变
  - [.env.example](.env.example)：补 LLM_MODE/LOCAL_MODEL/LOCAL_EMBED_MODEL 配置块
  - [test_llm.py](backend/tests/test_llm.py)：+11 例（local 下 chat/stream/embed 直走 Ollama 且 cloud client 设哨兵断言不被调用、失败不回退云端、model_for 本地映射、embed 8 条分批、config 校验）；全量 **217 passed / 1 skipped**
- **环境变更**：Ollama 整机容器重建加 `-e OLLAMA_KEEP_ALIVE=-1`（模型常驻显存，消除 40s 冷启动；qwen2.5:7b 4.7GB + bge-m3 1.2GB = 5.9GB < 8GB 显存）；模型库仍在 `E:\OllamaModels` 卷，重建不丢
- **切换动作**：`.env` 设 `LLM_MODE=local` + `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` → `docker compose up -d backend`（restart 不重读 .env）→ **必须重建 RAG 索引**（embedding 换模型，旧 v3 向量作废）：`rag_store.reset_namespace()` 删 collection 后 `build_index()`（bge-m3 重建 183 条）
- **实测数据（RTX 4060 Laptop，2026-09-06）**：
  - triage 意图分诊 **40/43 = 93.0%**（云端 qwen3.8-27b 基线 97.7%）；**危机 8/8 = 100% 安全红线保住**；3 个误判全是求助/咨询边界样本（不改变路由，两类都进 intervention）
  - 报告合规 **76/76 = 100%**（5 场景全过，发展建议 733-874 字非空，PDF 136-170KB，单场景 ~11s）
  - 正常对话 SSE：模型热态首 token ~0.8s（RAG 0.6s）；危机路径 0.15s（硬编码前置不调 LLM）
- **已知限制**：~~ASR/TTS 语音仍走百炼（3.A 未本地化，faster-whisper/edge-tts 推后）~~ ✅ 已在 D5 阶段解决。

### 本地语音本地化 D5 ✅（2026-09-06 实施并验证通过）

- **方案**：**faster-whisper** (ASR) + **sherpa-onnx** (TTS)。
- **代码实现**：[voice_local.py](backend/app/core/voice_local.py) 封装本地引擎；[api/voice.py](backend/app/api/voice.py) 根据 `VOICE_MODE` 自动路由。
- **环境修复**：
  - 解决了 `sherpa-onnx` 与 `onnxruntime` 的动态库版本符号冲突（`VERS_1.27.1` not found），通过强制重装配套 wheel 并修正 `entrypoint.sh` 库路径解决。
  - 适配了 `sherpa-onnx` 最新版本的 API（`OfflineTtsVitsModelConfig` 参数名 `vits` → `model`）。
- **模型挂载**：宿主机 `E:\OllamaModels\voice` 挂载至容器 `/models/voice`。
  - ASR: `faster-whisper-medium` (GPU 加速)
  - TTS: `vits-zh-aishell3` (ONNX 格式)
- **验证**：运行 `docker exec psycheflow-backend uv run python test_voice_local.py` → **ASR/TTS 均加载成功**。
- **2026-09-07 收尾（全链路闭环）**：
  - TTS 接入 `rule_fsts` 数字/日期规范化（date/number/phone/heteronym.fst），热线号码「12355」不再跳读/误读；
  - [api/voice.py](backend/app/api/voice.py) `/synthesize` 的 `media_type` 跟随 `voice_mode`（local=audio/wav / cloud=audio/mpeg），修复部分浏览器 `<audio>` 拒播；
  - E2E 往返验证脚本 [scripts/voice_e2e.py](backend/scripts/voice_e2e.py)（TTS 合成含 12355 文本 → WAV 结构检查 → faster-whisper 转写回读，关键词命中即 PASS），单测覆盖 local/cloud 双模式 media_type（`tests/test_voice.py::TestVoiceApi`）。
- **3.B 云GPU微调版**：✅ 全部闭环（2026-09-06 训练导出，2026-09-07 本地启用+评测通过），详见下节「本地私有化 3.B」

### 本地私有化 3.B 云 GPU 微调 ✅（2026-09-06 云上训练+GGUF 导出；2026-09-07 本地启用+评测通过，全部闭环）

- **目标**：QLoRA 微调 qwen2.5:7b，dialog 角色换用 DeepWell-Adol 心理对话风格，report 角色强化发展建议质量；intake/triage 保持基座不动——**危机红线不受微调影响**
- **代码侧**：[config.py](backend/app/core/config.py) 增 `LOCAL_MODEL_DIALOG/LOCAL_MODEL_REPORT`（local 模式下 dialog/report 角色可挂微调模型，留空回退 `local_model` 基座）；[llm.py](backend/app/core/llm.py) `_primary_for`/`model_for` 适配；单测全量 **218 passed / 1 skipped**
- **训练数据**：
  - dialog：清华 **DeepWell-Adol**（EMNLP 2025 青少年积极心理对话数据集，云上自动 clone + [convert_deepwell.py](backend/scripts/finetune/convert_deepwell.py) 转 sharegpt 格式）
  - report：**52 条蒸馏数据**（云端 deepseek 对项目历史测评数据生成发展建议，`data/finetune/finetune_report.jsonl`，建议 474–1089 字，结构完整）
- **训练配置**：QLoRA（bitsandbytes 4bit 基座 + LoRA target all，cutoff 2048，effective batch 16，bf16，cosine），AutoDL **RTX 4090 24GB**。首训 rank16/alpha32、dialog lr 1e-4/3ep；**2026-09-08 反模板重训**（解决 LoRA 模板腔/闭合问句，详见 [docs/训练记录-2026-09-08.md](docs/训练记录-2026-09-08.md)）：dialog 语料扩至 **1386 条**（DeepWell 清洗 600 + 云端合成 500 + 改写 280，均注入 318 字 system prompt），rank 16→**32**、alpha 32→**64**、lr 1e-4→**5e-5**、epochs 3→**5**（train_loss 1.3355）；report 52 条 3ep（train_loss 2.2855）
- **导出链**：合并 LoRA → f16 GGUF（convert_hf_to_gguf）→ **llama-quantize 量化 Q4_K_M**（token_embd/output 用 q6_K 保精度；convert 脚本不支持直接出 q4_k_m）→ 重训产物 `qwen2.5-dialog-lora-q4_k_m.gguf` + `qwen2.5-report-lora-q4_k_m.gguf`（各 **4.7GB**）经 [import_gguf.ps1](backend/scripts/finetune/import_gguf.ps1) 导入整机共享 Ollama 并覆盖旧 tag（`ollama list` 可见）
- **一键脚本**：[backend/scripts/finetune/cloud_train.sh](backend/scripts/finetune/cloud_train.sh)（AutoDL 数据盘 /root/autodl-tmp/ft，断点自愈：已训 adapter/已合并目录/已产出 GGUF 均自动跳过；内含全链路导入探针）+ [import_gguf.ps1](backend/scripts/finetune/import_gguf.ps1)（本地注册进 Ollama）
- **云上环境踩坑记录**（AutoDL 复现训练时的教训，脚本均已修复自愈）：
  1. 学术加速 `network_turbo` 开启会劫持 pip 流量导致找不到包 → 脚本改为**先装依赖后开代理**
  2. pip 新装 torchaudio 绑定 CUDA13 而宿主 torch 是 CUDA12（`libcudart.so.13` 报错）→ torchaudio 必须与 torch 严格同版本
  3. huggingface_hub 新版默认 Xet 协议直连 `cas-server.xethub.hf.co` 国内 401 → `HF_HUB_DISABLE_XET=1` 走 hf-mirror；hf-mirror 大文件断流 → **ModelScope 兜底**（阿里源 ~50MB/s）
  4. 两个 pip 进程并发写同一环境会连锁损坏（此坑最隐蔽：错误表现为误导性的 `cannot import PreTrainedModel`，真凶是 numpy 1.26.4 与 scipy 1.18+ 冲突 + torch 家族被拆散配对）→ 修复链：transformers 钉回 4.57.6、scipy==1.13.1（兼容 numpy 1.26.4）、torch/torchvision/torchaudio 对齐 2.8.0 CPU（pip 版本比较忽略 `+cpu/+cu128` 后缀，必须先卸载强制重装）
  5. `convert_hf_to_gguf.py` 的 `--outtype` 不支持 q4_k_m → 只能 f16 转换 + llama-quantize 两步（llama.cpp 需现场 cmake 编译 quantize 目标）
- **本地启用与评测（2026-09-07 实测）**：import_gguf.ps1 已完成导入（`ollama list` 可见 qwen2.5:dialog-lora / report-lora 各 4.7GB），`.env` 已配置并生效。`eval_report.py` 评测（local 模式，report=qwen2.5:report-lora）：
  - 2026-09-06 全量：**76/76 = 100%**（114s）；2026-09-07 复跑：75/76 → 唯一失败是「发展建议非空」字面"建议"断言误报（narrative 775 字只是措辞未含"建议"两字），`--only scared` 复跑 15/15（796 字）确认模型质量稳定，断言已加同义表达兜底
  - 结论：**微调模型 ≥ 3.A 基线（76/76），无回归，报告链路在对话/RAG 重构后依然全绿**
- **⚠️→✅ 显存换入换出实测（2026-09-07）**：8GB 显存装不下全部模型常驻（基座 4.7 + dialog 4.36 + report 4.36 + bge-m3 1.2 ≈ 14.6GB）已实测证实——报告生成时 Ollama 自动驱逐 dialog-lora（LRU）换入 report-lora，`ollama ps` 实时可见换出换入，GPU 占用 6.0/8.0GB。**KEEP_ALIVE=-1 在内存压力下不生效**（Ollama 仍按需驱逐），无需调整配置；实测换入延迟：报告首场景含换入约 30-60s、模型常驻后 12.5s/场景。对话→报告交替使用时各自有一次性换入延迟，属预期行为非 bug

### 对话质量改进批次 ✅（2026-09-08，commit `84ca211`）
- **问题**：干预回复模板腔——连续轮次复读「谢谢你愿意…」收尾句式、「对吧？」闭合问句、无具体可操作建议；另「睡不着」类查询 RAG 召回 0（relaxation_exercises.md 在库但 0.60 距离阈值未放行）
- **prompt 硬化**（[personas.py](backend/app/agents/personas.py) / [prompts.py](backend/app/agents/prompts.py)）：SAFETY_BASELINE 新增规则 3「落一个具体做法」（含知识库无片段时的通用兜底）/ 规则 4「开放式收尾」（禁"对吧/是不是/好吗"，整轮最多一个问题）/ 规则 5「不重样」（禁复读上轮建议与问句）；INTERVENTION_USER_TEMPLATE 同步加回复骨架与防重复指令；底线从 7 条扩为 9 条（assessment.py 注释同步改"第 9 条"）
- **温度 0.35 → 0.6**（config `temp_dialog` + intervention.py 两调用点）：实测 0.35 下本地 dialog-lora 模板惯性复读上轮问句，0.6 显著减少重复且未观察到连贯性劣化
- **新增知识卡**：`data/knowledge/05_睡眠卫生.txt`（固定作息/床只睡觉/屏幕蓝光/担忧记下法/运动与咖啡因/腹式呼吸/就医提示 8 段），`POST /api/rag/build` 重建后 126 片，冒烟第 3 轮 RAG 命中
- **验证**：容器内 290 passed / 1 skipped 全绿；新增 [dialog_smoke.py](backend/scripts/dialog_smoke.py) 多轮冒烟工具（复用生产 prompt 拼装含 RAG + 逐轮累积 history，`DIALOG_SMOKE_TEMP` 可调温）三轮实测零复读零闭合问句
- **已知边界**：本地 dialog-lora（7B Q4）对 prompt 结构规则的遵循是随机的，「落具体做法」时有时无——模板腔根因是 3.B 微调语料风格，纯 prompt 已到天花板；云端模式（指令遵循更强）直接受益。根治方向见 §8 第 8 条

### 18.1 RAG 知识库扩充 + 检索护栏批次 ✅（2026-09-09 实施，2026-09-11 入库；路线图第二批）

按 [docs/数据与模型详解.md](docs/数据与模型详解.md) 第十八章 18.1 方案执行，知识库 19→**32 文件**、索引 201→**327 片**：

- **9 个缺口主题科普 txt**（`data/knowledge/11_网络与游戏成瘾.txt` ~ `19_时间管理.txt`，内部编写科普层，格式同 07_抑郁自助：800–1500 字 + `来源：`/`适用场景：` 元数据行 + 受控词表标签）：网瘾、非自杀性自伤（NSSI，含危机求助段落但标签不含「危机」保证日常自助可检索）、创伤/PTSD 调适（grounding 5-4-3-2-1、4 吸 6 呼）、ADHD/注意力、进食障碍、哀伤辅导、青春期情感、考试焦虑（暴露练习/主动回忆/考场技术）、时间管理（番茄钟/四象限/两分钟法则）
- **4 个具名权威来源 md 摘要层**（格式 `## 来源《...》（公开摘要）` + `适用场景：`）：[cbti_manual.md](data/knowledge/cbti_manual.md)（AASM/Edinger-Carney CBT-I：睡眠限制+刺激控制+认知重构）、[nvc_parent_scripts.md](data/knowledge/nvc_parent_scripts.md)（Gordon P.E.T. 积极倾听/我信息/第三法 + Rosenberg NVC 四步）、[school_crisis_referral.md](data/knowledge/school_crisis_referral.md)（校园四级转介链 + QPR 三步法 + 12355 + 保密例外）、[mood_disorder_guidelines.md](data/knowledge/mood_disorder_guidelines.md)（抑郁防治指南二版 + 精神障碍诊疗规范 2020：ICD-11 分级、惊恐/广泛焦虑/社交焦虑/强迫，强迫条目补口语化表现）
- **既有文件补强**：[dbt_skills.md](data/knowledge/dbt_skills.md) 标签「危机」→「焦虑」（DBT 是通用情绪调节库，危机标签导致"发火怎么办"类日常 query 被危机过滤误杀）+ TIPP 章节补愤怒场景口语段；[cbt_techniques.md](data/knowledge/cbt_techniques.md) 认知重构工作表示例后补"被老师当众批评反刍"场景段
- **检索层 3 个真实 bug 修复**（[rag/service.py](backend/app/rag/service.py)）：① **阈值按嵌入模型自适应**——bge-m3 向量已归一化（范数 1.0），相关片段 L2 实测 0.77–0.91、无关 ≥1.10，云端校准的 0.75 阈值对本地模型过严导致几乎全过滤；新增 `VEC_THRESHOLD_CLOUD=0.75`/`VEC_THRESHOLD_LOCAL=0.95`/`BM25_VIRTUAL_GAP=0.03` 常量 + `_default_threshold()`（`getattr(self.llm,"is_local",False)` 判本地，兼容 mock），`search(threshold=None)` 默认走自适应；② **BM25 补充召回 tags 透传**——`_init_bm25()` 的 corpus_docs 每项补 tags、BM25 独有命中 candidate 用 `doc.get("tags")`，修复非危机 query 经 BM25 路径漏入「危机」片段（新增单测 `test_bm25_unique_hit_carries_tags_for_crisis_filter`）；③ BM25 虚拟距离改为相对值 `threshold−0.03`，删除 BM25 独有命中的关键词加权（-0.05 会使其反超真实向量命中）；危机 query 判定改用 `app.core.safety.CRISIS_KEYWORDS`（单一事实源，替代节点内硬编码 7 词子集）
- **检索评测护栏**（[eval_rag.py](backend/scripts/eval_rag.py) + [rag_eval_dataset.json](backend/scripts/eval/rag_eval_dataset.json)）：65 条 query→expect 文件（expect 可为数组，覆盖全部 32 文件），跑生产 `rag_service.search(top_k=3)` 统计 hit@1/recall@3/MRR/文件覆盖，结果写 `scripts/eval/results/rag_eval_<ts>.json` + `rag_eval_latest.json`。基线：**recall@3=100%（65/65）、hit@1=76.9%、MRR=0.874、32/32 文件覆盖**（唯一边界样本"惊恐发作"top3 为创伤呼吸着陆/DBT TIPP/放松技术——均为惊恐现场有效缓解，expect 已放宽为多可接受文件）
- **intervention 质检重试回退 bug 修复**（[intervention.py](backend/app/agents/nodes/intervention.py)，流式 `stream_intervention` 与非流式 `intervention_node` 同源）：原逻辑重试回复无条件覆盖 `reply`/tokens——重试 LLM 异常或重试回复仍不合格时首次回复丢失（与日志 "keep first reply"、docstring"重试 1 次"及测试契约矛盾，该 3 个测试在改动前基线上即失败）。修复为：保存首次回复，质检不合格时附 RETRY_HINT 降温 0.35 **重试 1 次**，重试回复**重新质检**——合格才采用，异常/空/仍不合格一律保留首次；`decision["llm"]["quality_retry"]` 置 `True`（原为 attempt+1 整数）。[dialog_smoke.py](backend/scripts/dialog_smoke.py) 内同口径副本同步修正（原为 range(2) 无条件覆盖）。测试 fixture 一处"哪部分吗？"改为"哪部分？"（"吗？"收尾被闭合问句护栏正确拦截，开放式问法不应带"吗"）
- **验证**：全量 pytest **333 passed / 1 skipped**（含 RAG 检索+埋点 + intervention_quality 重试全绿）；eval_rag recall@3 **65/65**；dialog_smoke 两轮 5 场景全绿（RAG 每轮 3 片、零闭合问句、方法具体：4-6 呼吸/担忧书写）；`POST /api/rag/build` 重建索引 327 片。**安全不依赖 RAG**：危机检测仍为硬编码前置层，RAG 片段仅作干预参考

### RAG 检索埋点 ✅（2026-09-10，18.1.7 第 1 项，零命中/弱命中聚类制度化）

- [rag/service.py](backend/app/rag/service.py) `_write_search_trace()`：每次 search best-effort 追加一行 JSON 到 `logs/rag_search_YYYYMMDD.jsonl`（UTC 日轮转，失败仅 warning 绝不阻断检索）；`search()` 新增 `intent`/`caller` 两个 kwargs，调用方已透传：intervention.py（真实对话）、case.py（案例上传，`intent="案例"`）、api/rag.py（调试端点 `caller="api"`）、eval_rag.py（`caller="eval"`，聚类时排除）
- schema 固定 17 字段：ts/caller/intent/is_crisis/threshold/embed_mode/vec_hits/bm25_unique/top1_distance（被过滤也记）/top1_passed/result_count/drop_threshold/drop_crisis_tag/drop_dedup/results(source+tags+distance)/query（截 200 字）；**禁止记 session/账号/IP/token**（TestSearchTrace 三测守护：schema/隐私字段/零命中/写失败不阻断）
- 待办：周度聚类脚本 `scripts/analyze_rag_gaps.py`（同主题簇单周 ≥5 次弱命中立项扩库，只统计 caller∈{intervention,case}）；详见 [docs/数据与模型详解.md](docs/数据与模型详解.md) 18.1.7

### 文档同步与批次收尾 ✅（2026-09-11 入库）

- **triage 决策 trace 去硬编码**：[triage.py](backend/app/agents/nodes/triage.py) LLM 分类寒暄快速通道的 `decisions["triage"]["model"]` 原硬编码 `"qwen2.5:0.5b"`（已弃用模型），改为 `provider.model_for("triage")` 动态取当前路由模型，trace 与实际调用一致
- **教师批量筛查演示名单**：[data/demo/screening_roster_demo.csv](data/demo/screening_roster_demo.csv) 30 名学生（初二 2 班，学号唯一，UTF-8 BOM 兼容 Excel 直接打开），供教师后台 CSV 建批次导入测试；已用生产 `_parse_roster` 验证解析通过
- **核心文档入库**：[docs/数据与模型详解.md](docs/数据与模型详解.md)（RAG 知识库/检索策略/模型微调/第十八章优化路线图全档，本文档 §7/§8 多处引用）、[docs/答辩全准备手册.md](docs/答辩全准备手册.md)、[docs/答辩速查手册.md](docs/答辩速查手册.md) 首次纳入版本库；README 相关文档区补「数据与模型详解」「本地模型化方案」链接
- **配置模板/注释同步**：[.env.example](.env.example) 增 `VOICE_MODE` 独立开关块、`LOCAL_MODEL_TRIAGE` 补 0.5b 弃用说明（48.8% → 7b 基座 41/43）；[config.py](backend/app/core/config.py) / [llm.py](backend/app/core/llm.py) 头部 D5 前「语音仍需云端」旧注释更正为云/本地双模式现状
- **仓库清理**：删除根目录旧账号临时草稿 `问题,txt`（其中试点反馈——学生端菜单泄漏/对话返回文件引用/量表标题重复/一量表一报告等——均已在历史批次修复）；[eval/results/.gitignore](backend/scripts/eval/results/.gitignore) 补 `rag_eval_2*.json`，带时间戳明细不入库、只保留 `rag_eval_latest.json`（与 triage/report 口径一致）
- **验证**：全量 pytest 333 passed / 1 skipped；eval_rag recall@3=100%（65/65）

### 多轮对话质量评测 + 三级质检重试阶梯 ✅（2026-10-01，commit `a85145a`；路线图五批之外的质量加固）

- **背景**：既有盲评/dialog_smoke 均为单轮或固定脚本，多轮复读（照抄自己历史回复）、话题延续、求做法后追问无生产链路护栏。
- **新增评测** [eval_multiturn.py](backend/scripts/eval/eval_multiturn.py)：4 场景 × 4 轮真实 LLM 对话（倾诉多轮延续/情绪跟进/话题切换/求做法后追问），逐轮累积 history 调生产 `/api/chat`；逐轮用生产 `check_reply_quality` 判定（import 而非复制，口径不漂移），场景级检查做法类别并集 ≥3，安全断言非危机不触发 crisis 标志；产物 `results/multiturn_eval_latest.json`（latest 入库，`multiturn_eval_2*.json` gitignore）。
- **质检重试升级三级阶梯**（[intervention.py](backend/app/agents/nodes/intervention.py)，非流式 `intervention_node` 与流式 `stream_intervention` 同源）：① **hint**：build_retry_hint 按本条回复实际违规点动态生成禁令（`_find_verbatim_overlap` 点名复读原句截 60 字 + 上轮做法类别中文标签），temp 0.6；② **rag_refresh**：`_refresh_rag_sources` 检索 top_k=6 排除首轮已引用 chunk 取 3 条重拼 prompt，temp 0.7；③ **context_trim**：history 剔除 assistant 回复（保留 user 保连贯）+ 换片重拼，temp 0.7。alt_chunks 在 ②③ 间共享（rag.search 最多 2 次）；质检始终按真实 history 校验，全部失败回退首次回复；`decision.llm.retry_modes` 记录模式列表。
- **评测驱动的三个根因修复**：
  1. **RETRY_HINT 自身是复读种子**：提示里的具体做法示例「把担心的事写在纸上」被 7B 照抄并补完库存句（「写完就合上本子…」），三次重试永远撞同一句——与「不引用违禁词原文」同原则，示例改为抽象类别表述；
  2. **呼吸参数公式误判复读**：「吸气四秒、呼气六秒」是项目硬约束统一口径，LoRA 表达呼吸法只有这一种句式，跨轮提呼吸必然 ≥12 字重叠——`_strip_breath_formula` 检测前剥离公式（哨兵「·」占位防拼接虚假重叠），公式之外的真实复读仍判不合格；
  3. **QC 分类学缺口**：prompt 骨架自列「任务拆解」示例做法但 `_METHOD_CATEGORIES` 不认识，模型照骨架给建议被误判「做法类别<2」——新增 planning 类（拆成/清单/列出来/计划表）；muscle_relax 补「握紧」关键词。
- **12355 定调**：非危机回复（如轻微倾诉）出现热线是 SAFETY_BASELINE 第 7 条「有危机倾向立即建议 12355」的「宁过度不遗漏」模型行为，不用 prompt 压制（避免削弱 LLM 层危机安全网），评测只记 warn 人工复核。
- **SSE 侧**：[api/chat.py](backend/app/api/chat.py) 流式端点改为预构建 messages/sources 传入 `stream_intervention`，避免节点内重复检索。
- **结果**：多轮评测 11/16 → **15/16（94%）**，剩余 1 处为 LoRA 库存句（journaling「把担心的事写在纸上」）权重级复读，属路线图第四批重训解决范围；全量 pytest **380 passed / 1 skipped**（新增 5 测试：呼吸公式豁免、公式外复读仍判、planning 检测、planning vs 呼吸不冲突、阶梯 context_trim 模式）。

---

## 8. 待做事项（五期优化）

按优先级排序：

0. ~~**【主线】本地私有化部署 3.A**~~ ✅（2026-09-06 完成，详见 §7「本地私有化 3.A」）：`LLM_MODE=local` 双模式改造落地，qwen2.5:7b + bge-m3 全本地，triage 93.0%（危机 100%）/ 报告 76/76=100%。后续可选项：
   - 3.B 云 GPU 微调版 ✅ 全部闭环（2026-09-06 训练导出，2026-09-07 本地启用+评测通过：76/76 基线无回归，显存换入换出已实测并回写 §7，KEEP_ALIVE 无需调整）
   - ~~ASR/TTS 语音本地化~~ ✅（D5 已闭环，详见 §7「本地语音本地化 D5」：faster-whisper + sherpa-onnx，`VOICE_MODE=local` 下 ASR/TTS 全离线，热线 12355 数字朗读经 rule_fsts 规范化，E2E 往返验证通过）
   - 切回云端：`.env` 改 `LLM_MODE=cloud` → `docker compose up -d backend` → **必须重建 RAG 索引**（embedding 换回 v3，旧 bge-m3 向量作废，同样先 reset_namespace 再 build_index）
   另：真实校园试点部署待用户决策。

1. ~~**性能压测**~~ ✅（2026-09-01 跑通，commit 待补）：脚本 [backend/scripts/perf_bench.py](backend/scripts/perf_bench.py)，命令 `docker exec psycheflow-backend uv run python scripts/perf_bench.py`。验收数据：
   - `/api/health` x50 并发：50/50 (100%)，总耗时 138ms，平均 123.6ms，P50 123.4ms，P95 127.9ms，QPS 361.2 ✅ 满足 NFR「接口 < 200ms」
   - `/api/chat` x10 并发：10/10 (100%)，全 `agent=intervention`/`crisis=False`，端到端整轮平均 20.9s，P50 20.2s，总耗时 26.8s。注：此处测的是 LangGraph 三节点串行（triage→assessment→intervention）+ deepseek-v4 reasoning_content 思考链的**完整响应时延**，非首 token；NFR-5「首 token < 2s」需流式接口（SSE/streaming）改造后单独测量，当前端到端 ~21s 属已知架构特征非 bug
2. **合规加固深化** ✅（2026-09-01 ~ 09-02，字段级加密按评估结论暂缓，访问层 (b)(c) 于 09-02 完成）：
   - ✅ **审计日志双写（文件 + DB）**：新增 `AuditLog` 表（[models.py](backend/app/models.py)），[audit.py](backend/app/core/audit.py) 的 `write_crisis_audit`/`write_report_audit` 落 JSON 文件后镜像写 DB 行（`_db_write_audit`，best-effort 失败仅 warning 不阻断主业务）。单测 `test_crisis_dual_writes_db` + `test_db_write_failure_does_not_block_endpoint` 验证双写一致性 + 不阻断
   - ✅ **授权链复检**：修复 [auth.py](backend/app/api/auth.py) `login_by_label` 教师绕密漏洞——教师账号 password_hash 非空却可凭 label 直接拿 token，绕过密码。现已一律 403（`teacher_requires_password`），必须走 `/login_by_password`。单测 `TestTeacherAuthHardening` 3 例覆盖（教师 label 拒、密码通、学生不受影响）
   - ⏳ **未成年人数据加密评估**（结论：暂不实施字段级加密，当前最优是传输层+访问层加固）：
     - 敏感数据盘点：`User.profile`(name/student_no/grade/klass/gender/age/guardian_phone/school/teacher_email)、`BatchEntry`(student_no/student_name)、`ConversationTurn.content`、`AssessmentRecord.answers/interpretation`
     - 现状保护：传输层 dev HTTP，prod nginx **已上 TLS1.2/1.3 + HSTS + CSP + 全套安全头**（[nginx.conf](frontend/nginx.conf)）；静态层 SQLite `./data/psycheflow.db` Docker volume 无加密；访问层 token=token_hex(32) 分离 + 教师 PBKDF2-SHA256 加盐 + login_by_label 漏洞已修 + 审计 DB 双写
     - 结论：SQLite MVP 实施字段级加密（guardian_phone 走 Fernet/AES）需密钥管理（env 弱密钥 / KMS 过度工程）且破坏 SQL 查询，性价比低。**三步状态**：(a) ✅ 生产 nginx TLS+HSTS+CSP 已完成（2026-09-01，自签证书已生成验证，nginx -t + compose config 双绿）、(b) ✅ `./data` volume 容器内非 root + 文件 600 权限（2026-09-02，[Dockerfile](backend/Dockerfile) 建 appuser uid 1000 + [docker-compose.prod.yml](docker-compose.prod.yml) `user:"1000:1000"`，dev 仍 root 仅测试数据；[db.py](backend/app/db.py) `restrict_db_file_perms` 启动时收紧 0600，实测 db 文件已变 `-rw-------`，单测 `TestDbFilePerms` 覆盖）、(c) ✅ SQLite 备份文件加密（2026-09-02，[backup_db.py](backend/scripts/backup_db.py) `sqlite3.backup` 一致性拷贝 + `openssl enc -aes-256-cbc -pbkdf2 -iter 100000` 加密，口令从 `BACKUP_PASSPHRASE` 注入空则拒备份；实测产出 .db.enc 且 round-trip 解密回 header=`SQLite format 3` size 一致）；规模化迁 PostgreSQL 后对 guardian_phone/teacher_email 走 pgcrypto 列级加密
3. ~~**流式接口（首 token 验收前置）**~~ ✅（2026-09-01 SSE 骨架 + 首 token 优化完成，**NFR-5 达标**）：
   - **SSE 骨架**（commit 70d2917）：新增 [POST /api/chat/stream](backend/app/api/chat.py)（保留旧 `/api/chat` 向后兼容）。架构 Option C：手动跑 triage→assessment（同步等结果），再用 `provider.stream()` 边生成边推 token；危机路径不流式，推完整 crisis_message 后 close。[llm.py](backend/app/core/llm.py) 加 `stream()`（只 yield `delta.content`，过滤 `reasoning_content` 思考链）。[intervention.py](backend/app/agents/nodes/intervention.py) 重构出 `build_intervention_messages`+`stream_intervention`+`FALLBACK_REPLY`，流式与非流式复用同一套 prompt 拼接。SSE 事件：`agent`(节点切换)/`sources`(RAG 卡片提前推)/`token`(流式)/`crisis`(完整话术)/`error`/`done`
   - **前端**：[api.ts](frontend/src/api.ts) 加 `streamChat`（fetch+ReadableStream 解析 SSE，因 EventSource 不支持 POST+auth）；[ChatPage.tsx](frontend/src/pages/ChatPage.tsx) `send` 改用 `streamChat`，边收 token 边追加到 assistant 气泡，空 assistant turn 在 loading 时显示"思考中"
   - **首 token 优化**（commit fe1a595，NFR-5 达标）：首 token **18.08s → 1.72s**
     - 瓶颈：triage（intake=qwen3.8）+ intervention（dialog=deepseek-v4-pro）两节点都有 reasoning_content 思考链，stream 模式下先输出思考链 5-15s 再输出 content
     - 误判排查：qwen3.8 不支持 `enable_thinking=False`（百炼报 400 restricted to True）；4.80s 假象是 stream() 抛异常走 FALLBACK_REPLY
     - 解决：triage + dialog_stream 两角色都换 **qwen-plus**（无思考链，首 content ~0.5s）。详见 §4 P12 + §7「SSE 流式 + 首 token 优化」段
   - **测试**：187 passed / 1 skipped（+6 流式单测 [test_api_chat_stream.py](backend/tests/test_api_chat_stream.py)：正常 token 序列/危机不流式/空回复 fallback/异常 fallback/未知人格回退/RAG 空不推 sources）
   - ~~**非阻塞后续项**：triage 从 qwen3.8 换 qwen-plus 后，准确率需重测（之前 9/9）。sse 实测 1/1 正确，但样本少，建议跑一轮多消息采样确认不退化~~ ✅ 已验证（2026-09-02）：换 `qwen3.8-27b`（关思考链）后 triage 抽样 9/9 全对（求助/倾诉/咨询各 3），无退化
4. **Ollama 本地兜底** ✅（2026-09-02，断网/降本灾备方案已落地）：
   - **兜底链**：百炼 cloud → Ollama 本地（`ollama_base_url` 非空时启用）→ 节点级硬编码话术。Ollama 仅在 cloud 异常**或**空回复（quota/思考链耗尽）时介入；未配置（`base_url` 空）则保持原 cloud-only 行为，零行为变更。
   - **实现**：[llm.py](backend/app/core/llm.py) 抽出 `_chat_once`/`_stream_once` helper（单次调用不重试不兜底）。`chat()` 捕获 cloud 异常 → 若启用 Ollama 则转本地，cloud 正常返回则不碰 Ollama；双失败返回 `""`（节点级话术兜底）。`stream()` 仅在**未 yield 任何 token**（起始即失败）时切 Ollama，已部分输出则不切（避免拼接错乱）并上抛由 SSE error 事件处理。Ollama 走 OpenAI 兼容端点 `/v1`（`AsyncOpenAI` 复用），`api_key` 填占位非空值（Ollama 不鉴权）。
   - **配置**：[config.py](backend/app/core/config.py) 新增 `ollama_base_url`（空=禁用）/`ollama_model`（默认 `qwen2.5:7b`）；.env.example/.env 加 `OLLAMA_BASE_URL`/`OLLAMA_MODEL`。**架构：整机共享独立容器**（不挂任何项目 compose，多项目共用一份模型库）：`docker run -d --name ollama --gpus all -p 11434:11434 -v E:/OllamaModels:/root/.ollama --restart always ollama/ollama:latest`；图形界面 Open WebUI：`docker run -d -p 3001:8080 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 -v open-webui:/app/backend/data --name open-webui --restart always ghcr.io/open-webui/open-webui:main`（浏览器 `http://localhost:3001` 注册本地账号即可可视化管理/聊天）。各项目后端容器经 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` 访问。
   - **测试**：[test_llm.py](backend/tests/test_llm.py) +10 例（`TestChatOllamaFallback` 6 + `TestStreamOllamaFallback` 4），全 mock 不依赖真实 Ollama：cloud 异常/空回复→ollama、未启用原样上抛、cloud 正常不碰 ollama、双失败返回空、stream 起始即失败切流、已输出中途断流不切、cloud 正常不碰 ollama、未启用原样上抛。全量 **199 passed / 1 skipped**（+10）。
   - **真实联调** ✅（整机共享 Ollama + GPU 直通 + Open WebUI，2026-09-02）：沙箱拦截原生安装器（exit 4，`%LOCALAPPDATA%\Programs\Ollama` 受限），故 Ollama 走 Docker 路线；又为多项目共享 + 模型不占 C 盘，升级为**整机共享独立容器**（从项目 compose 解耦）。模型库迁至 `E:\OllamaModels`（沙箱拦 PowerShell 写，用 `docker run --rm -v ... alpine cp -a` 中转拷贝，原项目 `./data/ollama` 8.7GB 已清）。共享容器 `ollama`（`--gpus all` + `-v E:/OllamaModels:/root/.ollama` + `--restart always`）开机随 Docker Desktop 自启；图形界面 `open-webui`（端口 3001，healthy，`OLLAMA_BASE_URL=http://host.docker.internal:11434`）。验证：`nvidia-smi -L` 见 RTX 4060 Laptop、`ollama list` 见 `qwen2.5:7b`（Q4_K_M，ctx 32768）、`http://localhost:3001` 注册账号后可视化管理/聊天。`.env` 设 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`，重启 backend 后 E2E 兜底：临时把 intake 模型名换成 `__nonexistent_model_xyz__` 逼 cloud 404 → `provider.chat` 自动转 ollama 返回非空中文（"你好，我叫Qwen，是来自阿里云的大规模语言模型…"，`FALLBACK_OK`）。以后新项目只需 `.env` 设同一 `OLLAMA_BASE_URL` 即可复用，无需再下/导入模型。
5. **多 Provider 切换**：硅基流动等备用 Provider（推后：待注册硅基流动账号；现有 Ollama 兜底已覆盖云端不可用场景）
6. **多租户支持**：按学校/区域隔离数据
7. **生产验收 / 打包交付** ✅（2026-09-02）：
   - **端到端 E2E 脚本** [scripts/e2e_acceptance.py](backend/scripts/e2e_acceptance.py)：驱动 `健康→登录(login_by_password)→建会话→正常对话(SSE token 流)→危机拦截(crisis 事件+12355+crisis_*.json 落盘)→报告(MHT 6 章+发展建议非空)→审计落库(AuditLog)` 七步，幂等可重跑（每次新 session_id，复用注册的 e2e-runner 教师）。实跑 **7/7 PASS**（`docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/e2e_acceptance.py`）。
   - **prod compose 复检** ✅：[docker-compose.prod.yml](docker-compose.prod.yml) backend `user:1000:1000`+4 worker+curl healthcheck（Dockerfile 已装 curl）+restart always；frontend nginx TLS(443)+HSTS+CSP+wget healthcheck。Ollama 不在 prod compose（整机共享独立容器，`.env` 配 `host.docker.internal`）。
   - **部署文档** [DEPLOY.md](DEPLOY.md)：新机器拉起全步骤（前置/`.env`必填项/开发模式/生产模式/TLS 证书/Ollama 可选/部署后验收/日常运维/常见问题）。
   - **`.env.example` Ollama 注释更新**：移除已删的 compose ollama 服务说明，改为整机共享独立容器启动命令 + Open WebUI。
8. **对话 LoRA 反模板重训 + 统一模型 + RAG 扩充（未来项，方案已定版未动手）**：dialog-lora 微调语料的共情模板（共情句 + 固定问句收尾）惯性会压过 prompt 指令——2026-09-08 实测强化 prompt 后「禁闭合问句/禁复读上轮」仍被部分无视（0.6 温度下缓解）。**完整路线图见 [docs/数据与模型详解.md](docs/数据与模型详解.md) 第十八章「后续优化路线图（2026-09-09 答辩研讨定版）」**，五批执行顺序：
   - ~~第一批 quick win（不依赖训练）~~ ✅（2026-09-26 完成）：bge-m3 挪 CPU（`ollama create bge-m3-cpu`，Modelfile `num_gpu 0`，向量等价索引不重建）+ `OLLAMA_KEEP_ALIVE=-1` 单模型常驻（实测 100% CPU + Forever），消除大部分冷切换；~~顺手更正 config.py / llm.py 头部「语音仍需云端」的 D5 前旧注释~~ ✅（2026-09-10 已更正：llm.py/config.py/.env.example 注释同步 VOICE_MODE 现状，0.5b 分诊弃用说明已补；2026-09-26 补修 .env 分诊旧注释）。**落地时意外发现 Chroma 索引丢失**（只剩 109 片旧式切片），eval_rag 护栏当场拦截（recall@3 100%→49.2%），重建 327 片后恢复 100%——教训：清 data 目录/切环境后必须重跑 eval_rag；18.1.7 周度聚类脚本 `scripts/analyze_rag_gaps.py` 同日落地；
   - ~~第二批 RAG 扩充~~ ✅（2026-09-09 完成，详见 §7「18.1 RAG 知识库扩充 + 检索护栏批次」）：知识库 19→32 文件 / 201→327 片（9 缺口主题科普 + 4 权威来源摘要：CBT-I/NVC 亲子/校园危机转介/心境障碍指南）；65 条检索评测集护栏 recall@3=100%、32/32 文件覆盖；顺带修复阈值本地自适应（bge-m3 0.95）、BM25 tags 透传、intervention 重试回退 3 个真实 bug。**后续扩充仍按此流水线**：新文件入库 → `POST /api/rag/build` → 跑 eval_rag.py，recall@3 下降即阻断；
   - 第三批 数据备料：dialog 1386→4000–6000（强 teacher + Best-of-4 + LLM 判官，评分标准与 dialog_smoke 同源）、report 52→200–300（量表×严重度矩阵全覆盖 + eval_report 断言过滤）、triage 0→800–1000（43 条人工集只做 test；硬负例与 detect_greeting/detect_method_question 规则对齐）；merge_datasets.py 改支持三套 system prompt；
   - 第四批 云端重训（训练成本不计的效果上限方案）：基座升级 **Qwen3-8B-Instruct**（保底 Qwen2.5-7B Q6_K），**全参 SFT（A100 80GB，lr 1e-5/3epoch）+ DPO（~2000 对，lr 5e-6；rejected 池=280 条模板腔改写数据）**，导出 **Q5_K_M GGUF（~5.9GB）**；Qwen3 必须训练/Ollama 双侧全程关思考链（/no_think）；
   - 第五批 本地部署验收：单模型统一路由（LOCAL_MODEL_DIALOG/REPORT/TRIAGE 留空回退）+ `OLLAMA_KV_CACHE_TYPE=q8_0`（KV 1.2→0.6GB）+ ASR 固定 CPU int8 让显存，总计 ~7GB 常驻零切换；验收闸门 eval_triage ≥41/43（危机 8/8）、eval_report 76/76、dialog_smoke 全过、无 `<think>` 残留、ollama ps 单模型 ~7GB、30 场景云本地盲评 ≥90%。**（实际执行：三模型独立路由，验收结果见 docs/验收报告_18.2_18.3.md）**
   - 过渡方案（已落地）：prompt 骨架规则 + 0.6 温度 + 睡眠卫生知识卡，用 `dialog_smoke.py` 回归验证

---

## 9. Git 提交历史

```
5fd9f6e fix: 报告字样/历史时间/详情显示 + 批次管理增强 + 测评提交即存档 — 兜底标题去内部标识 + UTC 补 Z 修少 8 小时 + 历史详情异步拉详情接口 + PATCH/DELETE/reopen 批次端点 + submit 即持久化 assessment + 3 批次测试
a743100 docs(handover): 同步安全加固与双端隔离补强批次 — commit 指针 df68bd0 + 补录 f1da7af/9d06a9b 批次记录 + 前端模块索引更新（RequireStudent/BackLink/门户换乘站）
df68bd0 feat: 双端隔离补强 + 门户身份换乘站 + 统一返回导航与按钮美化 — RequireStudent/RedirectIfAuthed 守卫 + 门户身份条与切端确认 + BackLink 统一接入 + 筛查码入口卡 + 作答返回确认 + 免登录文案纠偏
9d06a9b docs: 演示视频录制脚本（OBS 分镜 + 旁白词 + 录前清单）
f1da7af feat(security): 匿名 LLM 接口 IP 限流 + 匿名测评不存档提示
11ffa38 feat: README 开源级打磨（P3）— Mermaid 系统架构图 + 8 张核心页面截图 + 可复现截图脚本
9c49626 feat: LLM 输出评估体系（P2）— triage 评测 43 样本 97.7%（危机 8/8）+ 报告合规评测 76/76 100%，基线快照入库 + README 指标表
d7d9418 fix(test): RAG 集成测试在无 DASHSCOPE_API_KEY 环境前置 skip — do_ingest 逐文件吞错导致 CI 误报断言失败
83da14a ci: workflow 声明 issues:write 权限 — 失败排障 Issue 才能创建
6649bc0 ci: pytest 失败时自动开 Issue 上报日志尾部（远程排障用，稳定后移除）
0ee95e4 fix(ci): 补装 fonts-wqy-zenhei 中文字体 — 与生产镜像对齐，修复 PDF 体积断言因缺字 .notdef 缩水
d5a91e7 fix(ci): 提交 uv.lock 并取消 gitignore — --frozen 同步需要锁文件入库（可复现构建）
33aa441 fix(ci): 钉住 uv 0.11.5 — 新版 uv 判定旧锁格式过期导致 --frozen 同步失败
7f65806 ci: GitHub Actions — backend pytest + frontend typecheck/build + main 分支镜像构建验证
2cf193c feat: 前端视觉丰富 — 门户横幅+双端卡配图+对话空状态插画（缺图自动降级）+ public 测试残留迁移 tests/report-samples
5a12ac4 feat: 路由重构与双端门户 — /assess 前缀统一 + 旧路径重定向 + AdminShell 统一布局 + 路由守卫 + 三态门户 /（未登录选身份/按角色自动跳转）+ 双端零交叉入口
884e7fa docs: 试点合规与操作材料 — 监护人知情同意书模板 + 教师操作手册（建批次/看报告/危机处置 SOP）
15ca1d4 docs: 同步配置与文档至实际代码 — README 重写 + 开发计划/HANDOVER 更新
b43994e feat: 测评纠偏与前端体验批次 — 一量表一报告 + 报告增强 + PDF 下载交互分化
c529bb9 feat: 生产验收/打包交付 — E2E 7/7 验收脚本 + prod compose 复检 + DEPLOY.md 部署文档
dd30704 feat: Ollama 升级为整机共享独立容器 — 模型库迁 E:\OllamaModels + Open WebUI 图形界面（多项目复用）
90afc1b feat: Ollama 真实联调 — Docker ollama 服务 + RTX 4060 GPU 直通，qwen2.5:7b 导入，E2E 兜底验证通过
29999db feat: Ollama 本地兜底 — cloud 异常/空回复时回退本地 LLM（llm.py _chat_once/_stream_once + 10 单测）
fd86fae feat: 合规加固深化 (b)(c) — 容器非 root + SQLite 0600 + 备份 AES 加密
09c271e fix: 替换无额度的 qwen-plus — triage=qwen3.8-27b + dialog_stream=qwen3.8-max 关思考链，NFR-5 仍达标(1.75s)
fe1a595 feat: SSE 首 token 优化 18s→1.7s（NFR-5 达标）— triage+dialog_stream 换 qwen-plus 无思考链
70d2917 feat: SSE 流式对话 — POST /api/chat/stream 边生成边推 token
d2e3b50 feat: 生产传输层加固 — nginx TLS + HSTS + CSP + 安全头
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
- [ ] 重建 RAG 索引：`docker exec psycheflow-backend uv run python -c "import asyncio; from app.rag.service import rag_service; print(asyncio.run(rag_service.build_index()))"` 输出 `{'indexed': 327, ...}`（32 个知识库文件；或 `curl.exe -s -X POST http://localhost:8000/api/rag/build`）
- [ ] 跑测试：`docker exec psycheflow-backend uv run pytest -q --no-header` → 380 passed / 1 skipped / 0 failed（2026-10-01 复测值；基线首测 09-10，若实测不同以实测为准并回写文档）
- [ ] **RAG 检索护栏**：`docker exec psycheflow-backend uv run python scripts/eval_rag.py` → recall@3 100%（65/65）、文件覆盖 32/32（知识库每次扩充后必跑）
- [ ] **多轮对话质量护栏**：`docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/eval/eval_multiturn.py` → 轮次通过率 ≥15/16（基线 94%，2026-10-01；真实 LLM 有 ±1 轮温度波动，场景级 4 过 3 以上可接受）
- [ ] 遗留项验证：`docker exec psycheflow-backend uv run python scripts/verify_leftovers.py` → has_assessment PASS + triage 9/9
- [ ] **SSE 首 token 验证（NFR-5）**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py` → 首 token < 2s（实测 1.75s，triage=qwen3.8-27b/dialog_stream=qwen3.8-max 关思考链），事件序列 agent(triage)→agent(assessment)→agent(intervention)→sources→token×N→done
- [ ] **SSE 危机验证**：`docker exec psycheflow-backend uv run python scripts/sse_first_token.py --message "我想自杀"` → 首 token N/A（危机不流式），crisis 事件含 12355
- [x] **D5 本地语音验证**：`docker exec psycheflow-backend uv run python test_voice_local.py` → ASR/TTS 均加载成功，无 `ImportError`（2026-09-06 通过）；E2E 往返：`docker exec psycheflow-backend uv run python scripts/voice_e2e.py` → TTS 合成（含 12355）→ ASR 转写关键词命中 PASS（2026-09-07 通过）
- [ ] 浏览器访问 http://localhost:5174/chat → 纯净对话界面（无流程条；每条消息带 AgentBadge 标识分诊/干预等阶段，危机时红色 CrisisBanner）
- [ ] 输入「我最近压力大」→ 回复是共情内容（呼吸/放松建议），**不是**含 12355 的危机话术；**文字应逐字出现**（SSE 流式），非一次性出现
- [ ] 输入「我想自杀」→ CrisisBanner 出现 + 回复含 12355 + sources 为空 + current_agent=escalation
- [ ] 输入「重度抑郁症状」→ sources 里有 `ccmd3_summary.md`
- [ ] C 三期：访问 http://localhost:5174/admin/login → 注册教师账号 → 创建批次 → /screening 输码作答
- [ ] **批次管理回归（5fd9f6e）**：批次列表/详情页可见重命名、删除、reopen（重新开放）按钮且功能正常；创建批次表单展开时不再显示「暂无筛查批次」空状态；批次端点测试 `docker exec psycheflow-backend uv run pytest tests/test_admin_screening.py -q` 全绿
- [ ] **测评提交即存档（5fd9f6e）**：学生登录态完成一份量表提交后，**不点击「生成 PDF 报告」**直接进「历史」页 → 能看到该条记录；点击查看详情能看到完整报告内容（interpretation/answers，非空白）
- [ ] **历史时间回归（5fd9f6e）**：历史列表时间与本机实际时间一致（不再少 8 小时）；报告发展建议兜底场景无「（通用兜底）」内部字样
- [ ] D2 人格切换：对话页底部出现 4 个人格选择芯片
- [ ] D3 语音：对话页有 🎤 按钮 → 录音 → 转写文字到输入框 → 发送 → AI 回复下方有 🔊 朗读按钮
- [ ] 浏览器访问 http://localhost:8000/docs → FastAPI Swagger UI 正常
- [ ] 生产构建验证：`docker exec psycheflow-frontend sh -c "npm run build"` 无错误
- [ ] compose 语法验证：`docker compose -f docker-compose.yml -f docker-compose.prod.yml config --quiet` 无错误
- [ ] 审计 DB 双写：`docker exec psycheflow-backend uv run pytest tests/test_audit.py::TestAudit::test_crisis_dual_writes_db -q` 通过
- [ ] 授权链加固：`docker exec psycheflow-backend uv run pytest tests/test_auth.py::TestTeacherAuthHardening -q` 通过（教师凭 label 登录应 403）
- [ ] 合规 b-文件权限：`docker exec psycheflow-backend ls -la /app/data/psycheflow.db` → `-rw-------`（0600，启动时 restrict_db_file_perms 收紧）；`docker exec psycheflow-backend uv run pytest tests/test_db.py::TestDbFilePerms -q` 通过
- [ ] 合规 b-非 root：`docker exec psycheflow-backend id appuser` → uid=1000（镜像已建用户；生产经 docker-compose.prod.yml `user:"1000:1000"` 启用，部署前须 `chown -R 1000:1000 ./data ./logs`）
- [ ] 合规 c-备份加密：`docker exec psycheflow-backend uv run python scripts/backup_db.py` → 产出 `/app/data/backups/psycheflow-*.db.enc`（空 BACKUP_PASSPHRASE 拒备份）；解密验证 `openssl enc -d -aes-256-cbc -salt -pbkdf2 -iter 100000 -pass pass:$BACKUP_PASSPHRASE -in <enc> -out restored.db` → header=`SQLite format 3`
- [ ] Ollama 兜底单测：`docker exec psycheflow-backend uv run pytest tests/test_llm.py::TestChatOllamaFallback tests/test_llm.py::TestStreamOllamaFallback -q` → 10 passed。默认 `OLLAMA_BASE_URL=` 空 = 禁用（cloud-only 行为不变）；启用需宿主机先 `ollama pull qwen2.5:7b` 再取消 `.env` 中 `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1` 注释 + `docker compose up -d backend` 重建
