#!/usr/bin/env bash
#==============================================================================
# PsycheFlow 3.B 云 GPU 一键微调脚本（AutoDL 4090 / 同类 Linux + CUDA 环境）
#
# 用法（在云实例终端）：
#   1) 把整个 finetune 训练包上传到 /root/ft/（含本脚本、yaml、dataset_info.json、
#      convert_deepwell.py），并把本地导出的 finetune_report.jsonl 放到 /root/ft/data/
#   2) cd /root/ft && bash cloud_train.sh
#
# 产出（下载这两个 GGUF 回本地即可）：
#   /root/ft/gguf/qwen2.5-dialog-lora-q4_k_m.gguf
#   /root/ft/gguf/qwen2.5-report-lora-q4_k_m.gguf
#
# 跑完后记得在 AutoDL 控制台关机停止计费。
#==============================================================================
set -euo pipefail

FT=/root/ft
DATA=$FT/data
GGUF=$FT/gguf
mkdir -p "$DATA" "$GGUF"

# 国内 HF 镜像加速（AutoDL 环境）
export HF_ENDPOINT=https://hf-mirror.com
export PYTHONUNBUFFERED=1

echo "================ [0/6] 环境检查 ================"
nvidia-smi || { echo "未检测到 GPU，请确认实例含 GPU"; exit 1; }
python3 --version

echo "================ [1/6] 安装 LLaMA-Factory ================"
pip install -q "llamafactory[torch,metrics]" || pip install -q llamafactory
which llamafactory-cli && llamafactory-cli version || true

echo "================ [2/6] 准备 dialog 数据（DeepWell-Adol）================"
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

# 数据集注册文件放到数据目录
cp "$FT/dataset_info.json" "$DATA/dataset_info.json"

echo "================ [3/6] 训练 dialog LoRA ================"
llamafactory-cli train "$FT/train_dialog.yaml"

if [ "$HAS_REPORT" = "1" ]; then
  echo "================ [4/6] 训练 report LoRA ================"
  llamafactory-cli train "$FT/train_report.yaml"
fi

echo "================ [5/6] 合并 LoRA → 完整 HF 模型 ================"
make_export_yaml() {
  local adapter=$1 out=$2
  cat > "$FT/export_$(basename "$out").yaml" <<EOF
model_name_or_path: Qwen/Qwen2.5-7B-Instruct
adapter_name_or_path: $adapter
template: qwen
finetuning_type: lora
export_dir: $out
export_size: 2
export_legacy_format: false
EOF
}
make_export_yaml "$FT/lora_dialog" "$FT/merged_dialog"
llamafactory-cli export "$FT/export_merged_dialog.yaml"
if [ "$HAS_REPORT" = "1" ]; then
  make_export_yaml "$FT/lora_report" "$FT/merged_report"
  llamafactory-cli export "$FT/export_merged_report.yaml"
fi

echo "================ [6/6] 转 Q4_K_M GGUF ================"
if [ ! -d /root/llama.cpp ]; then
  git clone --depth 1 https://github.com/ggerganov/llama.cpp /root/llama.cpp
fi
pip install -q -r /root/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt
python3 /root/llama.cpp/convert_hf_to_gguf.py "$FT/merged_dialog" \
  --outfile "$GGUF/qwen2.5-dialog-lora-q4_k_m.gguf" --outtype q4_k_m
if [ "$HAS_REPORT" = "1" ]; then
  python3 /root/llama.cpp/convert_hf_to_gguf.py "$FT/merged_report" \
    --outfile "$GGUF/qwen2.5-report-lora-q4_k_m.gguf" --outtype q4_k_m
fi

echo ""
echo "================ 全部完成 ================"
ls -lh "$GGUF"
echo "下载以下 GGUF 回本地（AutoDL 网页文件管理器或 scp）："
echo "  $GGUF/qwen2.5-dialog-lora-q4_k_m.gguf"
[ "$HAS_REPORT" = "1" ] && echo "  $GGUF/qwen2.5-report-lora-q4_k_m.gguf"
echo "下载后回 AutoDL 控制台【关机】停止计费。"
