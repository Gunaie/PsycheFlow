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
# 关闭 Xet 存储协议：新版 huggingface_hub 默认走 cas-server.xethub.hf.co，
# 该域名国内直连 401（hf-mirror 只代理传统 HTTP LFS 下载，不代理 Xet）
export HF_HUB_DISABLE_XET=1
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

# 修复 torch CUDA：pip 装 llamafactory 可能把镜像自带的 CUDA 版 torch 覆盖成 +cpu 版，
# 导致 bf16 训练报 "Your setup doesn't support bf16/gpu"。检测到 CPU 版则强制装回 cu128。
if ! python3 -c "import torch; assert torch.cuda.is_available()" 2>/dev/null; then
  TORCH_VER=$(python3 -c "import torch; print(torch.__version__.split('+')[0])" 2>/dev/null || echo "2.8.0")
  echo "[WARN] torch 无 CUDA，重装 CUDA ${TORCH_VER} 版（pytorch 官方源需学术加速）..."
  if [ -f /etc/network_turbo ]; then source /etc/network_turbo || true; fi
  pip install "torch==${TORCH_VER}" torchvision torchaudio \
    --index-url https://download.pytorch.org/whl/cu128
  unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
fi
python3 -c "import torch; print('[OK] torch', torch.__version__, 'CUDA 可用:', torch.cuda.is_available())"

llamafactory-cli version || true

echo "================ [2/6] 准备 dialog 数据（DeepWell-Adol + 云端扩样 + 改写）================"
# git clone github 需要 AutoDL 学术加速（pip 已装完，此刻开代理不影响）
if [ -f /etc/network_turbo ]; then source /etc/network_turbo || true; fi
if [ ! -d "$FT/DeepWell-Adolescent" ]; then
  git clone --depth 1 https://github.com/DeepWell-Adol/DeepWell-Adolescent.git "$FT/DeepWell-Adolescent"
fi
python3 "$FT/convert_deepwell.py" "$FT/DeepWell-Adolescent" "$DATA/deepwell_dialog.jsonl"

# 关掉学术加速代理（pip 装 openai 需要国内源，不能走代理）
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY

# [2a] 云端扩 500 条合成样本（需 DASHSCOPE_API_KEY 环境变量）
# 若未设置 KEY 或 synthetic_dialog.jsonl 已存在则跳过
if [ -n "${DASHSCOPE_API_KEY:-}" ] && [ ! -f "$DATA/synthetic_dialog.jsonl" ]; then
  echo "---- [2a] 云端扩样：生成 500 条合成样本 ----"
  pip install -q openai || pip install -q openai -i https://mirrors.aliyun.com/pypi/simple
  python3 "$FT/gen_samples_cloud.py" --out "$DATA/synthetic_dialog.jsonl"
elif [ -z "${DASHSCOPE_API_KEY:-}" ]; then
  echo "[提示] 未设置 DASHSCOPE_API_KEY，跳过云端扩样与改写（仅用 DeepWell 数据训练）"
fi

# [2b] 改写 DeepWell 被剔除的模板腔样本（需 DASHSCOPE_API_KEY + 原始数据）
if [ -n "${DASHSCOPE_API_KEY:-}" ] && [ ! -f "$DATA/deepwell_rewritten.jsonl" ]; then
  echo "---- [2b] 改写：DeepWell 模板腔样本 → 合规版本 ----"
  python3 "$FT/rewrite_deepwell.py" "$FT/DeepWell-Adolescent" -o "$DATA/deepwell_rewritten.jsonl"
fi

# [2c] 合并三份数据 + 注入 system prompt
echo "---- [2c] 合并数据 + 注入 system prompt ----"
MERGE_INPUTS="$DATA/deepwell_dialog.jsonl"
[ -f "$DATA/synthetic_dialog.jsonl" ] && MERGE_INPUTS="$MERGE_INPUTS $DATA/synthetic_dialog.jsonl"
[ -f "$DATA/deepwell_rewritten.jsonl" ] && MERGE_INPUTS="$MERGE_INPUTS $DATA/deepwell_rewritten.jsonl"
python3 "$FT/merge_datasets.py" $MERGE_INPUTS -o "$DATA/dialog_merged.jsonl"
echo "合并后样本数：$(wc -l < "$DATA/dialog_merged.jsonl")"

# report 数据（用户上传）
HAS_REPORT=0
if [ -s "$DATA/finetune_report.jsonl" ]; then
  HAS_REPORT=1
  echo "检测到 finetune_report.jsonl：$(wc -l < "$DATA/finetune_report.jsonl") 条"
else
  echo "[提示] 未找到 $DATA/finetune_report.jsonl —— 跳过 report 微调，只训 dialog。"
fi
cp "$FT/dataset_info.json" "$DATA/dataset_info.json"

echo "================ [2.5/6] 预下载基座 Qwen2.5-7B-Instruct ================"
# 下模型走国内源（modelscope 阿里源最快），关掉学术代理
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
MODEL_DIR=$WORK/Qwen2.5-7B-Instruct
if [ -f "$MODEL_DIR/config.json" ]; then
  echo "本地已存在基座：$MODEL_DIR"
else
  pip install -q modelscope || pip install -q modelscope -i https://mirrors.aliyun.com/pypi/simple
  modelscope download --model Qwen/Qwen2.5-7B-Instruct --local_dir "$MODEL_DIR" || {
    echo "[WARN] modelscope 失败，回退 HF mirror（已禁用 Xet）"
    python3 - <<PY || { echo "基座下载失败"; exit 1; }
from huggingface_hub import snapshot_download
snapshot_download("Qwen/Qwen2.5-7B-Instruct", local_dir=r"$MODEL_DIR", max_workers=4)
PY
  }
fi
[ -f "$MODEL_DIR/config.json" ] || { echo "模型下载失败：$MODEL_DIR 无 config.json"; exit 1; }
echo "基座就绪：$MODEL_DIR（$(du -sh "$MODEL_DIR" | cut -f1)）"
# 训练 yaml 指向本地模型目录，LLaMA-Factory 不再联网下载
sed -i "s#^model_name_or_path:.*#model_name_or_path: $MODEL_DIR#" \
  "$FT/train_dialog.yaml" "$FT/train_report.yaml"

echo "================ [3/6] 训练 dialog LoRA ================"
# 断点续跑：适配器已存在则跳过（重跑脚本不会重训）
if [ -f "$FT/lora_dialog/adapter_model.safetensors" ]; then
  echo "[跳过] $FT/lora_dialog 已有训练好的适配器"
else
  llamafactory-cli train "$FT/train_dialog.yaml"
fi

if [ "$HAS_REPORT" = "1" ]; then
  echo "================ [4/6] 训练 report LoRA ================"
  if [ -f "$FT/lora_report/adapter_model.safetensors" ]; then
    echo "[跳过] $FT/lora_report 已有训练好的适配器"
  else
    llamafactory-cli train "$FT/train_report.yaml"
  fi
fi

# 训练全部完成，清理 checkpoint 中间产物（含 optimizer state，每个 1-2GB），只留最终 adapter
rm -rf "$FT/lora_dialog"/checkpoint-* "$FT/lora_report"/checkpoint-* 2>/dev/null || true
echo "[清理] checkpoint 中间产物已删除，剩余空间：$(df -h /root/autodl-tmp | tail -1 | awk '{print $4}')"

echo "================ [5/6] 准备 llama.cpp（转 GGUF 用）================"
# git clone github 需要学术加速
if [ -f /etc/network_turbo ]; then source /etc/network_turbo || true; fi
# 校验 convert 脚本存在（防半截 clone 的空目录）
if [ ! -f "$WORK/llama.cpp/convert_hf_to_gguf.py" ]; then
  rm -rf "$WORK/llama.cpp"
  git clone --depth 1 https://github.com/ggerganov/llama.cpp.git "$WORK/llama.cpp"
fi
# 兼容补丁：新版 llama.cpp master 的 gguf lazy tensor 用 np.memmap 读权重，
# torch.from_numpy 精确类型检查拒绝 memmap 子类：
# "TypeError: expected np.ndarray (got memmap)"
# → 把 byteswap_tensor 的返回值 .view(np.ndarray) 零拷贝转成基类视图（幂等）
python3 - <<PY
import re
p = "$WORK/llama.cpp/conversion/base.py"
s = open(p, encoding="utf-8").read()
m = re.search(r"import numpy as (\w+)", s)
np_name = m.group(1) if m else "np"
old = "torch.from_numpy(byteswap_tensor(tensor.mmap_bytes(), numpy_dtype))"
new = f"torch.from_numpy(byteswap_tensor(tensor.mmap_bytes(), numpy_dtype).view({np_name}.ndarray))"
if new in s:
    print("[跳过] memmap 补丁已存在")
elif old in s:
    open(p, "w", encoding="utf-8").write(s.replace(old, new))
    print("[OK] base.py 已打 memmap 补丁（numpy alias:", np_name + "）")
else:
    print("[WARN] 未匹配到补丁位置，新版 llama.cpp 可能已改结构，需人工检查")
PY
# pip 装依赖前关掉学术代理（否则国内 pip 源被代理坏）
unset http_proxy https_proxy HTTP_PROXY HTTPS_PROXY
pip install -q -r "$WORK/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" \
  || pip install -q -r "$WORK/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt" \
       -i https://mirrors.aliyun.com/pypi/simple
# llama.cpp 转换依赖会把 transformers 拉到 5.x，破坏 peft/llamafactory 的导入
# （ImportError: cannot import name 'PreTrainedModel'）→ 卸干净后重装 4.x。
# 实测仅 pip install 降级可能不生效，必须先 uninstall。
pip uninstall -y transformers || true
pip install -q "transformers>=4.49,<5" \
  || pip install -q "transformers>=4.49,<5" -i https://mirrors.aliyun.com/pypi/simple
# llama.cpp 转换依赖把 numpy 钉在 1.26.4，与 scipy 1.18+（要求 numpy>=2）冲突，
# 连锁导致 transformers.modeling_utils 导入崩溃（表现为误导性的
# "cannot import name 'PreTrainedModel'"）→ 降级 scipy 到兼容 numpy 1.26 的版本
pip install -q "scipy==1.13.1" \
  || pip install -q "scipy==1.13.1" -i https://mirrors.aliyun.com/pypi/simple
# numpy 2.x 与 llama.cpp 0.4.0-dev 的 convert_hf_to_gguf.py 不兼容：
# conversion/base.py 中 torch.from_numpy(memmap) 报
# "TypeError: expected np.ndarray (got memmap)" → 强制钉回 1.26.4
pip install -q "numpy==1.26.4" \
  || pip install -q "numpy==1.26.4" -i https://mirrors.aliyun.com/pypi/simple
python3 -c "import numpy; assert numpy.__version__.startswith('1.'), numpy.__version__; print('[OK] numpy', numpy.__version__)"
# llama.cpp 转换依赖会把 torch 升到最新版并拆散 torchvision/torchaudio 配对
# （RuntimeError: operator torchvision::nms does not exist）→ 全家族对齐回 2.8.0。
# 注意 pip 比较版本时看不见 +cpu/+cu128 后缀，必须先卸载强制重装正确的构建。
if [ -f "$FT/lora_dialog/adapter_model.safetensors" ]; then
  echo "[对齐] torch 家族 → 2.8.0 CPU 版（训练已完成，合并/转换不碰 GPU）"
  pip uninstall -y torch torchvision torchaudio || true
  pip install -q torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
    --index-url https://download.pytorch.org/whl/cpu
else
  echo "[对齐] torch 家族 → 2.8.0 CUDA 版（训练未完成，还需 GPU）"
  pip uninstall -y torch torchvision torchaudio || true
  pip install -q torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0 \
    --index-url https://download.pytorch.org/whl/cu128
fi
# 全链路探针：torch 家族 ABI + transformers + peft + llamafactory.data（mm_plugin→torchaudio）
python3 -c "import torch, torchvision, torchaudio, transformers, peft; from llamafactory.data import template; from transformers import PreTrainedModel" \
  || { echo "[FATAL] 训练栈导入链仍损坏，把上方完整日志发回"; exit 1; }
echo "[OK] 导入链完整，可以合并导出"

# convert_hf_to_gguf.py 只支持 f32/f16/bf16/q8_0/auto，不支持 q4_k_m！
# Q4_K_M 必须：先转 f16 GGUF → 再用 llama-quantize 量化（需编译 llama.cpp，只编 quantize 目标）
if [ ! -x "$WORK/llama.cpp/build/bin/llama-quantize" ]; then
  echo "---- 编译 llama-quantize（量化工具，约 5-10 分钟）----"
  command -v cmake >/dev/null || pip install -q cmake \
    || pip install -q cmake -i https://mirrors.aliyun.com/pypi/simple
  command -v g++ >/dev/null || { apt-get update -y && apt-get install -y build-essential; }
  (cd "$WORK/llama.cpp" \
    && cmake -B build -DGGML_NATIVE=ON -DLLAMA_CURL=OFF \
    && cmake --build build --config Release -j"$(nproc)" --target llama-quantize)
fi

# 合并 LoRA → 完整 HF 模型 → 转 f16 GGUF → llama-quantize 量化 Q4_K_M → 清理中间产物
merge_and_gguf() {
  local adapter=$1 merged=$2 gguf=$3
  if [ -f "$gguf" ]; then
    echo "[跳过] 已存在 $gguf"
    return 0
  fi
  if [ -f "$merged/config.json" ]; then
    echo "[跳过合并] $merged 已存在，直接转换"
  else
    echo "---- 合并 $adapter → $merged ----"
    cat > "$WORK/export_tmp.yaml" <<EOF
model_name_or_path: $MODEL_DIR
adapter_name_or_path: $adapter
template: qwen
finetuning_type: lora
export_dir: $merged
export_size: 2
export_legacy_format: false
EOF
    llamafactory-cli export "$WORK/export_tmp.yaml"
  fi
  local f16="${gguf%.gguf}-f16.gguf"
  echo "---- 转 GGUF(f16) → $f16 ----"
  python3 "$WORK/llama.cpp/convert_hf_to_gguf.py" "$merged" \
    --outfile "$f16" --outtype f16
  # f16 转换完成立即删 merged（15GB）：量化只读 f16，不再需要 merged
  rm -rf "$merged"
  echo "---- 量化 → $gguf (Q4_K_M) ----"
  (cd "$WORK/llama.cpp/build/bin" && ./llama-quantize "$f16" "$gguf" Q4_K_M)
  rm -f "$f16"
}

echo "================ [6/6] 合并 + 转 Q4_K_M GGUF ================"
# numpy 自检：gguf/torch 依赖链可能装出双 numpy 副本或 numpy 2.x，
# 导致 convert_hf_to_gguf.py 报 "expected np.ndarray (got memmap/ndarray)"
# （torch C 层 PyObject_TypeCheck 用的 PyArray_Type 与实际数组来自不同 numpy 安装）。
# 自检失败则卸载全部副本 + 物理清残留 + 重装 1.26.4。
if ! python3 -c "import numpy, torch; torch.from_numpy(numpy.zeros(3, dtype=numpy.float32))" 2>/dev/null; then
  echo "[WARN] numpy/torch ABI 异常（双 numpy 副本或 numpy 2.x），清理重装 numpy 1.26.4..."
  pip uninstall -y numpy >/dev/null 2>&1 || true
  pip uninstall -y numpy >/dev/null 2>&1 || true
  python3 - <<'PY'
import glob, os, shutil, site
paths = list(site.getsitepackages())
try: paths.append(site.getusersitepackages())
except Exception: pass
for sp in set(paths):
    for pat in ("numpy", "numpy-*.dist-info", "numpy-*.egg-info"):
        for p in glob.glob(os.path.join(sp, pat)):
            shutil.rmtree(p, ignore_errors=True)
            print("  removed:", p)
PY
  pip install -q "numpy==1.26.4" -i https://mirrors.aliyun.com/pypi/simple
  python3 -c "import numpy, torch; torch.from_numpy(numpy.zeros(3, dtype=numpy.float32)); print('[OK] numpy', numpy.__version__, 'from_numpy 正常')"
fi

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
