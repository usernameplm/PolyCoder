# persistence/redis_client.py
"""
创建 Redis 异步客户端。所有需要用 Redis 的模块（Blackboard、SessionStore）
都从这里拿同一个客户端实例，不要各自建各自的连接。
"""
import os
import redis.asyncio as redis


def create_redis_client() -> redis.Redis:
    """
    从环境变量读取连接信息（对应附录 B 的 REDIS_HOST / REDIS_PORT / REDIS_PASSWORD）。
    """
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        decode_responses=True,   # 让 Redis 返回的数据直接是 str，不是 bytes
    )