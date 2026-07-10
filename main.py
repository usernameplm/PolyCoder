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
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel, Field

import asyncio
from swarm.blackboard import Blackboard
from swarm.reviewer_agent import ReviewerSwarmAgent
from swarm.debugger_agent import DebuggerSwarmAgent
from swarm.test_writer_agent import TestWriterSwarmAgent
from swarm.task_types import TaskType
from persistence.redis_client import create_redis_client
from swarm.code_extractor import extract_python_code
from swarm.sandbox import resolve_safe_path, PathTraversalError

from agent import ask, ask_stream, clear_session
from observability.logging import logger

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
    session_id: str = Field(
        default="web:default",
        description="会话 ID，同一 session_id 的多次请求会共享对话历史（第 10 章）",
    )


class AskResponse(BaseModel):
    """POST /ask 的响应体格式"""
    text: str = Field(default="", description="Agent 的回答")
    session_id: str = Field(default="", description="本次请求使用的会话 ID，回传给前端便于下一轮携带")
    usage: dict = Field(default_factory=dict, description="Token 用量")
    error: str | None = Field(default=None, description="错误信息")


class ClearSessionRequest(BaseModel):
    """POST /session/clear 的请求体格式"""
    session_id: str = Field(..., min_length=1, description="要清除的会话 ID")


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
    derived_task_ids: list[str] = Field(default_factory=list, description="这个任务自动派生出的子任务 ID 列表，可用 GET /swarm/tasks/{id} 逐个查询")


class ApplyRequest(BaseModel):
    """POST /swarm/tasks/{task_id}/apply 的请求体格式"""
    path: str = Field(..., min_length=1, description="写入的目标路径，相对工作目录（SWARM_WORKSPACE），如 'out/auth.fixed.py'")


class ApplyResponse(BaseModel):
    """POST /swarm/tasks/{task_id}/apply 的响应体格式"""
    path: str
    bytes_written: int
    source: str


# ── 应用生命周期（启动/关闭钩子）────────────────────────────────────

# 全局白板 + Swarm Agent 后台任务（跟 _coordinator 一样，启动时初始化一次）
_blackboard = Blackboard()
_swarm_agent_tasks: list[asyncio.Task] = []
_redis_client = create_redis_client()
_swarm_save_task: asyncio.Task | None = None


async def periodic_save(blackboard: Blackboard, redis_client, interval: float = 5.0):
    """后台循环：每隔 interval 秒把白板状态备份到 Redis，直到被取消。"""
    fail_count = 0
    while True:
        await asyncio.sleep(interval)
        try:
            await redis_client.ping()
            fail_count = 0
            await blackboard.save_to_redis(redis_client)
        except Exception:
            fail_count += 1
            if fail_count == 1:
                logger.warning("periodic_save_redis_unavailable")
            if fail_count >= 3:
                await asyncio.sleep(60)  # Redis 持续不可用时放慢重试频率


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _swarm_save_task

    logger.info("server_starting")
    logger.info("api_docs_url", url="http://localhost:8002/docs")
    logger.info("stream_test_hint", hint="curl -N 'http://localhost:8002/ask/stream?question=你好'")

    # 1. 启动时先尝试从 Redis 恢复上次未完成的任务
    await _blackboard.load_from_redis(_redis_client)

    # 2. 常驻 Swarm Agent
    agents = [
        ReviewerSwarmAgent(_blackboard),
        DebuggerSwarmAgent(_blackboard),
        TestWriterSwarmAgent(_blackboard),
    ]
    _swarm_agent_tasks.extend(asyncio.create_task(a.start()) for a in agents)

    # 3. 后台定期备份，不阻塞主流程
    _swarm_save_task = asyncio.create_task(periodic_save(_blackboard, _redis_client))

    yield

    # 4. 收尾：停 Agent、停后台备份任务、退出前再存一次、关闭连接
    for task in _swarm_agent_tasks:
        task.cancel()
    await asyncio.gather(*_swarm_agent_tasks, return_exceptions=True)

    _swarm_save_task.cancel()
    await _blackboard.save_to_redis(_redis_client)
    await _redis_client.close()
    logger.info("server_shutdown")


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


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    """
    完整响应接口：调用 Agent 回答问题，支持按 session_id 记忆多轮对话（第 10 章）。

    请求体：{"question": "你的问题", "session_id": "可选，同一会话传相同值"}
    响应体：{"text": "回答", "session_id": "...", "usage": {...}, "error": null}
    """
    start = time.time()
    log = logger.bind(session_id=req.session_id)
    log.info("ask_request_received", question=req.question[:60])

    try:
        result = await ask(req.question, session_id=req.session_id)
        elapsed_ms = round((time.time() - start) * 1000)
        log.info("ask_request_done", elapsed_ms=elapsed_ms)
        return AskResponse(
            text=result.text,
            session_id=req.session_id,
            usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "turn_count": result.turn_count,
            },
        )
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000)
        log.error("ask_request_error", elapsed_ms=elapsed_ms, error=str(e))
        return AskResponse(session_id=req.session_id, error=str(e))


@app.get("/ask/stream")
async def ask_stream_endpoint(question: str, session_id: str | None = None):
    """
    SSE 流式响应接口：逐 token 实时推送，适合前端打字机效果。

    URL 参数：?question=你的问题&session_id=可选，同一会话传相同值
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
            async for chunk in ask_stream(question, session_id=session_id):
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


@app.post("/session/clear")
async def clear_session_endpoint(req: ClearSessionRequest) -> dict:
    """
    清除一个会话的历史（JSONL 文件 + Redis 缓存）。

    请求体：{"session_id": "..."}
    """
    await clear_session(req.session_id)
    return {"session_id": req.session_id, "cleared": True}


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
        derived_task_ids=task.derived_task_ids,
    )


@app.get("/swarm/tasks/{task_id}", response_model=SwarmAskResponse)
async def get_swarm_task(task_id: str) -> SwarmAskResponse:
    """按 task_id 补查任务最新状态，配合 /swarm/ask 超时后的场景使用。"""
    task = _blackboard.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return SwarmAskResponse(
        task_id=task.id, status=task.status,
        result=task.result, error=task.error,
        derived_task_ids=task.derived_task_ids,
    )


@app.get("/swarm/tasks")
async def list_swarm_tasks() -> dict:
    """白板整体状态摘要，如 {"pending": 2, "done": 5}，运维/监控用，不是业务调用入口。"""
    return _blackboard.summary()


@app.post("/swarm/tasks/{task_id}/apply", response_model=ApplyResponse)
async def apply_swarm_task(task_id: str, req: ApplyRequest) -> ApplyResponse:
    """
    把任务结果里的代码块真正写到磁盘。
    """
    task = _blackboard.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    if task.status != "done":
        raise HTTPException(status_code=409, detail=f"任务 {task_id} 当前状态是 {task.status}，未完成不能落地")

    if not isinstance(task.result, str) or not task.result.strip():
        raise HTTPException(status_code=400, detail=f"任务 {task_id} 没有可写入的文本结果")

    code, source = extract_python_code(task.result)
    if code is None:
        raise HTTPException(status_code=400, detail="任务结果里没有找到可抽取的代码块（可能是 code_review 这类审查意见，本身不含代码）")

    try:
        target = resolve_safe_path(req.path)
    except PathTraversalError as e:
        raise HTTPException(status_code=400, detail=str(e))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    return ApplyResponse(path=str(target), bytes_written=len(code.encode("utf-8")), source=source)


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