#!/bin/bash
# entrypoint.sh — 容器启动脚本

set -e   # 任何命令失败就退出

echo "=== My Agent 服务启动 ==="
echo "Provider：${LLM_PROVIDER:-anthropic}"

# 等待 Redis 就绪（最多等 30 秒）
# 用 uv run python + redis 库探活，不依赖 redis-cli（uv slim 镜像没装它）
if [ -n "${REDIS_HOST}" ]; then
    echo "等待 Redis 就绪..."
    for i in $(seq 1 30); do
        if uv run python -c "import redis, sys; sys.exit(0 if redis.Redis(host='${REDIS_HOST:-redis}', port=${REDIS_PORT:-6379}).ping() else 1)" > /dev/null 2>&1; then
            echo "Redis 已就绪"
            break
        fi
        sleep 1
    done
fi

# 单 Worker + 热重载启动（uv run 会用 .venv 里的 uvicorn）
# 单 worker 也避免了飞书 WebSocket 在多 worker 下各连一次导致的重复处理
echo "启动：单 Worker，热重载开启"
exec uv run uvicorn main:app \
    --host 0.0.0.0 \
    --port 8002 \
    --reload
