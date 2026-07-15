# 依赖管理与 Docker 部署

> 这份文档说明 PolyCoder 的依赖是怎么管理的、Docker 部署时依赖从哪来、以及改依赖后要做什么。
> 目的是让你在增删依赖或排查部署问题时，知道该动哪个文件、为什么。

---

## 一、一句话总结

**`uv.lock` 是依赖的唯一事实来源。** 你在 `pyproject.toml` 里声明"想要什么"，
`uv lock` 把它解析成"确切装哪些版本"写进 `uv.lock`，Docker 构建时严格按 `uv.lock` 安装。
改了 `pyproject.toml` 就必须重新 `uv lock`，否则 Docker 构建会失败。

---

## 二、依赖是怎么确定的（完整链路）

```
pyproject.toml            你声明的依赖意图，如 "anthropic>=0.40.0"
      │  uv lock（解析依赖树、锁定确切版本+哈希）
      ▼
uv.lock                   ★ 依赖的真正来源：每个包的确切版本、哈希、传递依赖
      │  docker build 阶段：
      │    Dockerfile: COPY pyproject.toml uv.lock
      │    Dockerfile: RUN uv sync --frozen --no-dev
      ▼
镜像内的 .venv            严格按 uv.lock 安装，一个不多一个不少
      │  docker compose build → image: my-agent:latest
      ▼
运行阶段                  entrypoint.sh 用 `uv run` 调用这个 .venv 启动服务
```

**关键点**：决定容器里装哪些依赖的是 `uv.lock`，不是 `docker-compose.yml`，也不是 `requirements.txt`。

---

## 三、各文件的角色

| 文件 | 角色 | 增删依赖时是否要改 |
|------|------|------------------|
| `pyproject.toml` | 声明依赖意图（`dependencies = [...]`） | ✅ 改这里 |
| `uv.lock` | 锁定确切版本（构建实际依据） | ✅ 运行 `uv lock` 自动更新 |
| `Dockerfile` | 用 uv 基础镜像 + `uv sync --frozen` 装依赖 | ❌ 不写死包名，无需改 |
| `docker-compose.yml` | 编排服务（端口/卷/网络/依赖顺序） | ❌ 不碰依赖，无需改 |
| `entrypoint.sh` | 容器启动脚本（探活 Redis + 起 uvicorn） | ❌ 无需改 |
| `.dockerignore` | 排除不进镜像的文件 | ❌ 无需改 |
| `requirements.txt` | 仅文档/参考用，Docker 实际不用它 | 保持与 pyproject 一致即可 |

> **为什么 `requirements.txt` 不参与 Docker 构建**：本项目用 uv 管理依赖，Dockerfile 走
> `uv sync`（按 `uv.lock`），不执行 `pip install -r requirements.txt`。保留 `requirements.txt`
> 只是给不用 uv 的读者一个参考清单，改依赖时顺手同步即可，但它不是构建依据。

---

## 四、哪里引用了 uv

uv 只出现在构建/运行层，`docker-compose.yml` 里没有直接调 uv：

| 位置 | 引用 | 作用 |
|------|------|------|
| `Dockerfile` 第 4 行 | `FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim` | 基础镜像自带 uv + Python 3.14 |
| `Dockerfile` 第 21 行 | `RUN uv sync --frozen --no-install-project --no-dev` | 先只装第三方依赖（利用层缓存） |
| `Dockerfile` 第 27 行 | `RUN uv sync --frozen --no-dev` | 复制代码后把项目本身装进环境 |
| `entrypoint.sh` 第 14 行 | `uv run python -c "import redis..."` | 用 `.venv` 探活 Redis |
| `entrypoint.sh` 第 25 行 | `uv run uvicorn main:app ...` | 用 `.venv` 启动服务 |

`docker-compose.yml` 通过 `build: dockerfile: Dockerfile` 间接触发上述 uv 命令，自己不调 uv。

### `uv sync` 关键参数

- `--frozen`：**只按现有 `uv.lock` 安装，不重新解析、不修改锁文件**。这保证容器依赖和本地
  `uv.lock` 完全一致。副作用：如果 `uv.lock` 和 `pyproject.toml` 不一致，构建会**报错失败**
  （这正是改依赖后必须重新 `uv lock` 的原因）。
- `--no-install-project`：此步只装第三方依赖，不装项目本身（代码还没 COPY 进来）。用于层缓存：
  依赖没变时这层直接命中缓存，重建镜像很快。
- `--no-dev`：不装 dev 依赖组（如 pytest），生产镜像更精简。

---

## 五、增删依赖的标准流程

```bash
# 1. 增依赖（会自动更新 pyproject.toml 和 uv.lock）
uv add <包名>

# 或删依赖
uv remove <包名>

# 2. 如果是手动改了 pyproject.toml 的 dependencies，必须重新锁定：
uv lock

# 3.（可选）本地验证锁文件和 pyproject 一致、能正常安装：
uv sync --frozen --no-dev --dry-run     # 干运行，不真正改环境

# 4. 重建镜像验证部署：
docker compose build agent
```

> **常见坑**：只改了 `pyproject.toml`（比如手动删掉一行依赖）却忘了 `uv lock`。
> 本地 `uv run` 可能还正常（用旧 `.venv`），但 `docker build` 会在 `uv sync --frozen` 那步
> 因 lock 与 pyproject 不一致而**失败**。改依赖后务必跑一次 `uv lock`。

---

## 六、Docker 部署的服务组成

`docker compose up` 会起一组服务（见 `docker-compose.yml`）：

| 服务 | 镜像 | 作用 |
|------|------|------|
| `agent` | `my-agent:latest`（本地 Dockerfile 构建） | PolyCoder 主服务，端口 8002 |
| `redis` | `redis:7-alpine` | 会话缓存，agent 依赖它健康后才启动 |
| `prometheus` | `prom/prometheus:latest` | 指标抓取，端口 9090 |
| `grafana` | `grafana/grafana:latest` | 统一可视化（指标+链路+日志），端口 3000 |
| `tempo` | `grafana/tempo:latest` | 链路追踪存储，OTLP gRPC 端口 4317 |
| `loki` | `grafana/loki:latest` | 日志存储，端口 3100 |
| `promtail` | `grafana/promtail:latest` | 采集容器 stdout 推给 Loki |

只有 `agent` 是本地构建（走 Dockerfile + uv），其余都是官方镜像直接拉取。

### 环境配置：`.env.docker`

容器的运行时配置（LLM Provider、API Key、Redis 地址、WORKSPACE、OTEL 端点等）来自
`.env.docker`，通过 `docker-compose.yml` 的 `env_file` 注入——**不打进镜像**（`.dockerignore`
排除了 `.env*`），改配置无需重建镜像，重启容器即可生效。

### 数据持久化（卷映射）

- `./sessions:/app/sessions`：会话 JSONL 文件映射到主机，容器重建不丢失。
- `./workspace:/app/workspace`：工具/子 Agent 产出的文件映射到主机。
- `redis_data` / `prometheus_data` / `grafana_data` / `tempo_data` / `loki_data`：命名卷，各组件数据持久化。

---

## 七、常用命令速查

```bash
# 构建 agent 镜像（实走 uv sync --frozen，可验证依赖是否正确）
docker compose build agent

# 启动全部服务
docker compose up -d

# 只看 agent 日志
docker compose logs -f agent

# 改了依赖后：重新锁定 + 重建
uv lock && docker compose build agent

# 停止并清理（保留数据卷）
docker compose down

# 停止并删除数据卷（谨慎：会清空 Redis/Grafana 等数据）
docker compose down -v
```

---

## 八、和第 13 章的关系

第 13 章《容器化部署》讲的是 Dockerfile、docker-compose、可观测性编排的**从零搭建过程**。
本文档是它的**速查补充**，聚焦"依赖如何流动、改依赖动哪里"这条主线，便于日常维护时快速定位。
两者不冲突：第 13 章教你怎么搭，本文档帮你搭好之后怎么维护依赖。
