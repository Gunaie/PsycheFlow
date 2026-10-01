# PsycheFlow Kubernetes 部署清单

## 文件说明

| 文件 | 说明 |
|------|------|
| `namespace.yaml` | 创建 psycheflow 命名空间 |
| `configmap.yaml` | 非敏感环境变量（模型配置/RAG 关键词/语音开关） |
| `secret.yaml` | 敏感配置（数据库 URL/API Key/JWT 密钥），生产环境建议用 sealed-secrets 或 external-secrets 管理 |
| `backend-deployment.yaml` | 后端 Deployment（2 副本 + 健康探针 + 资源限制）+ ClusterIP Service |
| `frontend-deployment.yaml` | 前端 Deployment（2 副本）+ ClusterIP Service |
| `hpa.yaml` | HPA 自动扩缩容（backend: CPU 70% / memory 80%，min 2 / max 10；frontend: CPU 80%，min 2 / max 5） |
| `ingress.yaml` | Ingress 路由（/api 和 /metrics 走 backend，/ 走 frontend）+ TLS + 限流 |
| `pvc.yaml` | 持久卷（data 5Gi / logs 2Gi） |

## 部署步骤

```bash
# 1. 创建命名空间
kubectl apply -f namespace.yaml

# 2. 配置敏感信息（修改 secret.yaml 中的 CHANGE_ME）
kubectl apply -f secret.yaml

# 3. 创建配置和存储
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml

# 4. 部署应用
kubectl apply -f backend-deployment.yaml
kubectl apply -f frontend-deployment.yaml

# 5. 自动扩缩容
kubectl apply -f hpa.yaml

# 6. 入口路由
kubectl apply -f ingress.yaml
```

## 生产环境建议

1. **镜像仓库**：将 `psycheflow-backend:latest` / `psycheflow-frontend:latest` 替换为实际镜像仓库地址（如 `registry.example.com/psycheflow/backend:v1.0.0`）
2. **Secret 管理**：使用 [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets) 或 [external-secrets](https://external-secrets.io/) 替代明文 secret.yaml
3. **数据库**：生产环境使用外部 PostgreSQL（如 RDS/Cloud SQL），不依赖容器内 postgres
4. **监控**：部署 Prometheus + Grafana，抓取 `/metrics` 端点
5. **日志**：配置 Fluentd/Filebeat 采集 `/app/logs` 到 ELK/Loki
