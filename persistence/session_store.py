# persistence/session_store.py
"""
会话持久化存储。

设计原则：
1. 主存储：JSONL 文件（追加写，不修改，简单可靠）
2. 索引：Redis（快速查找 session_id → 最新状态）
3. 降级：Redis 不可用时，直接读 JSONL 文件（慢但可用）
"""
import json
import time
import asyncio
from pathlib import Path
import aiofiles
from providers.types import Message, TextBlock


class SessionStore:

    def __init__(self, base_dir: str = "sessions/", redis_client=None):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.redis = redis_client

    def _session_path(self, session_id: str) -> Path:
        """生成 JSONL 文件路径。用 session_id 的前两位做目录分片（避免单目录文件过多）。"""
        prefix = session_id[:2] if len(session_id) >= 2 else "xx"
        dir_path = self.base_dir / prefix
        dir_path.mkdir(exist_ok=True)
        return dir_path / f"{session_id}.jsonl"

    async def append_message(self, session_id: str, role: str, content: str):
        """追加一条对话消息到 JSONL 文件。"""
        record = {
            "type": "message",
            "ts": int(time.time()),
            "session_id": session_id,
            "role": role,
            "content": content,
        }
        await self._append_record(session_id, record)

    async def append_tool_call(self, session_id: str, tool_name: str, inputs: dict, output: str):
        """追加工具调用记录（仅审计用）。"""
        record = {
            "type": "tool_call",
            "ts": int(time.time()),
            "session_id": session_id,
            "tool": tool_name,
            "input": inputs,
            "output": output[:500],   # 截断，避免大数据
        }
        await self._append_record(session_id, record)

    async def _append_record(self, session_id: str, record: dict):
        """把一条记录追加到 JSONL 文件，同时更新 Redis 索引。"""
        path = self._session_path(session_id)
        line = json.dumps(record, ensure_ascii=False)

        # 追加写入 JSONL 文件
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(line + "\n")

        # 更新 Redis 索引（记录最后更新时间）
        if self.redis:
            try:
                redis_key = f"session:{session_id}"
                await self.redis.hset(redis_key, mapping={
                    "file": str(path),
                    "updated_at": int(time.time()),
                    "last_record": line[:200],   # 存最后一条记录的预览
                })
                await self.redis.expire(redis_key, 7 * 24 * 3600)   # 7 天过期
            except Exception as e:
                print(f"[SessionStore] Redis 更新失败（降级到纯文件模式）：{e}")

    async def load_messages(self, session_id: str, max_turns: int = 20) -> list[Message]:
        """
        从 JSONL 文件加载对话历史，重建 messages 列表。

        参数：
            max_turns - 最多加载几轮（防止历史太长超出 Token 限制）

        返回：
            Message 对象列表，可直接传给 Agentic Loop
        """
        path = self._session_path(session_id)
        if not path.exists():
            return []

        # 读取所有 message 类型的记录
        raw_messages = []
        try:
            async with aiofiles.open(path, "r", encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("type") == "message":
                            raw_messages.append(record)
                    except json.JSONDecodeError:
                        continue   # 跳过损坏的行
        except Exception as e:
            print(f"[SessionStore] 读取会话文件失败：{e}")
            return []

        # 只取最近 max_turns 轮（每轮 = user + assistant）
        max_records = max_turns * 2
        recent = raw_messages[-max_records:] if len(raw_messages) > max_records else raw_messages

        # 重建 Message 对象
        messages = []
        for r in recent:
            role = r.get("role")
            content = r.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append(Message(
                    role=role,
                    content=[TextBlock(text=content)],
                ))

        return messages

    async def clear(self, session_id: str):
        """清除会话历史（用户请求 /clear 时调用）。"""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()

        if self.redis:
            try:
                await self.redis.delete(f"session:{session_id}")
            except Exception:
                pass

        print(f"[SessionStore] 已清除会话：{session_id}")