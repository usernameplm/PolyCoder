---
name: env-config
description: 本项目配置管理规范——.env + Pydantic Settings 联动流程
triggers:
  - 环境变量
  - 配置
  - .env
  - settings
  - config
  - Settings
  - Pydantic
---

## 本项目的配置机制

```
.env 文件（实际值）
    ↓ 被 Pydantic 自动读取
core/config.py → Settings 类（声明字段 + 类型 + 默认值）
    ↓ 被业务代码引用
from core.config import settings
settings.redis_host  # "localhost"
```

## ⚠️ 已知陷阱（本项目踩过）

Pydantic Settings 默认 **`extra = "forbid"`**——如果 `.env` 里有字段但 `Settings`
类没有声明，启动直接报错：

```
pydantic_core._pydantic_core.ValidationError:
  Extra inputs are not permitted [type=extra_forbidden]
```

**解决方案：新增环境变量必须同时改两处**（缺一个就炸）。

## 新增配置项的标准流程

每新增一个配置项，必须同步修改：

1. **`.env`**：加值（带注释）
   ```bash
   # Redis 连接配置
   REDIS_HOST=localhost
   REDIS_PORT=6379
   ```

2. **`core/config.py`**：加字段（含类型 + 默认值）
   ```python
   class Settings(BaseSettings):
       redis_host: str = "localhost"
       redis_port: int = 6379
   ```

## 命名规范

- `.env` 里全大写：`REDIS_HOST`、`LLM_PROVIDER`
- `Settings` 类里蛇形小写：`redis_host`、`llm_provider`
- Pydantic 自动映射（大小写不敏感）

## 默认值原则

- 本地能跑的默认值（`localhost`、`6379`、`8002`）
- 密钥默认空字符串 `""`：启动不报错，调用时才报错
- 禁止把生产密钥写进代码或 `.env` 模板
