# Dockerfile

# 使用 uv 官方镜像（自带 uv + Python 3.14，和 .python-version / pyproject 的 requires-python 一致）
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# 设置工作目录
WORKDIR /app

# 设置 Python 不生成 .pyc 文件（容器里不需要）；输出不缓冲（日志实时可见）
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# 用国内镜像加速依赖下载；超时设长一些防止大包下载中断
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV UV_HTTP_TIMEOUT=120

# 系统依赖：docling 间接依赖 opencv-python（版面分析用），在 slim 镜像里
# import cv2 会因缺少这些动态库而报错（libGL.so.1 等），需提前装好。
# --no-install-recommends 减小镜像体积；装完清理 apt 缓存。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# 先只复制依赖清单，利用 Docker 层缓存（依赖没变时不重装）
COPY pyproject.toml uv.lock ./

# 按 uv.lock 精确安装依赖到 .venv（--frozen 不改锁文件；--no-install-project 此时还没复制项目代码；
# --no-dev 不装 dev 依赖组，如 pytest）
RUN uv sync --frozen --no-install-project --no-dev

# 再复制应用代码（代码改动时只需重新构建这一层，依赖层缓存有效）
COPY . .

# 把项目本身也装进环境（此时代码已就位）
RUN uv sync --frozen --no-dev

# 暴露服务端口（声明意图，实际映射在 docker-compose.yml 里）
EXPOSE 8002

# 给启动脚本加执行权限
RUN chmod +x entrypoint.sh

# 启动命令
ENTRYPOINT ["./entrypoint.sh"]
