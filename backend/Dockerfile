# 后端镜像：Python + uv + WeasyPrint 系统库（cairo/pango 等）
# 规避 Windows 本地装 WeasyPrint 的系统库地狱
FROM python:3.11-slim

# apt 换清华源（加速系统库安装；兼容 bookworm DEB822 与旧格式）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g; s|security.debian.org|mirrors.tuna.tsinghua.edu.cn|g' \
        /etc/apt/sources.list

# WeasyPrint 运行期系统依赖（cairo/pango/gdk-pixbuf + 中文字体，否则 PDF 中文变方块）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libpangocairo-1.0-0 \
    libcairo2 \
    libgdk-pixbuf-2.0-0 \
    libffi-dev \
    build-essential \
    curl \
    fonts-wqy-zenhei \
    && rm -rf /var/lib/apt/lists/*

# uv PyPI 用清华源（加速 Python 依赖安装）
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

# 用 pip 从清华源安装 uv（避免从 ghcr.io 拉镜像，国内访问不稳定）
RUN pip install --no-cache-dir uv

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
COPY pyproject.toml ./
RUN uv sync --no-dev 2>/dev/null || uv sync --no-dev --no-lockfile

# 复制业务代码（开发期由 volume 覆盖）
COPY . .

EXPOSE 8000

# 默认命令（compose 中由 command 覆盖为 --reload）
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
