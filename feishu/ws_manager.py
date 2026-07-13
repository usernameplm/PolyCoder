# feishu/ws_manager.py
"""
飞书 WebSocket 长连接管理器。

飞书的长连接（Long Connection）模式：你的服务主动连接飞书的 WebSocket 服务器，
飞书有新消息时通过这条连接推送过来。

三个必须注意的坑（否则后端收不到消息）：
1. lark SDK 的 ws.Client.start() 是**同步阻塞**方法（内部自己 run_until_complete），
   不能 await，要放到独立线程里跑，否则会阻塞/破坏 FastAPI 主 loop。
2. lark 在 import 时用**模块级全局 loop**（lark_oapi.ws.client.loop = get_event_loop()），
   在主线程 import 就抓住了 FastAPI 的主 loop。直接在线程里跑 start() 会报
   "This event loop is already running"。必须在工作线程里新建一个**线程私有 loop**，
   并覆盖 lark 模块的全局 loop 变量，让 SDK 的 run_until_complete 跑在这个独立 loop 上。
3. SDK 用**同步方式**调用事件回调（processor.do(data)，不会 await 协程）。而我们的
   handle_event 是 async 的，所以回调里要用 run_coroutine_threadsafe 把协程投递回
   主 event loop 执行——直接 async def 回调会被 SDK 丢弃，body 永不执行。
"""
import asyncio
import json

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .client import FeishuClient
from .handler import MessageHandler
from core.config import settings
from observability.logging import logger


class FeishuWSManager:

    def __init__(self):
        self._client_http = FeishuClient()
        self._handler = MessageHandler(self._client_http)
        self._loop: asyncio.AbstractEventLoop | None = None
        # event_id 去重：在同步回调里做（单线程，无并发穿透）。飞书会重推同一条消息，
        # 尤其当回调 ACK 慢的时候——所以既要秒 ACK，也要在这里挡住重推。
        self._seen_events: set[str] = set()

    async def start(self):
        """启动飞书 WebSocket 长连接，开始接收事件。"""
        # 记住主 event loop：SDK 的回调跑在别的线程，要靠它把协程调度回来
        self._loop = asyncio.get_running_loop()

        # 注册事件处理器（无加密：encrypt_key 和 verification_token 传空字符串）
        event_dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )

        ws_client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=event_dispatcher,
        )

        logger.info("feishu_ws_connecting")
        # 在独立线程里跑：线程内建一个私有 loop 并覆盖 lark 的模块级 loop，
        # 避免和 FastAPI 主 loop 冲突（This event loop is already running）
        await asyncio.to_thread(self._run_ws_blocking, ws_client)

    def _run_ws_blocking(self, ws_client) -> None:
        """在工作线程里以线程私有 loop 运行 lark 的同步阻塞 start()。"""
        thread_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(thread_loop)
        # 关键：覆盖 lark 模块级全局 loop，让 SDK 内部 run_until_complete 用这个私有 loop
        lark_ws_client.loop = thread_loop
        try:
            ws_client.start()   # 同步阻塞，持续监听
        finally:
            thread_loop.close()

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        """
        收到飞书消息事件时的回调（SDK 在独立线程里同步调用）。

        这里是同步函数——SDK 不会 await 协程。关键点：
        - 必须**立刻返回**（不等 handle_event 跑完），否则 lark 迟迟不向飞书 ACK，
          飞书判定推送失败会重推同一条消息 → 一条消息被处理多次。
        - event_id 去重在这里做：本回调是 SDK 单线程串行调用，不会有并发穿透。
        - 用 run_coroutine_threadsafe 把 handle_event 投递回主 loop，但**不 result()**（fire-and-forget）。
        """
        try:
            # P2ImMessageReceiveV1 是对象，先 marshal 成 JSON 再转回 dict，
            # 这样 handler 里 event_data.get("header", {}).get("event_type") 才拿得到值
            payload = json.loads(lark.JSON.marshal(data))

            # event_id 去重：挡住飞书的重推（同步单线程，安全）
            event_id = payload.get("header", {}).get("event_id", "")
            if event_id:
                if event_id in self._seen_events:
                    return
                self._seen_events.add(event_id)
                if len(self._seen_events) > 10000:
                    self._seen_events.clear()

            # fire-and-forget：投递到主 loop 后立刻返回，让 lark 秒 ACK
            asyncio.run_coroutine_threadsafe(
                self._handler.handle_event(payload),
                self._loop,
            )
        except Exception as e:
            logger.error("feishu_event_dispatch_failed", error=str(e))
