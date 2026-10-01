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
| `kustomization.yaml` | Kustomize 基础配置，聚合所有清单并设置镜像仓库 |

## 部署步骤（Kustomize）

```bash
# dev 环境（1 副本，无 HPA，dev 镜像标签）
kustomize build overlays/dev | kubectl apply -f -

# prod 环境（2 副本 + HPA，latest 标签，Always 拉取策略）
kustomize build overlays/prod | kubectl apply -f -
```

> 无 Kustomize CLI 时可直接 `kubectl apply -f` 各文件，但镜像需手动替换为实际仓库地址。

## 手动逐文件部署（旧方式）

```bash
kubectl apply -f namespace.yaml
kubectl apply -f secret.yaml
kubectl apply -f configmap.yaml
kubectl apply -f pvc.yaml
kubectl apply -f backend-deployment.yaml
kubectl apply -f frontend-deployment.yaml
kubectl apply -f hpa.yaml
kubectl apply -f ingress.yaml
```

## CI/CD 自动部署

`.github/workflows/deploy.yml` 已配置 GitHub Actions：main 分支 push 时自动构建 → 推 GHCR → Kustomize 滚动更新 K8s。

前置：仓库 Settings → Secrets → Actions → 添加 `KUBE_CONFIG`（base64 编码的 kubeconfig）：
```bash
cat ~/.kube/config | base64 -w 0
```

## 生产环境建议

1. **镜像仓库**：`kustomization.yaml` 默认指向 `ghcr.io/<owner>/psycheflow/{backend,frontend}`，建议改为私有 Harbor/ACR
2. **Secret 管理**：使用 [sealed-secrets](https://github.com/bitnami-labs/sealed-secrets) 或 [external-secrets](https://external-secrets.io/) 替代明文 secret.yaml
3. **数据库**：生产环境使用外部 PostgreSQL（如 RDS/Cloud SQL），不依赖容器内 postgres
4. **监控**：本地开发用 `docker-compose up prometheus grafana`；生产部署 Prometheus Operator + 导入 `monitoring/grafana/dashboards/psycheflow.json`
5. **日志**：配置 Fluentd/Filebeat 采集 `/app/logs` 到 ELK/Loki
