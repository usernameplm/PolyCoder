# feishu/handler.py
"""
飞书消息事件处理器。

并发策略：latest-wins 防抖（debounce）。同一会话连发多条消息时，只处理最新一条、
丢弃中间的——收到消息先进入一个防抖窗口，窗口内若又来新消息，就取消上一条的处理任务。
这样避免了「表情/回复时序错乱」和「一条消息被回复多次」的问题。
（event_id 去重在上游 ws_manager 的同步回调里做，那里是单线程，不会有并发穿透。）
"""
import asyncio
import json
from .client import FeishuClient
from coordinator.agent import CoordinatorAgent, clear_session   # 传 session_id 就是带记忆的调用


class MessageHandler:

    # 防抖窗口：窗口内同一会话的新消息会取代旧消息。正常单条对话几乎无感。
    _DEBOUNCE_SEC = 0.8

    def __init__(self, client: FeishuClient):
        self.client = client
        self._coordinator = CoordinatorAgent()
        # 每个会话当前正在等待/处理的任务，新消息到来时取消它（latest-wins）
        self._pending: dict[str, asyncio.Task] = {}

    async def handle_event(self, event_data: dict):
        """处理飞书推送的事件：解析消息，按会话做 latest-wins 防抖调度。"""
        # 只处理消息接收事件
        if event_data.get("header", {}).get("event_type") != "im.message.receive_v1":
            return

        event = event_data.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {})

        # 提取关键信息
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")    # "p2p"（私聊）或 "group"（群聊）
        sender_id = sender.get("sender_id", {}).get("open_id", "")
        chat_id = message.get("chat_id", message.get("open_id", ""))

        # 确定回复目标（私聊 → 发给用户，群聊 → 发给群）
        if chat_type == "p2p":
            receive_id = sender_id
            receive_id_type = "open_id"
            session_key = f"feishu_p2p:{sender_id}"
        else:
            receive_id = chat_id
            receive_id_type = "chat_id"
            session_key = f"feishu_group:{chat_id}:{sender_id}"

        # 提取文本内容
        msg_type = message.get("message_type", "")
        text = ""
        try:
            content = json.loads(message.get("content", "{}"))
            if msg_type == "text":
                text = content.get("text", "").strip()
            elif msg_type == "post":
                # 富文本格式，提取所有文本
                for para in content.get("content", []):
                    for elem in para:
                        if elem.get("tag") == "text":
                            text += elem.get("text", "")
        except Exception:
            pass

        if not text:
            return

        # latest-wins 防抖：同一会话已有待处理任务，先取消（丢弃旧消息，只回最新一条）
        old = self._pending.get(session_key)
        if old and not old.done():
            old.cancel()

        self._pending[session_key] = asyncio.create_task(
            self._debounced_process(session_key, receive_id, receive_id_type, message_id, text)
        )

    async def _debounced_process(self, session_key, receive_id, receive_id_type, message_id, text):
        """先等一个防抖窗口，窗口内若被新消息取消就直接退出，否则真正处理。"""
        try:
            await asyncio.sleep(self._DEBOUNCE_SEC)
        except asyncio.CancelledError:
            return   # 窗口内被更新的消息取代，直接放弃这条
        try:
            await self._process(session_key, receive_id, receive_id_type, message_id, text)
        finally:
            # 处理完清理自己（避免 _pending 无限增长）
            if self._pending.get(session_key) is asyncio.current_task():
                self._pending.pop(session_key, None)

    async def _process(self, session_key, receive_id, receive_id_type, message_id, text):
        """真正处理一条消息：命令 / 加表情 → 调 Agent → 回复 → 去表情。"""
        # 内置命令处理
        if text.strip() in ("/help", "帮助"):
            await self.client.send_text(
                receive_id,
                "你好！我是 AI 助手。直接发消息给我就能对话。\n/clear 清除对话历史",
                id_type=receive_id_type,
            )
            return

        if text.strip() in ("/clear", "清除历史"):
            await clear_session(session_key)
            await self.client.send_text(receive_id, "对话历史已清除。", id_type=receive_id_type)
            return

        # 添加"处理中"Emoji 反应（OnIt 是飞书表示"正在处理"的合法枚举值，
        # 大小写敏感；"Thinking" 不是合法值，会返回 231001 reaction type is invalid）
        reaction_id = None
        try:
            reaction_resp = await self.client.add_reaction(message_id, "OnIt")
            reaction_id = reaction_resp.get("reaction_id")
        except Exception as e:
            # Emoji 反应失败不影响主流程，但要打日志（常见原因：没申请 im:message.reaction 权限）
            print(f"[Handler] 添加处理中 Emoji 失败：{e}")

        # 调用 Agent
        try:
            result = await self._coordinator.run(text, session_id=session_key)
            await self.client.send_text(receive_id, result.text, id_type=receive_id_type)
        except Exception as e:
            await self.client.send_text(receive_id, f"处理时遇到错误，请稍后重试。", id_type=receive_id_type)
            print(f"[Handler] 错误：{e}")
        finally:
            # 移除"处理中"Emoji
            if reaction_id:
                try:
                    await self.client.remove_reaction(message_id, reaction_id)
                except Exception:
                    pass