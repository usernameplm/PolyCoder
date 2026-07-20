# Python 编码规范

## 命名
- 函数 / 变量：snake_case；类：PascalCase；常量：UPPER_SNAKE_CASE
- 私有成员以单下划线开头（`_internal`）；模块级私有函数同样以 `_` 开头

## 类型注解
- 所有公开函数必须写完整类型注解（参数 + 返回值）
- 优先用 `list[str]`、`dict[str, int]` 等内置泛型，不用 `typing.List`

## 异步
- IO 操作（网络、文件、DB）一律用 async/await，禁止在协程里做阻塞调用
- 并发多个协程用 `asyncio.gather`，不要手动创建裸 Task 不管理

## 异常处理
- 只在系统边界（入口、外部调用）捕获异常，内部逻辑让异常自然向上抛
- 自定义异常继承项目基类 `AppError`，禁止裸 `raise Exception(...)`

# 统一错误码表
| code | 常量名 | 含义 |
|---|---|---|
| 0 | OK | 成功 |
| 1001 | ERR_INVALID_PARAM | 参数校验失败 |
| 1002 | ERR_UNAUTHORIZED | 未认证或 token 失效 |
| 1003 | ERR_FORBIDDEN | 无权限 |
| 2001 | ERR_NOT_FOUND | 资源不存在 |
| 5001 | ERR_INTERNAL | 服务内部错误 |
| 5002 | ERR_UPSTREAM_TIMEOUT | 上游服务超时 |
