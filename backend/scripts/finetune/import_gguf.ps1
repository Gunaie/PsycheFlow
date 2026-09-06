#==============================================================================
# PsycheFlow 3.B 本地导入：把云 GPU 微调产出的 GGUF 注册进 Ollama
#
# 前置：
#   1) 已从云实例下载 GGUF，放到 E:\OllamaModels\gguf\（Ollama 容器挂载 /root/.ollama）
#        qwen2.5-dialog-lora-q4_k_m.gguf
#        qwen2.5-report-lora-q4_k_m.gguf（若训了 report）
#   2) Ollama 容器在运行（docker ps 能看到 ollama）
#
# 用法：在项目根目录 PowerShell 执行
#   powershell -ExecutionPolicy Bypass -File backend\scripts\finetune\import_gguf.ps1
#
# 导入后：.env 设 LOCAL_MODEL_DIALOG=qwen2.5:dialog-lora、
#         LOCAL_MODEL_REPORT=qwen2.5:report-lora，再 docker compose up -d backend
#==============================================================================

$ErrorActionPreference = "Stop"

# 宿主机 GGUF 目录（容器内对应 /root/.ollama/gguf）
$HostGfufDir = "E:\OllamaModels\gguf"

$models = @(
    @{ Gguf = "qwen2.5-dialog-lora-q4_k_m.gguf"; Name = "qwen2.5:dialog-lora" },
    @{ Gguf = "qwen2.5-report-lora-q4_k_m.gguf"; Name = "qwen2.5:report-lora" }
)

Write-Host "=== 检查 Ollama 容器 ===" -ForegroundColor Cyan
docker ps --filter "name=ollama" --filter "status=running" --format "{{.Names}}" |
    Where-Object { $_ -eq "ollama" } | ForEach-Object { Write-Host "  容器 ollama 运行中" }
if (-not (docker ps --filter "name=ollama" --filter "status=running" --format "{{.Names}}")) {
    Write-Error "Ollama 容器未运行，请先 docker start ollama"
    exit 1
}

foreach ($m in $models) {
    $hostPath = Join-Path $HostGfufDir $m.Gguf
    if (-not (Test-Path $hostPath)) {
        Write-Host "[跳过] 未找到 $hostPath" -ForegroundColor Yellow
        continue
    }
    $containerGguf = "/root/.ollama/gguf/$($m.Gguf)"
    $modelfile = "/root/.ollama/Modelfile.$($m.Name -replace ':','-')"
    Write-Host "=== 导入 $($m.Name) <- $($m.Gguf) ===" -ForegroundColor Cyan
    # 在容器内写 Modelfile 并创建模型
    docker exec ollama sh -c "printf 'FROM %s\n' '$containerGguf' > '$modelfile' && ollama create '$($m.Name)' -f '$modelfile'"
}

Write-Host ""
Write-Host "=== 当前 Ollama 模型列表 ===" -ForegroundColor Green
docker exec ollama ollama list

Write-Host ""
Write-Host "下一步：编辑 .env 加入（取消注释）：" -ForegroundColor Cyan
Write-Host "  LOCAL_MODEL_DIALOG=qwen2.5:dialog-lora"
Write-Host "  LOCAL_MODEL_REPORT=qwen2.5:report-lora"
Write-Host "然后重建后端：docker compose up -d backend"
