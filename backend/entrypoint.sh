#!/bin/bash
set -e

echo "--- Entrypoint Debugging ---"
echo "Current directory: $(pwd)"

# 1. 确保依赖已同步 (解决依赖在启动后才安装导致脚本找不到库的问题)
echo "Syncing dependencies..."
uv sync --no-dev

echo "Checking for .venv..."
ls -d .venv 2>/dev/null || echo ".venv not found!"

# 2. 更加精确地查找 libonnxruntime.so (TTS 需要)
ONNX_CAPI_DIR=$(find /app/.venv -type d -name "capi" | grep "onnxruntime/capi" | head -n 1)

if [ -n "$ONNX_CAPI_DIR" ]; then
    echo "Found onnxruntime capi at: $ONNX_CAPI_DIR"
    
    # 查找所有以 libonnxruntime.so 开头的文件
    SO_FILE=$(find "$ONNX_CAPI_DIR" -name "libonnxruntime.so*" | head -n 1)
    
    if [ -n "$SO_FILE" ]; then
        echo "Found actual SO file: $SO_FILE"
        if [ "$SO_FILE" != "$ONNX_CAPI_DIR/libonnxruntime.so" ]; then
            echo "Creating symlink: $ONNX_CAPI_DIR/libonnxruntime.so -> $SO_FILE"
            ln -sf "$SO_FILE" "$ONNX_CAPI_DIR/libonnxruntime.so"
        fi
    else
        echo "Warning: No libonnxruntime.so* found in $ONNX_CAPI_DIR"
    fi
    
    export LD_LIBRARY_PATH="$ONNX_CAPI_DIR:$LD_LIBRARY_PATH"
else
    echo "Warning: Could not find onnxruntime/capi directory"
fi

# 3. 查找 NVIDIA CUDA 运行时库 (ASR GPU 加速需要)
# 现在已经在上面执行了 uv sync，应该能找到了
CUBLAS_LIB_DIR=$(find /app/.venv -type d -name "lib" | grep "nvidia/cublas/lib" | head -n 1)
CUDNN_LIB_DIR=$(find /app/.venv -type d -name "lib" | grep "nvidia/cudnn/lib" | head -n 1)

if [ -n "$CUBLAS_LIB_DIR" ]; then
    echo "Found nvidia-cublas at: $CUBLAS_LIB_DIR"
    export LD_LIBRARY_PATH="$CUBLAS_LIB_DIR:$LD_LIBRARY_PATH"
fi

if [ -n "$CUDNN_LIB_DIR" ]; then
    echo "Found nvidia-cudnn at: $CUDNN_LIB_DIR"
    export LD_LIBRARY_PATH="$CUDNN_LIB_DIR:$LD_LIBRARY_PATH"
fi

if [ -n "$CUBLAS_LIB_DIR" ] || [ -n "$CUDNN_LIB_DIR" ]; then
    echo "Updated LD_LIBRARY_PATH with NVIDIA libs: $LD_LIBRARY_PATH"
fi

echo "--- Starting Application ---"
# 执行传给 entrypoint 的命令
exec "$@"
