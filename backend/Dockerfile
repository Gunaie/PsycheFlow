# 后端镜像：Python + uv + WeasyPrint 系统库（cairo/pango 等）
# 规避 Windows 本地装 WeasyPrint 的系统库地狱
FROM python:3.11-slim

# apt 换阿里云源（加速系统库安装；清华源曾对 trixie 限流 403；兼容 DEB822 与旧格式）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
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

# uv PyPI 用阿里云源（加速 Python 依赖安装；清华源曾对本机 IP 限流 403）
ENV UV_DEFAULT_INDEX=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 用 pip 从清华源安装 uv（避免从 ghcr.io 拉镜像，国内访问不稳定）
RUN pip install --no-cache-dir uv

# 创建非 root 用户 appuser(uid 1000)，生产通过 compose user:"1000:1000" 启用；
# 开发期仍以 root 运行（仅测试数据）。HOME 供 uv/uvicorn 以 uid 1000 运行时有可写缓存目录。
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 -m -d /home/appuser appuser
ENV HOME=/home/appuser

WORKDIR /app

# 先装依赖（利用 Docker 缓存层）
COPY pyproject.toml ./
RUN uv sync --no-dev

# 复制业务代码
COPY . .

# 设置启动脚本
RUN chmod +x entrypoint.sh
ENTRYPOINT ["./entrypoint.sh"]

EXPOSE 8000

# 默认命令（compose 中由 command 覆盖为 --reload）
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
