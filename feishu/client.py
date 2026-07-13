# feishu/client.py
"""
飞书 HTTP 客户端：发送消息、添加 Emoji 反应等。
"""
import json
import time
import httpx
from core.config import settings


class FeishuClient:

    def __init__(self):
        self._app_id = settings.feishu_app_id
        self._app_secret = settings.feishu_app_secret
        self._token: str | None = None
        self._token_expires_at: float = 0

    async def _get_token(self) -> str:
        """
        获取 tenant_access_token（有效期 2 小时，自动续期）。

        每次调用飞书 API 都需要这个 token 作为认证。
        为了避免频繁请求，缓存 token 并在过期前更新。
        """
        now = time.time()
        if self._token and now < self._token_expires_at - 60:
            return self._token

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._app_id,
                    "app_secret": self._app_secret,
                },
            )
            data = resp.json()

        self._token = data["tenant_access_token"]
        self._token_expires_at = now + data["expire"]
        return self._token

    async def _headers(self) -> dict:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def send_text(self, receive_id: str, text: str, id_type: str = "open_id"):
        """
        发送文本消息。

        receive_id：接收者的 ID（open_id 是用户 ID，chat_id 是群 ID）
        id_type：receive_id 的类型（"open_id" 或 "chat_id"）
        """
        async with httpx.AsyncClient() as client:
            await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}",
                headers=await self._headers(),
                json={
                    "receive_id": receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )

    async def send_markdown(self, receive_id: str, markdown: str, id_type: str = "open_id"):
        """
        以交互式卡片（interactive）发送 Markdown 内容——飞书的 text 类型不渲染 Markdown，
        必须用卡片里的 markdown 元素，才能显示加粗、代码块、列表、链接等格式。

        receive_id / id_type：同 send_text。
        """
        card = {
            "config": {"wide_screen_mode": True},
            "elements": [
                {"tag": "markdown", "content": markdown},
            ],
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={id_type}",
                headers=await self._headers(),
                json={
                    "receive_id": receive_id,
                    "msg_type": "interactive",
                    "content": json.dumps(card, ensure_ascii=False),
                },
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"send_markdown 失败：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    async def add_reaction(self, message_id: str, emoji: str = "OnIt") -> dict:
        """
        添加 Emoji 反应（显示"处理中"效果），返回响应里的 data（含 reaction_id）。

        emoji 参数：飞书支持的 emoji_type 枚举值，**大小写敏感**，必须用官方合法值：
        "OnIt"     = 👌 处理中（本项目默认，表示"正在处理你的消息"）
        "THUMBSUP" = 👍
        非法值（如 "Thinking"）会返回 code=231001 reaction type is invalid。
        完整枚举见：https://open.feishu.cn/document/server-docs/im-v1/message-reaction/emojis-introduce
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
                headers=await self._headers(),
                json={
                    "reaction_type": {"emoji_type": emoji}
                },
            )
        data = resp.json()
        if data.get("code") != 0:
            # 暴露飞书的真实错误（权限不足、emoji_type 非法等），不要静默吞掉
            raise RuntimeError(f"add_reaction 失败：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    async def remove_reaction(self, message_id: str, reaction_id: str) -> dict:
        """移除 Emoji 反应（处理完成后移除"思考中"）。"""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}",
                headers=await self._headers(),
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"remove_reaction 失败：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})
