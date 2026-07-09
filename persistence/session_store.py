# persistence/session_store.py
"""
会话持久化存储。

设计原则：
1. 主存储：JSONL 文件（追加写，不修改，简单可靠，永久保存全部历史）
2. 缓存：Redis List（缓存最近 CACHE_MAX_TURNS 轮消息内容本身，加速加载）
3. 降级：Redis 不可用 / 未命中时，直接读 JSONL 文件（慢但一定可用），
   并顺手把读到的内容回填进 Redis，让下一次命中缓存

关键点：Redis 缓存的必须是**消息内容本身**，而不是文件路径或摘要——
文件路径本来就能由 session_id 直接算出来（见 _session_path），
缓存路径没有意义；只有缓存内容才能真正省掉磁盘 I/O 和 JSONL 解析开销。
"""
import json
import time
import asyncio
from pathlib import Path
import aiofiles
from providers.types import Message, TextBlock

# Redis List 里最多缓存多少轮对话（每轮 = user + assistant，即 2 条记录）
# 请求的 max_turns 超过这个窗口时，缓存肯定不完整，直接退回读文件。
CACHE_MAX_TURNS = 20
CACHE_MAX_RECORDS = CACHE_MAX_TURNS * 2
CACHE_TTL_SECONDS = 7 * 24 * 3600   # 7 天没有新消息，缓存自动过期


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

    def _cache_key(self, session_id: str) -> str:
        return f"session_msgs:{session_id}"

    async def append_message(self, session_id: str, role: str, content: str):
        """追加一条对话消息到 JSONL 文件，并同步进 Redis 缓存。"""
        record = {
            "type": "message",
            "ts": int(time.time()),
            "session_id": session_id,
            "role": role,
            "content": content,
        }
        await self._append_record(session_id, record, cache=True)

    async def append_tool_call(self, session_id: str, tool_name: str, inputs: dict, output: str):
        """追加工具调用记录（仅审计用，不缓存——恢复历史时用不上）。"""
        record = {
            "type": "tool_call",
            "ts": int(time.time()),
            "session_id": session_id,
            "tool": tool_name,
            "input": inputs,
            "output": output[:500],   # 截断，避免大数据
        }
        await self._append_record(session_id, record, cache=False)

    async def _append_record(self, session_id: str, record: dict, cache: bool):
        """把一条记录追加到 JSONL 文件；如果是 message 记录，同时推进 Redis List 缓存。"""
        path = self._session_path(session_id)
        line = json.dumps(record, ensure_ascii=False)

        # 追加写入 JSONL 文件——这一步是唯一"必须成功"的部分
        async with aiofiles.open(path, "a", encoding="utf-8") as f:
            await f.write(line + "\n")

        # 同步更新 Redis List 缓存（尽力而为，失败不影响主流程）
        if self.redis and cache:
            try:
                cache_key = self._cache_key(session_id)
                await self.redis.rpush(cache_key, line)
                # 只保留最近 CACHE_MAX_RECORDS 条，防止 List 无限增长
                await self.redis.ltrim(cache_key, -CACHE_MAX_RECORDS, -1)
                await self.redis.expire(cache_key, CACHE_TTL_SECONDS)
            except Exception as e:
                print(f"[SessionStore] Redis 缓存更新失败（降级到纯文件模式）：{e}")

    async def load_messages(self, session_id: str, max_turns: int = 20) -> list[Message]:
        """
        加载对话历史，重建 messages 列表。

        优先从 Redis List 缓存读取（快，内存命中）；
        缓存未启用 / 未命中 / 请求轮数超出缓存窗口时，退回读 JSONL 文件（慢但保真），
        并把读到的最近 CACHE_MAX_TURNS 轮回填进 Redis，供下次命中。

        参数：
            max_turns - 最多加载几轮（防止历史太长超出 Token 限制）

        返回：
            Message 对象列表，可直接传给 Agentic Loop
        """
        raw_messages = None

        # 1. 尝试命中 Redis 缓存：只有当请求的轮数不超过缓存窗口时，缓存才可能是完整的
        if self.redis and max_turns <= CACHE_MAX_TURNS:
            try:
                cache_key = self._cache_key(session_id)
                cached_lines = await self.redis.lrange(cache_key, 0, -1)
                if cached_lines:
                    raw_messages = [json.loads(line) for line in cached_lines]
            except Exception as e:
                print(f"[SessionStore] Redis 缓存读取失败（降级到读文件）：{e}")

        # 2. 缓存未命中：读 JSONL 文件（唯一保真的数据源）
        if raw_messages is None:
            raw_messages = await self._load_from_file(session_id)
            # 把最近 CACHE_MAX_RECORDS 条回填进 Redis，供下次命中
            if self.redis and raw_messages:
                try:
                    cache_key = self._cache_key(session_id)
                    recent_for_cache = raw_messages[-CACHE_MAX_RECORDS:]
                    lines = [json.dumps(r, ensure_ascii=False) for r in recent_for_cache]
                    await self.redis.delete(cache_key)
                    if lines:
                        await self.redis.rpush(cache_key, *lines)
                        await self.redis.expire(cache_key, CACHE_TTL_SECONDS)
                except Exception as e:
                    print(f"[SessionStore] Redis 缓存回填失败（不影响本次返回结果）：{e}")

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

    async def _load_from_file(self, session_id: str) -> list[dict]:
        """从 JSONL 文件读取所有 message 类型的原始记录。"""
        path = self._session_path(session_id)
        if not path.exists():
            return []

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

        return raw_messages

    async def clear(self, session_id: str):
        """清除会话历史（用户请求 /clear 时调用）。"""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()

        if self.redis:
            try:
                await self.redis.delete(self._cache_key(session_id))
            except Exception:
                pass

        print(f"[SessionStore] 已清除会话：{session_id}")