# 编码规范 v2.1

## 命名规范

- 变量名使用 snake_case，例如 user_name、total_count
- 类名使用 PascalCase，例如 HttpClient、UserManager
- 常量使用 UPPER_SNAKE_CASE，例如 MAX_RETRY_COUNT、DEFAULT_TIMEOUT
- 私有属性以单下划线开头，例如 self._cache、self._client

## HTTP 请求规范

所有对外 HTTP 请求必须通过 HttpClient 封装类发送，禁止直接使用 requests/httpx：
- 自动添加 Trace ID 到请求头 X-Trace-Id
- 超时默认 30 秒，可通过 timeout 参数覆盖
- 所有非 2xx 响应自动转为 HttpClientError 异常

示例：
    client = HttpClient()
    resp = await client.get("https://api.example.com/data")

## 异步规范

- 所有 IO 操作使用 async/await，禁止在协程中调用同步阻塞函数
- 并发多个协程使用 asyncio.gather()，注意设置 return_exceptions=True
- 数据库连接池使用 asyncpg，Redis 使用 aioredis
- 长时间任务使用 asyncio.wait_for() 设置超时
