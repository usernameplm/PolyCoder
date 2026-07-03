"""
main.py — FastAPI Web 服务入口

提供两个接口：
  POST /ask        → 完整响应（等待全部内容）
  GET  /ask/stream → SSE 流式响应（逐 token 实时推送）

额外接口：
  GET /health      → 健康检查（运维用）
  GET /docs        → 自动生成的 API 文档（FastAPI 内置）
"""

import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

import asyncio
from swarm.blackboard import Blackboard
from swarm.reviewer_agent import ReviewerSwarmAgent
from swarm.debugger_agent import DebuggerSwarmAgent
from swarm.test_writer_agent import TestWriterSwarmAgent
from swarm.task_types import TaskType

from agent import ask_stream
from coordinator.agent import CoordinatorAgent

from typing import Any


# ── 请求/响应数据格式定义 ──────────────────────────────────────────

class AskRequest(BaseModel):
    """POST /ask 的请求体格式"""
    question: str = Field(
        ...,                        # ... 表示必填，不能为空
        min_length=1,
        description="用户的问题",
        examples=["Python 是什么？"]
    )


class AskResponse(BaseModel):
    """POST /ask 的响应体格式"""
    text: str = Field(default="", description="Agent 的回答")
    usage: dict = Field(default_factory=dict, description="Token 用量")
    error: str | None = Field(default=None, description="错误信息")


class SwarmAskRequest(BaseModel):
    """POST /swarm/ask 的请求体格式"""
    task_type: TaskType = Field(..., description="任务类型，范围见 swarm/task_types.py", examples=["code_review"])
    payload: dict = Field(..., description="任务内容，字段随 task_type 变化，如 {'code': '...', 'file': 'a.py'}")
    timeout: float = Field(default=30.0, ge=1.0, le=120.0, description="最长等待秒数，超时仍未完成则返回当前状态（一般是 pending）")


class SwarmAskResponse(BaseModel):
    """POST /swarm/ask 的响应体格式"""
    task_id: str
    status: str                 # pending | claimed | done | failed
    result: Any = None
    error: str | None = None


# ── 应用生命周期（启动/关闭钩子）────────────────────────────────────

# 全局白板 + Swarm Agent 后台任务（跟 _coordinator 一样，启动时初始化一次）
_blackboard = Blackboard()
_swarm_agent_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    yield 前：服务启动时执行（初始化资源）
    yield 后：服务关闭时执行（释放资源）
    """
    print("服务启动中...")
    print(f"API 文档地址：http://localhost:8002/docs")
    print(f"流式测试：curl -N 'http://localhost:8002/ask/stream?question=你好'")

    # 常驻 Swarm Agent：跟 FastAPI 服务同生命周期，不是每次请求创建/销毁
    agents = [
        ReviewerSwarmAgent(_blackboard),
        DebuggerSwarmAgent(_blackboard),
        TestWriterSwarmAgent(_blackboard),
    ]
    _swarm_agent_tasks.extend(asyncio.create_task(a.start()) for a in agents)

    yield

    for task in _swarm_agent_tasks:
        task.cancel()
    await asyncio.gather(*_swarm_agent_tasks, return_exceptions=True)
    print("服务已关闭。")


# ── 创建 FastAPI 实例 ─────────────────────────────────────────────

app = FastAPI(
    title="My Agent API",
    description="基于 Claude 的智能对话服务，支持完整响应和流式响应。",
    version="0.2.0",
    lifespan=lifespan,
)

# 跨域中间件（允许浏览器前端直接调用，生产环境改为具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API 接口定义 ───────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """
    健康检查。运维工具（Kubernetes、负载均衡器）用这个接口确认服务正常。
    返回 200 OK 即可，不需要做复杂逻辑。
    """
    return {"status": "ok", "timestamp": int(time.time())}


# 全局 Coordinator 实例（启动时初始化一次）
_coordinator = CoordinatorAgent()


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    """
    完整响应接口：通过 Coordinator 规划任务 → 分发执行 → 聚合结果。

    请求体：{"question": "你的问题"}
    响应体：{"text": "回答", "usage": {...}, "error": null}
    """
    start = time.time()
    print(f"[/ask] 收到请求: {req.question[:60]}")

    try:
        result = await _coordinator.run(req.question)
        elapsed_ms = round((time.time() - start) * 1000)
        print(f"[/ask] 完成，耗时 {elapsed_ms}ms")
        return AskResponse(text=result)
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000)
        print(f"[/ask] 出错，耗时 {elapsed_ms}ms：{e}")
        return AskResponse(error=str(e))


@app.get("/ask/stream")
async def ask_stream_endpoint(question: str):
    """
    SSE 流式响应接口：逐 token 实时推送，适合前端打字机效果。

    URL 参数：?question=你的问题
    响应：text/event-stream 格式，逐块推送 JSON 数据

    测试方法：
      curl -N "http://localhost:8002/ask/stream?question=请写一首短诗"
    """

    async def event_generator():
        """
        SSE 事件生成器。
        每次 yield 一条 SSE 格式的消息（'data: {...}\\n\\n'）。
        """
        try:
            async for chunk in ask_stream(question):
                # 把文本片段包装成 SSE 格式
                data = json.dumps(
                    {"type": "text_delta", "text": chunk},
                    ensure_ascii=False
                )
                yield f"data: {data}\n\n"

            # 流结束标记
            yield "data: [DONE]\n\n"

        except Exception as e:
            # 出错时发送错误事件，让客户端知道出了问题
            error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",          # 不缓存，保证实时性
            "X-Accel-Buffering": "no",            # 禁用 Nginx 缓冲（如果用了 Nginx 反代）
        },
    )

@app.post("/swarm/ask", response_model=SwarmAskResponse)
async def swarm_ask_endpoint(req: SwarmAskRequest) -> SwarmAskResponse:
    """
    用法跟 /ask 类似：提交一个任务、等结果、拿到最终状态再返回。
    跟 /ask 的区别在于编排方式——这里任务是发布到 Blackboard，由常驻的
    Swarm Agent 通过 claim() 认领执行的，不是主 Agent 直接调用子 Agent。
    """
    try:
        task_id = await _blackboard.post(req.task_type, req.payload)
    except ValueError as e:
        # 正常情况下 task_type 已经被 TaskType（Literal）挡在 API 校验层，
        # 这里兜底的是"合法类型但没有 Agent 注册"这种内部配置错误
        return SwarmAskResponse(task_id="", status="failed", error=str(e))

    # Blackboard 目前只在"有新任务发布"时唤醒等待者，没有"某个任务完成"的专属信号，
    # 所以这里用最简单的方式实现"等结果"：固定间隔轮询任务状态，直到 done/failed 或超时
    deadline = time.time() + req.timeout
    task = _blackboard.get_task(task_id)
    while task.status in ("pending", "claimed") and time.time() < deadline:
        await asyncio.sleep(0.3)
        task = _blackboard.get_task(task_id)

    return SwarmAskResponse(
        task_id=task_id, status=task.status,
        result=task.result, error=task.error,
    )


@app.get("/")
async def index():
    """返回前端测试页面"""
    return FileResponse("static/index.html")


# ── 直接运行的入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.environ.get("APP_PORT", 8002))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,    # 开发模式：代码改动自动重启
    )