import asyncio
import os
import sys

# 设置 LD_LIBRARY_PATH 以帮助找到 libonnxruntime.so
import subprocess

def get_lib_paths():
    paths = []
    # 查找 onnxruntime
    onnx_path = subprocess.getoutput('find /app/.venv -type d -name "capi" | grep "onnxruntime/capi" | head -n 1')
    if onnx_path: paths.append(onnx_path)
    # 查找 nvidia libs
    cublas_path = subprocess.getoutput('find /app/.venv -type d -name "lib" | grep "nvidia/cublas/lib" | head -n 1')
    if cublas_path: paths.append(cublas_path)
    cudnn_path = subprocess.getoutput('find /app/.venv -type d -name "lib" | grep "nvidia/cudnn/lib" | head -n 1')
    if cudnn_path: paths.append(cudnn_path)
    return ":".join(paths)

os.environ["LD_LIBRARY_PATH"] = get_lib_paths() + ":" + os.environ.get("LD_LIBRARY_PATH", "")

# 将 app 目录加入路径
sys.path.append(os.path.join(os.getcwd(), "app"))

from app.core.voice_local import _get_asr_model, _get_tts_engine

async def main():
    print("Testing local voice model loading...")
    
    try:
        print("1. Loading ASR model (faster-whisper)...")
        asr = _get_asr_model()
        print("✅ ASR model loaded successfully.")
        
        print("2. Loading TTS engine (sherpa-onnx)...")
        tts = _get_tts_engine()
        print("✅ TTS engine loaded successfully.")
        
        print("\nAll local voice components are ready!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
