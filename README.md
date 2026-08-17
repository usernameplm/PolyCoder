# PolyCoder

一个多模型、多 Agent 的智能编程助手。**自实现 Agentic Loop**（不依赖 claude-agent-sdk 黑箱），
通过一行 `.env` 配置即可在 Anthropic / OpenAI / Gemini / Ollama / DeepSeek 之间切换后端；
内置 Coordinator（规划-分发-聚合）与 Swarm（白板任务队列）两套多 Agent 编排架构，
配套本地 RAG 知识库、Skills 系统、会话持久化、飞书机器人与完整可观测性栈。

> 设计文档见 `docs/PolyCoder/Multi-Agent项目复现完整指南(v2-完整版).md`。

---

## 核心特性

| 能力 | 说明 |
|------|------|
| **多 Provider 统一接入** | 改一行 `LLM_PROVIDER` 即可切换 Anthropic / OpenAI-compat（含 Ollama、DeepSeek、vLLM、Azure）/ Gemini，无需改代码、无 Node.js 依赖 |
| **自实现 Agentic Loop** | `while` 主循环状态机，工具调用检测 → 并行执行 → 结果回填，每一轮状态可读可调试 |
| **Coordinator 编排** | 主 Agent 只负责规划任务 DAG、分发给专家子 Agent、聚合结果（`/ask`、CLI 走此架构） |
| **Swarm 编排** | 常驻专家 Agent 通过 Blackboard（任务白板）认领并执行任务，支持任务自动派生（`/swarm/ask`） |
| **专家子 Agent** | code_writer / code_reviewer / debugger / test_writer，各自带独立 system prompt 与工具集 |
| **本地 RAG 知识库** | BM25（稀疏）+ 向量（bge-small-zh-v1.5 + Qdrant）双路召回 + RRF 融合；支持 txt/md/pdf/图片多模态解析 |
| **Skills 系统** | 技能索引常驻 system prompt，LLM 在循环中用 `get_skill_guide` 工具按需加载 SKILL.md 全文 |
| **会话持久化** | JSONL 落盘 + Redis 缓存最近历史，支持多轮记忆与断点续传 |
| **工作目录沙箱** | 所有工具与子 Agent 的文件操作限定在 `WORKSPACE` 内，禁止 `../` / 绝对路径穿越 |
| **可观测性** | 结构化日志（structlog）+ Prometheus 指标 + OpenTelemetry 链路追踪，Grafana 一处看齐 |
| **飞书机器人** | WebSocket 长连接接入飞书，群聊/私聊直接对话 |
| **容器化部署** | docker-compose 一键拉起 Agent + Redis + Prometheus + Grafana + Tempo + Loki + Promtail |

---

## 架构总览

```
                         ┌─────────────── FastAPI (main.py) ───────────────┐
                         │  POST /ask         GET /ask/stream (SSE)         │
                         │  POST /swarm/ask   GET /swarm/tasks/{id}         │
                         └──────┬────────────────────────────┬─────────────┘
                                │                            │
                  ┌─────────────▼──────────┐    ┌────────────▼─────────────┐
                  │   Coordinator 架构      │    │       Swarm 架构          │
                  │  规划 → 分发 → 聚合     │    │  Blackboard（任务白板）   │
                  │  (coordinator/)         │    │  常驻 Agent 认领执行      │
                  └─────────────┬──────────┘    └────────────┬─────────────┘
                                │                            │
                  ┌─────────────▼────────────────────────────▼─────────────┐
                  │  专家子 Agent：code_writer / code_reviewer /            │
                  │  debugger / test_writer   (sub_agents/, swarm/)         │
                  └─────────────┬───────────────────────────────────────────┘
                                │
                  ┌─────────────▼──────────┐   每个子 Agent 内部驱动：
                  │  Agentic Loop           │   agent_core/loop.py
                  │  (agent_core/)          │   工具并行执行 + 上下文压缩
                  └─────────────┬──────────┘
                                │
       ┌────────────────────────┼────────────────────────────┐
       │                        │                            │
┌──────▼───────┐   ┌───────────▼──────────┐    ┌─────────────▼───────────┐
│ Provider 层  │   │  工具层 (tools/)      │    │  Skills (skills/)        │
│ (providers/) │   │  读写/搜索/执行/RAG   │    │  get_skill_guide 按需加载│
│ 统一抽象     │   │  文档多模态加载       │    │                          │
└──────────────┘   └──────────────────────┘    └──────────────────────────┘
```

### 模块速览

| 目录 | 职责 |
|------|------|
| `core/` | 全局配置（`config.py`）与工作目录沙箱（`workspace.py`） |
| `providers/` | Provider 统一抽象层与路由（Anthropic / OpenAI / Gemini + `router.py`） |
| `agent_core/` | Agentic Loop 状态机（`loop.py` / `state.py` / `executor.py` / `context.py`） |
| `coordinator/` | Coordinator 架构：`planner.py`（规划）+ `dispatcher.py`（分发）+ `agent.py`（编排） |
| `swarm/` | Swarm 架构：`blackboard.py`（任务白板）+ 各 SwarmAgent + `task_types.py` |
| `sub_agents/` | 专家子 Agent（code_writer / code_reviewer / debugger / test_writer） |
| `tools/` | 内置工具（`builtin/`）与文档加载器（`document_loaders/`，pdf/图片/文本切分） |
| `skills/` | Skills 系统：`loader.py` + `skill_tool.py` + 各 `*.md` 技能文档 |
| `knowledge_base/` | RAG 检索的知识库文档目录（api_spec.md / coding_style.md 等） |
| `persistence/` | 会话持久化：`session_store.py`（JSONL）+ `redis_client.py` |
| `observability/` | 日志 / 指标 / 链路追踪（`logging.py` / `metrics.py` / `tracing.py`） |
| `feishu/` | 飞书 WebSocket 机器人接入 |

---

## 快速开始

### 环境要求

- Python **3.14+**（本项目用 `uv` 管理，见 `.python-version` / `pyproject.toml`）
- Redis 7+（可选，会话持久化与 Swarm 白板备份用，未启动有降级处理）
- Docker 24+（仅容器化部署用）

### 本地运行

```bash
# 1. 安装依赖（推荐 uv）
uv sync
#   或 pip install -r requirements.txt

# 2. 配置 .env，至少填一个 Provider 的 Key
cp .env.docker .env   # 参考模板，按需修改

# 3. 启动服务（默认端口 8002）
uv run uvicorn main:app --host 0.0.0.0 --port 8002 --reload
#   或 python main.py
```

启动后：

- API 文档：<http://localhost:8002/docs>
- 前端测试页：<http://localhost:8002/>
- 健康检查：<http://localhost:8002/health>

### 命令行交互

```bash
uv run python cli.py
#   /usage 查看累计 Token 用量，Ctrl+C 退出
```

### 验证 RAG 知识库（无需调用对话 LLM）

```bash
uv run python demo_kb.py
#   首次运行会自动下载 bge-small-zh-v1.5（~100MB），用 :memory: 内存向量库演示语义检索
```

---

## 配置说明（`.env`）

```dotenv
# 选择 Provider（任选其一激活）
LLM_PROVIDER=anthropic          # anthropic | openai | gemini | ollama | deepseek | azure

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-4-6

# OpenAI 兼容（Ollama / DeepSeek / vLLM）
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1   # Ollama: http://localhost:11434/v1
OPENAI_MODEL=gpt-4o

# Gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash

# 视觉专用 Provider（可选，留空则回退到对话 Provider）
VISION_PROVIDER=
VISION_MODEL=

# 工作目录沙箱：所有工具/子 Agent 的文件操作限定在此目录内
WORKSPACE=./workspace

# Redis（可选）
REDIS_HOST=localhost
REDIS_PORT=6379

# Qdrant（RAG 知识库向量库）：本地跑用 localhost；docker-compose 部署要改成服务名
# QDRANT_URL=http://qdrant:6333
QDRANT_URL=http://localhost:6333

# 可观测性：OTLP 导出地址（Tempo/Jaeger），不填则不导出
OTEL_EXPORTER_OTLP_ENDPOINT=

# 飞书机器人（可选，两者都填才启动）
FEISHU_APP_ID=
FEISHU_APP_SECRET=

APP_PORT=8002
```

---

## API 接口

| 方法 & 路径 | 架构 | 说明 |
|-------------|------|------|
| `POST /ask` | Coordinator | 完整响应，支持 `session_id` 多轮记忆 |
| `GET /ask/stream` | Coordinator | SSE 流式，按子任务完成顺序推送 |
| `POST /session/clear` | — | 清除指定会话历史（JSONL + Redis） |
| `POST /swarm/ask` | Swarm | 提交任务到白板并等结果（`task_type`: code_review / debug / test_write） |
| `GET /swarm/tasks/{id}` | Swarm | 补查任务最新状态（配合超时场景） |
| `GET /swarm/tasks` | Swarm | 白板整体状态摘要 |
| `POST /swarm/tasks/{id}/apply` | Swarm | 把任务结果中的代码块落地写入 `WORKSPACE` |
| `GET /health` | — | 健康检查 |
| `GET /metrics` | — | Prometheus 指标端点 |

**示例：**

```bash
# 完整响应
curl -X POST http://localhost:8002/ask \
  -H 'Content-Type: application/json' \
  -d '{"question": "写一个快速排序函数", "session_id": "demo"}'

# 流式响应
curl -N "http://localhost:8002/ask/stream?question=请写一首短诗"

# Swarm 代码审查
curl -X POST http://localhost:8002/swarm/ask \
  -H 'Content-Type: application/json' \
  -d '{"task_type": "code_review", "payload": {"code": "def f(): ...", "file": "a.py"}}'
```

---

## RAG 知识库

`tools/builtin/knowledge_base.py` 实现混合检索：

- **稠密召回**：`sentence-transformers` 加载 `BAAI/bge-small-zh-v1.5`（512 维、中文强、CPU 可跑、离线免费），存入 Qdrant（cosine / HNSW）。
- **稀疏召回**：`rank-bm25` 进程内现建 BM25 索引，擅长精确术语 / 型号 / 错误码。
- **融合排序**：RRF（Reciprocal Rank Fusion，k=60）按名次融合两路结果，无需归一化不可比的分数。
- **多模态解析**：txt/md 语义切分；pdf 用 docling 解析文本/表格/图片（表格转 Markdown，图片并发调用视觉 LLM 描述）；图片直接调用视觉 LLM 描述。

把文档放进 `knowledge_base/` 目录即可被检索。

---

## 容器化部署

```bash
# 编辑 .env.docker 配置 Provider / 飞书 / Redis
docker compose up -d --build
```

一键拉起以下服务：

| 服务 | 端口 | 用途 |
|------|------|------|
| agent | 8002 | 主服务 |
| redis | 6379 | 会话 / 白板持久化 |
| qdrant | 6333 / 6334 | RAG 知识库向量存储 |
| prometheus | 9090 | 指标采集 |
| grafana | 3000 | 可视化（默认 admin/admin，指标+链路+日志一处看齐） |
| tempo | 4317 / 3200 | 链路追踪存储 |
| loki | 3100 | 日志存储 |
| promtail | — | 日志采集 |

---

## 测试

```bash
uv run pytest          # asyncio_mode = auto，见 pyproject.toml
```

---

## 技术栈

FastAPI · Pydantic · structlog · OpenTelemetry · Prometheus · Redis · Qdrant ·
sentence-transformers · rank-bm25 · docling · PyMuPDF · lark-oapi · tenacity
