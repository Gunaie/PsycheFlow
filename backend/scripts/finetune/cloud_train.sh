#!/usr/bin/env bash
#==============================================================================
# PsycheFlow 3.B 云 GPU 一键微调脚本（AutoDL 4090 / 同类 Linux + CUDA 环境）
#
# 【重要】所有大文件都放数据盘 autodl-tmp（系统盘 /root 只有 ~30GB 放不下）：
#   - HF 模型缓存（Qwen2.5-7B ~15GB）→ /root/autodl-tmp/hf
#   - 训练包 / LoRA / 合并模型 / GGUF → /root/autodl-tmp/ft
#
# 用法（在云实例终端，先把训练包上传到 /root/autodl-tmp/ft/，
#       finetune_report.jsonl 放到 /root/autodl-tmp/ft/data/）：
#   cd /root/autodl-tmp/ft
#   sed -i 's/\r$//' cloud_train.sh convert_deepwell.py   # 防 Windows CRLF
#   bash cloud_train.sh
#
# 产出（下载这两个 GGUF 回本地即可）：
#   /root/autodl-tmp/ft/gguf/qwen2.5-dialog-lora-q4_k_m.gguf
#   /root/autodl-tmp/ft/gguf/qwen2.5-report-lora-q4_k_m.gguf
#
# 跑完后记得在 AutoDL 控制台【关机】停止计费。
#==============================================================================
set -euo pipefail

# 工作目录：默认 AutoDL 数据盘；可用 FT_DIR=/xxx bash cloud_train.sh 覆盖
FT=${FT_DIR:-/root/autodl-tmp/ft}
DATA=$FT/data
GGUF=$FT/gguf
WORK=$FT/work
mkdir -p "$DATA" "$GGUF" "$WORK"

# HF 模型/数据集缓存放数据盘（否则默认 ~/.cache 占系统盘）
export HF_HOME=/root/autodl-tmp/hf
export PYTHONUNBUFFERED=1
# HuggingFace 国内镜像（直连 hf-mirror 下 Qwen 基座，属国内源，不走学术代理）
export HF_ENDPOINT=${HF_ENDPOINT:-https://hf-mirror.com}
# 注意：pip 用 AutoDL 默认的国内 PyPI 镜像，绝不能先 source /etc/network_turbo
# （学术代理会把国内 pip 源代理坏，报 No matching distribution）。
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

echo "================ [0/6] 环境检查 ================"
nvidia-smi || { echo "未检测到 GPU，请确认实例含 GPU"; exit 1; }
python3 --version
echo "工作目录 FT=$FT"
df -h /root/autodl-tmp | tail -1

echo "================ [1/6] 安装 LLaMA-Factory（国内 pip 源，不开代理）================"
# 不装 [torch]：用镜像自带的 CUDA 版 torch，避免 pip 重装成 CPU/错版。
# 默认源失败则显式换阿里云源兜底。
pip install -q llamafactory bitsandbytes \
  || pip install -q llamafactory bitsandbytes -i https://mirrors.aliyun.com/pypi/simple

# 对齐 torchaudio：pip 可能装到绑定新版 CUDA（如 libcudart.so.13）的 torchaudio，
# 与镜像自带 torch（CUDA 12.x，libcudart.so.12）ABI 冲突 → 启动报 libcudart 找不到。
# torchaudio 与 torch 版本号一致，装回同版本（纯文本训练用不到音频，仅防 import 崩）。
python3 - <<'PY' || true
import subprocess, sys
try:
    import torch
    tv = torch.__version__.split("+")[0]
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", f"torchaudio=={tv}"])
    print(f"[OK] torchaudio 已对齐 torch {tv}")
except Exception as e:
    print("[WARN] torchaudio 对齐失败（可忽略，纯文本训练）:", e)
PY

llamafactory-cli version || true
python3 -c "import torch; print('torch', torch.__version__, 'CUDA 可用:', torch.cuda.is_available())"

echo "================ [2/6] 准备 dialog 数据（DeepWell-Adol）================"
# git clone github 需要 AutoDL 学术加速（pip 已装完，此刻开代理不影响）
if [ -f /etc/network_turbo ]; then source /etc/network_turbo || true; fi
if [ ! -d "$FT/DeepWell-Adolescent" ]; then
  git clone --depth 1 https://github.com/DeepWell-Adol/DeepWell-Adolescent.git "$FT/DeepWell-Adolescent"
fi
python3 "$FT/convert_deepwell.py" "$FT/DeepWell-Adolescent" "$DATA/deepwell_dialog.jsonl"

# report 数据（用户上传）
HAS_REPORT=0
if [ -s "$DATA/finetune_report.jsonl" ]; then
  HAS_REPORT=1
  echo "检测到 finetune_report.jsonl：$(wc -l < "$DATA/finetune_report.jsonl") 条"
else
  echo "[提示] 未找到 $DATA/finetune_report.jsonl —— 跳过 report 微调，只训 dialog。"
fi
cp "$FT/dataset_info.json" "$DATA/dataset_info.json"

echo "================ [3/6] 训练 dialog LoRA ================"
llamafactory-cli train "$FT/train_dialog.yaml"

if [ "$HAS_REPORT" = "1" ]; then
  echo "================ [4/6] 训练 report LoRA ================"
  llamafactory-cli train "$FT/train_report.yaml"
fi

echo "================ [5/6] 准备 llama.cpp（转 GGUF 用）================"
if [ ! -d "$WORK/llama.cpp" ]; then
  git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$WORK/llama.cpp"
fi
# pip 装依赖前关掉学术代理（否则国内 pip 源被代理坏）
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
pip install -q -r "$WORK/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" \
  || pip install -q -r "$WORK/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" \
       -i https://mirrors.aliyun.com/pypi/simple

# 合并 LoRA → 完整 HF 模型 → 转 Q4_K_M GGUF → 删合并模型（省数据盘空间，串行处理）
merge_and_gguf() {
  local adapter=$1 merged=$2 gguf=$3
  echo "---- 合并 $adapter → $merged ----"
  cat > "$WORK/export_tmp.yaml" <<EOF
model_name_or_path: Qwen/Qwen2.5-7B-Instruct
adapter_name_or_path: $adapter
template: qwen
finetuning_type: lora
export_dir: $merged
export_size: 2
export_legacy_format: false
EOF
  llamafactory-cli export "$WORK/export_tmp.yaml"
  echo "---- 转 GGUF → $gguf ----"
  python3 "$WORK/llama.cpp/convert_hf_to_gguf.py" "$merged" \
    --outfile "$gguf" --outtype q4_k_m
  rm -rf "$merged"
}

echo "================ [6/6] 合并 + 转 Q4_K_M GGUF ================"
merge_and_gguf "$FT/lora_dialog" "$WORK/merged_dialog" \
  "$GGUF/qwen2.5-dialog-lora-q4_k_m.gguf"
if [ "$HAS_REPORT" = "1" ]; then
  merge_and_gguf "$FT/lora_report" "$WORK/merged_report" \
    "$GGUF/qwen2.5-report-lora-q4_k_m.gguf"
fi

echo ""
echo "================ 全部完成 ================"
ls -lh "$GGUF"
echo "下载以下 GGUF 回本地（AutoDL 网页文件管理器进 autodl-tmp/ft/gguf 右键下载，或 scp）："
echo "  $GGUF/qwen2.5-dialog-lora-q4_k_m.gguf"
[ "$HAS_REPORT" = "1" ] && echo "  $GGUF/qwen2.5-report-lora-q4_k_m.gguf"
echo ""
echo "下载后回 AutoDL 控制台【关机】停止计费。"
