# 内部 HTTP 客户端规范

## 统一请求封装
- 所有对外请求必须通过 `core.http.HttpClient`，禁止直接使用 requests / httpx
- `HttpClient` 已内置超时（默认 10s）、重试（指数退避，最多 3 次）、链路追踪 header
- 用法：`await HttpClient.request(method, url, *, json=None, headers=None)`

## 认证
- 内部服务调用统一走 `HttpClient`，会自动从环境变量注入 `X-Internal-Token`
- 调用第三方 API 时，在 headers 里显式传 `Authorization`，不要写死在代码里

## 统一响应结构
所有内部接口返回 JSON，结构固定：
```json
{"code": 0, "message": "ok", "data": {...}}
```
- `code == 0` 表示成功，`data` 是业务数据
- `code != 0` 表示失败，`message` 是错误描述，`data` 为 null
- 业务代码应先判断 `code`，非 0 时抛 `ApiError(code, message)`

## 分页约定
- 列表接口统一用 `page`（从 1 开始）和 `page_size`（默认 20，最大 100）两个参数
- 返回的 `data` 里含 `items` 列表和 `total` 总数
