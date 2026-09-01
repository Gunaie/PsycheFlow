# PsycheFlow · 智能心理评估系统

利用大语言模型与多智能体技术，提供标准化心理测评、开放式 AI 对话、智能报告生成的一体化心理健康筛查服务。

> 定位：面向中小学生的校园心理筛查辅助工具。**非医疗器械，非替代专业诊疗。**

## 技术栈

- **前端**：React + Vite + TypeScript + TailwindCSS
- **后端**：Python + FastAPI + Pydantic + LangGraph
- **数据库**：SQLite3（MVP）→ PostgreSQL（规模化）
- **向量库**：Chroma
- **模型**：阿里云百炼云端 API（qwen3.7-plus / deepseek-v4-pro-0813 / deepseek-v4-flash-0731 / text-embedding-v3）
- **报告 PDF**：WeasyPrint + Jinja2
- **部署**：Docker Compose（chroma + backend + frontend）

## 快速开始

### 1. 配置密钥
复制环境变量模板并填入百炼 API Key：
```bash
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY
```

### 2. 一键启动（开发模式）
```bash
docker compose up --build
```
- 前端：http://localhost:5173
- 后端 API：http://localhost:8000
- 健康检查：http://localhost:8000/api/health
- Chroma：http://localhost:8001

## 项目结构

```
PsycheFlow/
├── frontend/          # React 前端
├── backend/           # FastAPI 后端
├── data/              # SQLite + Chroma + 量表数据（运行期产物）
├── logs/              # 审计日志
├── docker-compose.yml
├── .env.example
└── 开发计划.md        # 完整开发计划与方案
```

## 开发计划

详见 [开发计划.md](./开发计划.md)。
