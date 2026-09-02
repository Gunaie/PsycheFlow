# PsycheFlow 部署指南

新机器从零拉起 PsycheFlow 的完整步骤。区分**开发**与**生产**两种模式。

## 前置条件

- **Docker Desktop**（含 WSL2 后端）+ `docker compose` 子命令。启 GPU 兜底需在 Docker Desktop 设置里开启 NVIDIA GPU 支持（Settings → Resources → Extensions / `--gpus` 支持）。
- **Git** 拉代码。
- **阿里云百炼 API Key**（必填）：https://bailian.console.aliyun.com/ → 模型广场 → API Key。
- （可选）**NVIDIA 显卡 + 驱动**：仅当要启用 Ollama 本地兜底时需要。

## 一、拉代码与配置

```bash
git clone <repo-url> PsychFlow
cd PsychFlow
cp .env.example .env
```

编辑 `.env`，**必填**：

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | 百炼 API Key（不填则所有 LLM/RAG 不可用） |

**生产额外必填**：

| 变量 | 说明 |
|---|---|
| `BACKUP_PASSPHRASE` | SQLite 备份加密口令（AES-256-CBC）；空则 `scripts/backup_db.py` 拒绝备份 |
| `FRONTEND_ORIGIN` | 前端真实地址（CORS），生产改 `https://你的域名` |

**可选**（Ollama 本地兜底，灾备用）：

| 变量 | 说明 |
|---|---|
| `OLLAMA_BASE_URL` | 空=禁用（cloud-only）；填 `http://host.docker.internal:11434/v1` 启用 |
| `OLLAMA_MODEL` | 本地模型名，默认 `qwen2.5:7b` |

模型配置（`MODEL_*`）已预置可用模型，无需改动。

## 二、开发模式

```bash
docker compose up -d --build
```

启动后访问：
- 前端：http://localhost:5174（Vite dev server，热更新）
- 后端 API：http://localhost:8000（Swagger 文档 `/docs`）
- Chroma 向量库：http://localhost:8001

**首次启动后构建 RAG 知识库**（必须，否则分诊/干预拿不到参考资料）：

```bash
curl -X POST http://localhost:8000/api/rag/build
```

## 三、生产模式

```bash
# 1. 数据/日志目录授权给非 root 用户（uid 1000）
#    Windows 下可跳过（Docker Desktop bind mount 不强制）；Linux 部署必须：
sudo chown -R 1000:1000 ./data ./logs

# 2. 放置 TLS 证书到 ./certs/（自签或真实证书）
#    自签示例（CN 填你的域名）：
openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
  -keyout ./certs/key.pem -out ./certs/cert.pem -subj "/CN=your-domain"

# 3. 用 prod override 启动（非 root + 4 worker + nginx TLS + healthcheck）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

生产访问：
- 前端：`https://你的域名`（443，nginx 提供 TLS + HSTS + CSP）
- 后端 API：`http://你的域名:8000`（建议生产再加一层 nginx 反代到 443）

## 四、（可选）启用 Ollama 本地兜底

Ollama 作为**整机共享独立容器**运行（多项目复用一份模型库，不在本仓库 compose 内）：

```bash
# 1. 起共享 Ollama（需 NVIDIA GPU；无 GPU 去掉 --gpus all，CPU 也能跑但 7B 慢）
docker run -d --name ollama --gpus all -p 11434:11434 \
  -v E:/OllamaModels:/root/.ollama --restart always ollama/ollama:latest

# 2. 拉模型（首次）
docker exec ollama ollama pull qwen2.5:7b

# 3.（可选）图形界面 Open WebUI
docker run -d -p 3001:8080 -e OLLAMA_BASE_URL=http://host.docker.internal:11434 \
  -v open-webui:/app/backend/data --name open-webui --restart always \
  ghcr.io/open-webui/open-webui:main
```

在 `.env` 里设：
```
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
OLLAMA_MODEL=qwen2.5:7b
```
重启 backend：`docker compose restart backend`。兜底链：百炼 cloud → Ollama 本地 → 节点级硬编码话术。

## 五、部署后验收

```bash
# 1. 健康检查
curl http://localhost:8000/api/health        # 期望 {"status":"ok",...}

# 2. 端到端验收（7 步：健康/登录/会话/正常对话/危机拦截/报告/审计）
docker exec -e PYTHONUTF8=1 psycheflow-backend uv run python scripts/e2e_acceptance.py
# 期望 7/7 PASS

# 3. 确认 RAG 已建
curl http://localhost:8000/api/rag/search -X POST \
  -H "Content-Type: application/json" -d '{"query":"抑郁","k":3}'
```

## 六、日常运维

```bash
# 看日志
docker compose logs -f backend
docker compose logs -f frontend

# 重启某服务
docker compose restart backend

# 进容器排障
docker exec -it psycheflow-backend sh

# 备份数据库（加密 .db.enc）
docker exec -e BACKUP_PASSPHRASE=$BACKUP_PASSPHRASE psycheflow-backend \
  uv run python scripts/backup_db.py
# 产出 ./data/backup/psycheflow_YYYYMMDD_HHMMSS.db.enc

# 重建 RAG（更新知识库语料后）
curl -X POST http://localhost:8000/api/rag/build

# 跑全量单测
docker exec psycheflow-backend uv run pytest -q
```

## 七、常见问题

- **后端容器起不来 / healthcheck unhealthy**：先看 `docker compose logs backend`；多为 `.env` 缺 `DASHSCOPE_API_KEY` 或 `./data` 目录权限不对（生产非 root 时须 `chown 1000:1000`）。
- **中文 PDF 变方块**：镜像已装 `fonts-wqy-zenhei`；若自改镜像别删该字体。
- **LLM 回复空 / 报 quota**：百炼额度耗尽；检查 `.env` 模型名，或启用 Ollama 兜底。
- **前端 5174 打不开**：dev 端口被占用时 Docker 会映射到别的端口，看 `docker compose ps` 的 PORTS 列。
- **`host.docker.internal` 解析失败**：仅 Linux 主机需在 compose 加 `extra_hosts: ["host.docker.internal:host-gateway"]`（Docker Desktop 自带）。
