# Multi-Agent 项目复现指南 · v2（多模型统一接入版）

> 面向有一定 Python 基础的开发者。核心变化：去掉 claude-agent-sdk 黑箱，
> 自实现 Agentic Loop，同时支持 Anthropic / OpenAI-compat / Gemini / Bedrock 等多 Provider。
> 每一阶段都是可独立运行的完整程序，在上一阶段基础上增加一个新能力。

> **📎 2026 行业趋势对标增补（基于 2026-07-17 的评估）**：在不改动第 1-14 章已实现主体的前提下，
> 本版新增了 4 处训练无关的进阶改进，全部与"RL 只训本地小模型"的约束兼容：
> - **15.14** CONCAT 动态分组与稀疏通信裁剪——按相关性裁剪 Coordinator 注入的上下文、对 Swarm
>   相似任务聚类去重（对应 2026 趋势"稀疏通信"）；
> - **16.13** MAST 失败结构化分类——给指标加 `failure_category` 三分类字段；
> - **16.14** 评测技能化——把测试模式沉淀为 `agent-eval-authoring` Skill（对应 EvalAgent 研究）；
> - **第 18 章（附录）** 收录本该落在第 6、11 章的改进：**18.1** Coordinator DAG 局部重新规划、
>   **18.2** 按失败类别的 Grafana 聚合查询、**18.3** LLM 输出层安全护栏的已知空白。
>   第 6、11 章正文只在对应位置留了一行"进阶备注"指引，未改动其编号与已实现内容。

---

## 阶段划分总览

```
基础层（0-2）：环境 + 最小 Agent + Web 接口
  阶段 0  → 准备工作
  阶段 1  → 最小 Agent（直接调 API，不依赖任何 SDK 框架）
  阶段 2  → Web 接口（流式 SSE）

Provider 层（3-4）：★ 新增，取代 claude-agent-sdk
  阶段 3  → 统一 Provider 抽象层（Anthropic / OpenAI-compat / Gemini）
  阶段 4  → 自实现 Agentic Loop（query loop 状态机）

工具层（5）：
  阶段 5  → 工具调用（BaseTool + MCP + 异步工具）

编排层（6-7）：
  阶段 6  → Coordinator 模式（主 Agent 只编排）
  阶段 7  → Swarm 模式（持久化团队 + 任务白板）

工程层（8-10）：
  阶段 8  → 上下文管理（Token 预算 + 压缩 + 缓存）
  阶段 9  → Skills 系统（索引表常驻 + LLM 用 get_skill_guide 工具按需加载 SKILL.md）
  阶段 10 → 会话持久化（JSONL + Redis 缓存 + 断点续传）

集成层（11-14）：
  阶段 11 → 可观测性（结构化日志 + Prometheus + 链路追踪）
  阶段 12 → 飞书机器人
  阶段 13 → 容器化部署
  阶段 14 → 定制化指南
```

---

## 与前版大纲的核心差异

| 维度 | 前版（v1） | 本版（v2） |
|------|-----------|-----------|
| Agent 框架 | claude-agent-sdk（黑箱进程） | 自实现 Agentic Loop（完全透明） |
| Provider 支持 | 只支持 Claude | Anthropic / OpenAI / Ollama / DeepSeek / Gemini |
| 切换模型 | 改 Python 代码 | 改一行 .env（`LLM_PROVIDER=openai`） |
| Node.js 依赖 | 必须（claude-agent-sdk 底层） | 完全不需要 |
| 工具调用机制 | SDK 内部处理（不可见） | 自己的 ToolExecutor + ToolRegistry |
| Loop 可观测性 | 无法调试 Loop 内部 | 每轮状态完全可见可调试 |
| 本地模型支持 | 不支持 | 一行配置接入 Ollama |

---

## 第 0 章：准备工作

### 0.1 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.11+ | 核心运行环境 |
| Redis | 7.0+（可选） | 第 7 章（白板持久化）、第 10 章（会话持久化）会用到，两处都有降级处理，不装也能跑 |
| Docker | 24.0+（可选） | 阶段 13 才用到 |
| Node.js | **不需要** | v2 已完全去掉此依赖 |

### 0.2 依赖清单

```txt
# requirements.txt

# HTTP 客户端（直接调 API）
anthropic>=0.40.0           # Anthropic 官方 SDK（原生流式支持）
openai>=1.40.0              # OpenAI SDK（也兼容 Ollama/DeepSeek/vLLM）
google-generativeai>=0.8.0  # Gemini SDK

# Web 框架
fastapi>=0.111.0
uvicorn>=0.30.6

# 数据处理
pydantic>=2.0.0
pydantic-settings>=2.0.0
aiofiles>=24.0.0

# 可观测性
structlog>=24.0.0
opentelemetry-sdk>=1.25.0
prometheus-client>=0.21.0

# 存储
redis>=5.0.0                # 5.x 起自带 redis.asyncio，异步能力已内置，不需要再单独装 aioredis

# 飞书
lark-oapi>=1.6.0

# 辅助
python-dotenv>=1.0.0
httpx>=0.27.0
tenacity>=9.0.0             # 重试（指数退避）
```

> 第 9 章的 Skills 系统改为"LLM 通过 `get_skill_guide` 工具按需加载"，不再做 TF-IDF 分词搜索，
> 因此**无需 jieba**。（旧版本这里列过 `jieba`，现已移除。）

### 0.3 .env 配置（多 Provider 支持）

```dotenv
# 选择 Provider（任选其一激活）
LLM_PROVIDER=anthropic       # anthropic | openai | gemini | bedrock

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...

# OpenAI / 兼容（Ollama, DeepSeek, vLLM 等）
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1  # 改成 Ollama 地址就接入本地模型
OPENAI_MODEL=gpt-4o

# Gemini
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash

# 通用
LLM_MODEL=claude-sonnet-4-6
APP_PORT=8002
```

### 0.4 目录骨架初始化

```
my-agent/
├── .env
├── .gitignore
├── requirements.txt
└── core/
    ├── __init__.py
    └── config.py       ← 统一读取所有配置，一处修改全局生效
```

```python
# core/config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    llm_provider: str = "anthropic"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    llm_model: str = "claude-sonnet-4-6"
    app_port: int = 8002

    class Config:
        env_file = ".env"

settings = Settings()
```

### 0.5 本章检查清单

- [ ] Python 3.11+ 安装完成（`python --version` 验证）
- [ ] 虚拟环境创建并激活（`python -m venv .venv && source .venv/bin/activate`）
- [ ] 所有依赖安装完成（`pip install -r requirements.txt`）
- [ ] `.env` 文件配置完成，至少填写一个 Provider 的 Key
- [ ] `core/config.py` 能正常导入（`python -c "from core.config import settings; print(settings.llm_provider)"`）

---

# 第 0 章：准备工作

> 本章不写任何业务代码。目标只有一个：让你的电脑具备运行后续所有章节所需的全部条件。
> 每一步都有验证命令，做完一步就运行一下，确认没问题再继续。

---

## 0.1 环境要求

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.11+ | 核心运行环境 |
| Redis | 7.0+（可选） | 第 7 章（白板持久化）、第 10 章（会话持久化）会用到，两处都有降级处理，不装也能跑 |
| Docker | 24.0+（可选） | 第 13 章才用到 |
| Node.js | **不需要** | v2 已完全去掉此依赖 |

与 v1 最大的变化：**不再依赖 Node.js**。v1 通过 `claude-agent-sdk` 在后台启动一个 Node.js 进程，你的 Python 代码通过 stdin/stdout 和它通信——这是个不透明的黑箱，出问题很难排查。v2 改为直接调用各家 LLM 的 Python SDK，所有逻辑都在你自己的进程里，随时可以加断点调试。

---

## 0.2 依赖清单详解

在 `my-agent` 文件夹下创建 `requirements.txt`：

```txt
# requirements.txt

# ── LLM 客户端 ───────────────────────────────────────────────────
anthropic>=0.40.0           # Anthropic 官方 Python SDK（Claude）
openai>=1.40.0              # OpenAI SDK，也兼容 Ollama/DeepSeek/vLLM 等
google-generativeai>=0.8.0  # Gemini SDK

# ── Web 框架 ──────────────────────────────────────────────────────
fastapi>=0.111.0            # 高性能 Python Web 框架
uvicorn>=0.30.6             # ASGI 服务器（运行 FastAPI 用）

# ── 数据处理 ──────────────────────────────────────────────────────
pydantic>=2.0.0             # 数据验证与序列化
pydantic-settings>=2.0.0    # 从 .env 读取配置
aiofiles>=24.0.0            # 异步文件读写（会话持久化用）

# ── 可观测性 ──────────────────────────────────────────────────────
structlog>=24.0.0           # 结构化日志（JSON 格式，便于日志聚合）
opentelemetry-sdk>=1.25.0   # OpenTelemetry 链路追踪
prometheus-client>=0.21.0   # Prometheus 指标暴露
opentelemetry-exporter-otlp-proto-grpc>=1.25.0   # 可选：不装也能跑，只是 Span 不会导出到 Jaeger/Tempo（tracing.py 里 ImportError 会被吞掉，打印一行提示）

# ── 存储 ──────────────────────────────────────────────────────────
redis>=5.0.0                # Redis 客户端（5.x 起自带 redis.asyncio 异步支持，aioredis 已停止维护，不需要再装）

# ── 飞书集成 ──────────────────────────────────────────────────────
lark-oapi>=1.6.0            # 飞书开放平台官方 Python SDK

# ── 辅助 ──────────────────────────────────────────────────────────
python-dotenv>=1.0.0        # 从 .env 文件加载环境变量
httpx>=0.27.0               # 异步 HTTP 客户端（调用外部 API 用）
tenacity>=9.0.0             # 重试装饰器（指数退避，处理临时网络错误）
```

安装命令（先激活虚拟环境）：

```bash
# 创建并激活虚拟环境
python -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows（命令提示符）
.venv\Scripts\activate.bat

# 安装所有依赖
pip install -r requirements.txt

# 如果速度慢，换清华镜像：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 💡 **什么是虚拟环境？**
> 把每个 Python 项目想象成一间独立的房间。虚拟环境就是这间房间——里面安装的包只属于这个项目，不会和其他项目互相干扰。每次打开新终端都要重新"进入房间"（激活虚拟环境）。

---

## 0.3 多 Provider 的 .env 配置

在 `my-agent` 文件夹下创建 `.env` 文件：

```dotenv
# .env — 请勿提交到 Git！

# ── 选择 LLM Provider（任选其一激活）──────────────────────────────
LLM_PROVIDER=anthropic       # anthropic | openai | gemini

# ── Anthropic（推荐，原生支持 Claude）────────────────────────────
ANTHROPIC_API_KEY=sk-ant-...

# ── OpenAI / 兼容服务（GPT-4、Ollama、DeepSeek 等）──────────────
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
# Ollama（本地模型）：改为 http://localhost:11434/v1
# DeepSeek：改为 https://api.deepseek.com/v1
OPENAI_MODEL=gpt-4o

# ── Gemini ────────────────────────────────────────────────────────
GEMINI_API_KEY=AIza...
GEMINI_MODEL=gemini-2.0-flash

# ── 通用配置 ──────────────────────────────────────────────────────
LLM_MODEL=claude-sonnet-4-6  # Anthropic Provider 使用的默认模型
APP_PORT=8002                 # 服务监听端口

# ── 工作目录（所有工具和子 Agent 的文件操作都限定在此目录内）──────────
WORKSPACE=.                   # 默认当前目录；生产建议指向一个专门的工作区，如 /data/workspace
```

> **为什么要有统一工作目录？** Agent 的工具会读写文件、执行代码。如果每个工具各自决定在哪读写，行为就不可控——尤其飞书/HTTP 是外部入口，用户可能诱导模型写到 `/etc` 这类系统路径。本项目把「工作目录」作为**唯一真相来源**（`WORKSPACE` 环境变量）：所有工具（`read_file`/`write_file`/`search_code`/`list_dir`/`run_python`）和所有子 Agent 的文件操作都限定在这个目录内，且禁止用 `../` 或绝对路径穿越到目录外。详见 0.4 的 `core/workspace.py` 和第 6 章工具实现。

**关于 `.gitignore`**：

```bash
# 在项目根目录创建 .gitignore，防止 Key 泄露
cat > .gitignore << 'EOF'
.env
.venv/
__pycache__/
*.pyc
sessions/
logs/
EOF
```

---

## 0.4 目录骨架初始化

创建核心配置模块：

```bash
mkdir -p core
touch core/__init__.py
```

创建 `core/config.py`：

```python
# core/config.py
# 统一读取所有配置——整个项目只从这里拿配置，修改 .env 立刻生效。

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # LLM Provider 选择
    llm_provider: str = "anthropic"

    # Anthropic
    anthropic_api_key: str = ""

    # OpenAI / 兼容服务
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o"

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # 通用
    llm_model: str = "claude-sonnet-4-6"
    app_port: int = 8002

    # 工作目录：所有工具（读/写/搜索/执行）和子 Agent 的文件操作都限定在此目录内，
    # 且禁止路径穿越到目录外。启动时用环境变量 WORKSPACE 覆盖，默认当前目录。
    workspace: str = "."

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

# 全局单例——所有模块都 from core.config import settings 来使用
settings = Settings()
```

**创建 `core/workspace.py`（统一工作目录 + 路径沙箱）：**

这是「工作目录」这一概念的落地实现。它从 `settings.workspace` 读出唯一的工作目录，并提供带**路径穿越校验**的安全写入函数——所有工具都调它，而不是各自 `Path(...)` 直接读写：

```python
# core/workspace.py
"""
全项目统一的工作目录（工作区）。

设计目标：所有工具（read_file / write_file / search_code / list_dir / run_python）
和所有子 Agent 的文件操作，都限定在同一个工作目录内，且禁止路径穿越到目录外。

工作目录的唯一来源是 core.config.settings.workspace（环境变量 WORKSPACE，默认当前目录）。
启动服务时用 WORKSPACE=/path uvicorn main:app 指定，一处设置，全局生效。
"""
from pathlib import Path
from core.config import settings


class PathTraversalError(ValueError):
    """请求路径解析后跑出了工作目录范围。"""


def get_workspace() -> Path:
    """返回工作目录的绝对路径（唯一来源：settings.workspace）。"""
    return Path(settings.workspace).resolve()


def resolve_safe_path(raw_path: str, workspace: Path | None = None) -> Path:
    """把相对路径解析为工作目录内的绝对路径；跑出工作目录则抛 PathTraversalError。"""
    workspace = (workspace or get_workspace()).resolve()
    target = (workspace / raw_path).resolve()
    if target != workspace and workspace not in target.parents:
        raise PathTraversalError(f"路径 '{raw_path}' 解析后跑出了工作目录范围，拒绝访问")
    return target


def write_text_sandboxed(raw_path: str, content: str, workspace: Path | None = None) -> Path:
    """在沙箱校验通过后写盘，自动创建缺失的父目录。返回写入的绝对路径。"""
    target = resolve_safe_path(raw_path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
```

> **为什么用 `workspace not in target.parents` 而不是字符串 `startswith`？** 字符串前缀判断会把 `/data/workspace-evil` 误判为在 `/data/workspace` 内（前缀相同但不是子目录）。`Path.parents` 是基于路径层级的判断，`.resolve()` 又会先把 `../` 展开成真实路径，两者配合才能严丝合缝地拦住穿越。

**验证配置加载正常：**

```bash
python -c "from core.config import settings; print('Provider:', settings.llm_provider)"
# 应该输出：Provider: anthropic

# 验证工作目录沙箱（穿越应被拦截）
WORKSPACE=/tmp/ws python -c "
from core.workspace import resolve_safe_path, PathTraversalError
print('正常路径:', resolve_safe_path('a/b.py'))
try:
    resolve_safe_path('../evil.py')
except PathTraversalError as e:
    print('穿越已拦截:', e)
"
```

---

## 0.5 本章检查清单

```
□ Python 3.11+ 已安装
  验证：python --version  →  显示 Python 3.11.x 或更高

□ 虚拟环境已创建并激活
  验证：终端提示符前有 (.venv) 标记

□ 所有依赖安装完成
  验证：pip show anthropic  →  显示版本信息（不报错）

□ .env 文件已配置，至少填写一个 Provider 的 Key

□ core/config.py 能正常导入
  验证：python -c "from core.config import settings; print(settings.llm_provider)"

□ .gitignore 文件已创建，包含 .env
```

---

# 第 1 章：阶段 1 —— 最小可运行 Agent（直接调 API）

> **本章目标**：不依赖任何 Agent 框架，直接调用 LLM SDK，理解最原始的"问 → 答"。
> 这是整个项目的基石。搞懂这里，后面所有复杂机制都是在这上面加功能。

---

## 1.1 目录结构

```
my-agent/
├── .env
├── core/
│   ├── __init__.py
│   └── config.py
├── agent.py    ← 直接调用 Anthropic SDK，核心约 20 行
└── cli.py      ← 命令行交互入口
```

---

## 1.2 核心概念讲解

### 1.2.1 为什么不用现成的 Agent 框架？

市面上有很多 Agent 框架（LangChain、claude-agent-sdk 等），它们封装了很多细节。但封装越多，出问题时越难排查。

v2 的思路：**先理解最底层的原理，再按需加功能**。

```
框架方式（v1）:
你的代码 → claude-agent-sdk → Node.js 子进程 → API → 结果
                              （黑箱，无法调试）

直接调用方式（v2）:
你的代码 → anthropic.messages.create() → API → 结果
          （完全透明，随时加断点）
```

### 1.2.2 消息格式：role + content

Claude API 使用对话消息列表来维护上下文。理解这个格式是一切的基础：

```python
messages = [
    {"role": "user",      "content": "什么是递归？"},
    {"role": "assistant", "content": "递归是函数调用自身的编程技术..."},
    {"role": "user",      "content": "能给个例子吗？"},
]
```

规则：
- `role` 只有 `user` 和 `assistant` 两种，**必须交替出现**
- 第一条必须是 `user`
- `system` 是单独的参数，不在 `messages` 列表里

**类比**：就像 QQ 聊天记录——你说一句，对方说一句，记录按时间顺序排列。

### 1.2.3 流式响应 vs 完整响应

| 对比项 | 完整响应（非流式） | 流式响应 |
|--------|-----------------|---------|
| 等待方式 | 等所有内容生成完才返回 | 生成一点返回一点 |
| 用户体验 | 等待时界面无变化 | 像打字机一样逐渐出现 |
| 适用场景 | 后端批处理、API 调用 | 前端对话框、CLI 实时输出 |

本章先用**完整响应**（简单），第 2 章再加流式接口。

### 1.2.4 Token 和计费

Claude 按 Token 计费。Token 大约是单词或词组的片段（中文约 1-2 字一个 Token，英文约 3-4 字母一个 Token）。

每次调用 API 后，响应里的 `usage` 对象告诉你消耗了多少 Token：

```python
message.usage.input_tokens    # 你发送的 Token 数（问题 + 历史 + 系统提示词）
message.usage.output_tokens   # 模型生成的 Token 数（回答）
```

费用 = 输入 Token × 输入单价 + 输出 Token × 输出单价。关注 Token 用量，可以优化成本。

---

## 1.3 编写 `agent.py`

```python
"""
agent.py — 阶段 1：最小 Agent（直接调 Anthropic API）

这是整个项目最简单的核心：接收问题，调用 Claude，返回回答。
没有工具、没有记忆、没有框架——就是最纯粹的"问 → 答"。
"""

import anthropic
from dataclasses import dataclass
from core.config import settings


@dataclass
class AskResult:
    """封装一次对话的结果，包括回答文字和 Token 用量。"""
    text: str           # Agent 的回答文字
    input_tokens: int   # 本次消耗的输入 Token
    output_tokens: int  # 本次消耗的输出 Token


async def ask(question: str) -> AskResult:
    """
    向 Claude 提问，返回回答和 Token 用量。

    参数：
        question - 用户的问题（字符串）

    返回：
        AskResult 对象，包含 text、input_tokens、output_tokens
    """
    # 创建 Anthropic 客户端（AsyncAnthropic 支持 async/await，不阻塞）
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key
    )

    # 调用 messages.create()——这是最核心的 API
    message = await client.messages.create(
        model=settings.llm_model,      # 使用哪个 Claude 模型
        max_tokens=4096,               # 最多生成多少 Token（防止无限输出）
        system="你是一个智能助手，请用中文回答问题，回答要简洁准确。",
        messages=[
            {"role": "user", "content": question}  # 用户的问题
        ],
    )

    # message.content 是一个列表，第一个元素是文本块
    # message.content[0].text 就是 Claude 的回答
    return AskResult(
        text=message.content[0].text,
        input_tokens=message.usage.input_tokens,
        output_tokens=message.usage.output_tokens,
    )
```

**代码要点说明：**

1. `AsyncAnthropic`：异步版客户端。你的程序在等待 Claude 回复时，可以同时处理其他请求（如 Web 服务同时接待多个用户）。
2. `system`：给 Claude 的"工作说明书"，告诉它扮演什么角色、遵守什么规则。
3. `max_tokens`：安全阀，防止 Claude 生成超长回复。根据你的业务调整，一般对话 1024 够用，长文本生成可以到 8192。
4. `message.content[0]`：Claude 的回复是一个内容块列表，简单情况下只有一个文本块。第 5 章加工具后，会出现 `tool_use` 类型的块。

---

## 1.4 编写 `cli.py`（命令行交互入口）

```python
"""
cli.py — 命令行交互入口

功能：
- 循环读取用户输入
- 调用 ask() 并打印回答
- /usage 命令查看累计 Token 用量
- Ctrl+C 退出
"""

import asyncio
from agent import ask


async def main():
    print("=" * 50)
    print("  Agent 已就绪（直接调用 Anthropic API）")
    print("  输入问题后按回车，输入 /usage 查看 Token 用量")
    print("  按 Ctrl+C 退出")
    print("=" * 50)

    total_input = 0   # 累计输入 Token
    total_output = 0  # 累计输出 Token

    while True:
        try:
            question = input("\n你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            break

        if not question:
            continue

        # 内置命令
        if question == "/usage":
            print(f"  累计 Token：输入 {total_input}，输出 {total_output}")
            print(f"  大约费用（Sonnet）：${(total_input * 3 + total_output * 15) / 1_000_000:.4f} USD")
            continue

        if question.lower() in ("quit", "q", "exit", "退出"):
            print("再见！")
            break

        # 调用 Agent
        try:
            print("Agent：", end="", flush=True)
            result = await ask(question)
            print(result.text)

            # 累计 Token 用量
            total_input  += result.input_tokens
            total_output += result.output_tokens
            print(f"  （本次：输入 {result.input_tokens} / 输出 {result.output_tokens} tokens）")

        except Exception as e:
            print(f"\n[错误] {e}")
            print("提示：检查 .env 里的 ANTHROPIC_API_KEY 是否正确，以及网络是否正常。")


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 1.5 运行与测试

```bash
# 确保虚拟环境已激活（提示符前有 (.venv)）
# 确保在 my-agent 目录下

python cli.py
```

**预期输出：**

```
==================================================
  Agent 已就绪（直接调用 Anthropic API）
  输入问题后按回车，输入 /usage 查看 Token 用量
  按 Ctrl+C 退出
==================================================

你：Python 是什么？
Agent：Python 是一种高级、解释型编程语言，以简洁易读的语法著称...
  （本次：输入 21 / 输出 87 tokens）

你：/usage
  累计 Token：输入 21，输出 87
  大约费用（Sonnet）：$0.0014 USD

你：q
再见！
```

---

## 1.6 接入其他 Provider（改一行 .env 即可）

**接入 OpenAI GPT-4o：**

修改 `.env`：
```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
```

修改 `agent.py` 的 `ask()` 函数（把 Anthropic 客户端换成 OpenAI）：

```python
from openai import AsyncOpenAI

async def ask(question: str) -> AskResult:
    client = AsyncOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
    )
    response = await client.chat.completions.create(
        model=settings.openai_model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": "你是一个智能助手，请用中文回答问题。"},
            {"role": "user",   "content": question},
        ],
    )
    return AskResult(
        text=response.choices[0].message.content,
        input_tokens=response.usage.prompt_tokens,
        output_tokens=response.usage.completion_tokens,
    )
```

**接入本地 Ollama（零费用）：**

```dotenv
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama     # Ollama 不验证 Key，随便填
OPENAI_MODEL=llama3.2:3b  # 换成你拉取的模型名
```

> 💡 Ollama 使用 OpenAI 兼容协议，所以 OpenAI 的 SDK 可以直接连接 Ollama，代码不用改，只改 `.env`。

---

## 1.7 常见报错及解决方法

**报错：`AuthenticationError: Error code: 401`**

原因：API Key 不正确或未设置。

解决：
1. 打开 `.env`，确认 `ANTHROPIC_API_KEY=` 后面是真实的 Key（以 `sk-ant-` 开头）
2. Key 前后不要有多余的空格或引号
3. 确认虚拟环境已激活（不激活的话 `python-dotenv` 可能没装）

**报错：`ModuleNotFoundError: No module named 'anthropic'`**

原因：依赖没有在当前虚拟环境里安装。

解决：
```bash
source .venv/bin/activate   # 先激活虚拟环境
pip install -r requirements.txt
```

**报错：`ConnectionError` 或程序卡住不动**

原因：网络无法连接 Anthropic 服务器。

解决：
1. 检查网络是否能正常访问外网
2. 如果需要代理，在 `.env` 里添加：
   ```dotenv
   ANTHROPIC_BASE_URL=https://你的代理地址/v1
   ```
   并修改 `agent.py` 中的客户端初始化：
   ```python
   client = anthropic.AsyncAnthropic(
       api_key=settings.anthropic_api_key,
       base_url=settings.anthropic_base_url,  # 需要在 Settings 里加这个字段
   )
   ```

**报错：`pydantic_settings` 相关错误**

原因：`.env` 文件格式不对（如 Key 的值里有特殊字符未加引号）。

解决：把包含特殊字符的值用引号包住：
```dotenv
ANTHROPIC_API_KEY="sk-ant-..."
```

---

## 1.8 测试脚本（可选）

创建 `tests/test_stage1.py`：

```python
"""阶段 1 自动测试"""
import asyncio
import pytest
from agent import ask, AskResult


@pytest.mark.asyncio
async def test_ask_returns_result():
    """基础对话测试：应该返回非空回答"""
    result = await ask("用一句话解释什么是 Python")
    assert isinstance(result, AskResult)
    assert len(result.text) > 10, "回答不应该为空"
    assert result.input_tokens > 0
    assert result.output_tokens > 0


@pytest.mark.asyncio
async def test_ask_chinese_response():
    """应该用中文回答（系统提示词里要求了）"""
    result = await ask("What is Python?")
    # 检查回答包含中文字符
    has_chinese = any('一' <= char <= '鿿' for char in result.text)
    assert has_chinese, "应该用中文回答"
```

运行测试：

```bash
pip install pytest pytest-asyncio
python -m pytest tests/test_stage1.py -v
```

---

## 1.9 本章检查清单

```
□ agent.py 已创建，能正常导入（python -c "from agent import ask"，不报错）

□ cli.py 已创建

□ python cli.py 能正常启动，输入问题得到回答

□ /usage 命令显示 Token 用量（数字大于 0）

□ 能理解 messages 列表的格式（role + content 交替）

□ 能理解 input_tokens / output_tokens 的含义

□（可选）test_stage1.py 测试全部通过
```

**全部打勾之后，进入第 2 章。**

---

# 第 2 章：阶段 2 —— Web 接口（流式 SSE）

> **本章目标**：把命令行程序变成 Web 服务，并支持两种调用方式：
> - `POST /ask`：完整响应（等 Claude 写完再返回）
> - `GET /ask/stream`：流式响应（逐 token 实时推送，像打字机）

---

## 2.1 本章新增内容

```
my-agent/
├── agent.py        ← 新增 ask_stream() 方法
└── main.py         ← 新增：FastAPI Web 服务入口
```

---

## 2.2 核心概念讲解

### 2.2.1 什么是 SSE（Server-Sent Events）

SSE 是服务器向浏览器**单向推送数据**的协议。和普通 HTTP 请求的区别：

| 普通 HTTP 请求 | SSE |
|--------------|-----|
| 请求 → 等 → 一次性返回全部数据 | 请求 → 服务器持续推送数据 → 客户端逐块接收 |
| 适合：查询用户信息 | 适合：AI 打字机效果、实时日志推送 |

SSE 的数据格式（服务器发出的格式）：

```
data: {"type": "text_delta", "text": "你好"}

data: {"type": "text_delta", "text": "，有什么"}

data: {"type": "text_delta", "text": "可以帮你的？"}

data: [DONE]
```

每条消息以 `data: ` 开头，以 `\n\n` 结尾。`[DONE]` 表示流结束。

### 2.2.2 为什么要提供两个接口

- **`POST /ask`（非流式）**：适合后端调用——你的程序调用 Agent 服务，拿到完整结果再处理。简单、稳定。
- **`GET /ask/stream`（流式 SSE）**：适合前端对话框——用户能即时看到 AI 正在输出，体验好，不会"等了半天才出来"。

实际项目里两个都要有，分别服务不同的调用方。

### 2.2.3 FastAPI 的装饰器语法

FastAPI 用装饰器把函数绑定到 URL：

```python
@app.post("/ask")         # POST 请求发到 /ask
async def ask_endpoint(req: AskRequest):
    ...

@app.get("/ask/stream")   # GET 请求发到 /ask/stream
async def ask_stream_endpoint(question: str):
    ...
```

`question: str` 这种写法会自动从 URL 参数读取：`/ask/stream?question=你好`。

### 2.2.4 Pydantic 数据模型的作用

FastAPI + Pydantic 组合会自动做两件事：
1. **验证输入**：如果调用方发来的数据缺少必填字段，自动返回 400 错误（你不用写检查代码）
2. **自动生成文档**：访问 `/docs` 能看到所有接口的参数说明和在线测试页面

---

## 2.3 新增流式方法到 `agent.py`

在 `agent.py` 的末尾加上：

```python
# agent.py（在原有 ask() 函数之后追加）

from typing import AsyncGenerator


async def ask_stream(question: str) -> AsyncGenerator[str, None]:
    """
    流式调用 Claude，逐块 yield 文本片段。

    调用方用 async for chunk in ask_stream(question) 来逐块接收文本。
    每个 chunk 是一小段文字（几个字或一个词），积累起来就是完整回答。

    参数：
        question - 用户的问题

    yield：
        str 类型的文本片段（不是完整回答，是片段）
    """
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key
    )

    # client.messages.stream() 返回一个流式上下文管理器
    async with client.messages.stream(
        model=settings.llm_model,
        max_tokens=4096,
        system="你是一个智能助手，请用中文回答问题，回答要简洁准确。",
        messages=[
            {"role": "user", "content": question}
        ],
    ) as stream:
        # stream.text_stream 是一个异步生成器，逐 token 产出文本
        async for text_chunk in stream.text_stream:
            yield text_chunk   # 把每个文本片段传给调用方
```

> 💡 **`AsyncGenerator[str, None]` 是什么？**
> 这是返回类型标注，表示这个函数是一个"异步生成器"——调用它不会立刻返回结果，而是可以用 `async for` 逐块迭代。`str` 表示每次 yield 的是字符串，`None` 表示最终 return 值是 None（生成器不 return 有意义的值）。

---

## 2.4 编写 `main.py`

```python
"""
main.py — FastAPI Web 服务入口

提供两个接口：
  POST /ask        → 完整响应（等待全部内容）
  GET  /ask/stream → SSE 流式响应（逐 token 实时推送）

额外接口：
  GET /health      → 健康检查（运维用）
  GET /docs        → 自动生成的 API 文档（FastAPI 内置）
"""

import json
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent import ask, ask_stream


# ── 请求/响应数据格式定义 ──────────────────────────────────────────

class AskRequest(BaseModel):
    """POST /ask 的请求体格式"""
    question: str = Field(
        ...,                        # ... 表示必填，不能为空
        min_length=1,
        description="用户的问题",
        examples=["Python 是什么？"]
    )


class AskResponse(BaseModel):
    """POST /ask 的响应体格式"""
    text: str = Field(description="Agent 的回答")
    usage: dict = Field(description="Token 用量：{input_tokens, output_tokens}")


# ── 应用生命周期（启动/关闭钩子）────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    yield 前：服务启动时执行（初始化资源）
    yield 后：服务关闭时执行（释放资源）
    """
    print("服务启动中...")
    print(f"API 文档地址：http://localhost:8002/docs")
    print(f"流式测试：curl -N 'http://localhost:8002/ask/stream?question=你好'")
    yield
    print("服务已关闭。")


# ── 创建 FastAPI 实例 ─────────────────────────────────────────────

app = FastAPI(
    title="My Agent API",
    description="基于 Claude 的智能对话服务，支持完整响应和流式响应。",
    version="0.2.0",
    lifespan=lifespan,
)

# 跨域中间件（允许浏览器前端直接调用，生产环境改为具体域名）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API 接口定义 ───────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """
    健康检查。运维工具（Kubernetes、负载均衡器）用这个接口确认服务正常。
    返回 200 OK 即可，不需要做复杂逻辑。
    """
    return {"status": "ok", "timestamp": int(time.time())}


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    """
    完整响应接口：等 Claude 写完，一次性返回全部内容。

    请求体：{"question": "你的问题"}
    响应体：{"text": "回答", "usage": {"input_tokens": N, "output_tokens": N}}
    """
    start = time.time()
    print(f"[/ask] 收到请求: {req.question[:60]}")

    result = await ask(req.question)

    elapsed_ms = round((time.time() - start) * 1000)
    print(f"[/ask] 完成，耗时 {elapsed_ms}ms")

    return AskResponse(
        text=result.text,
        usage={
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        },
    )


@app.get("/ask/stream")
async def ask_stream_endpoint(question: str):
    """
    SSE 流式响应接口：逐 token 实时推送，适合前端打字机效果。

    URL 参数：?question=你的问题
    响应：text/event-stream 格式，逐块推送 JSON 数据

    测试方法：
      curl -N "http://localhost:8002/ask/stream?question=请写一首短诗"
    """

    async def event_generator():
        """
        SSE 事件生成器。
        每次 yield 一条 SSE 格式的消息（'data: {...}\\n\\n'）。
        """
        try:
            async for chunk in ask_stream(question):
                # 把文本片段包装成 SSE 格式
                data = json.dumps(
                    {"type": "text_delta", "text": chunk},
                    ensure_ascii=False
                )
                yield f"data: {data}\n\n"

            # 流结束标记
            yield "data: [DONE]\n\n"

        except Exception as e:
            # 出错时发送错误事件，让客户端知道出了问题
            error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",          # 不缓存，保证实时性
            "X-Accel-Buffering": "no",            # 禁用 Nginx 缓冲（如果用了 Nginx 反代）
        },
    )


# ── 直接运行的入口 ────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    import os

    port = int(os.environ.get("APP_PORT", 8002))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=True,    # 开发模式：代码改动自动重启
    )
```

---

## 2.5 启动和测试

### 启动服务

```bash
python main.py
```

成功后终端显示：

```
服务启动中...
API 文档地址：http://localhost:8002/docs
流式测试：curl -N 'http://localhost:8002/ask/stream?question=你好'
INFO:     Uvicorn running on http://0.0.0.0:8002 (Press CTRL+C to quit)
```

### 测试非流式接口（方式一：Swagger 文档）

浏览器打开 `http://localhost:8002/docs`，点击 `POST /ask` → Try it out → 填写参数 → Execute。

### 测试非流式接口（方式二：curl）

新开一个终端：

```bash
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "用一句话解释什么是递归"}'
```

预期响应：

```json
{
  "text": "递归是函数直接或间接调用自身来解决问题的编程技术。",
  "usage": {"input_tokens": 28, "output_tokens": 32}
}
```

### 测试流式接口

```bash
# -N 参数：禁用 curl 的输出缓冲，这样才能看到实时流式输出
curl -N "http://localhost:8002/ask/stream?question=请用三句话介绍Python"
```

预期输出（逐条出现，有打字机效果）：

```
data: {"type": "text_delta", "text": "Python"}

data: {"type": "text_delta", "text": " 是一种"}

data: {"type": "text_delta", "text": "高级编程语言"}

...

data: [DONE]
```

### 测试健康检查

```bash
curl http://localhost:8002/health
# 预期：{"status":"ok","timestamp":1719619200}
```

---

## 2.6 数据流图

```
客户端（浏览器/curl/其他程序）
        │
        │  POST /ask
        │  {"question": "..."}
        ▼
  FastAPI (main.py)
        │
        │  1. Pydantic 验证请求格式
        │  2. 调用 ask(req.question)
        ▼
  agent.py → Anthropic SDK → Claude API
        │
        │  返回 AskResult{text, input_tokens, output_tokens}
        ▼
  FastAPI 包装成 AskResponse JSON 返回
        │
        ▼
{"text": "...", "usage": {...}}

─────────────────────────────────────────────────

客户端（支持 SSE 的浏览器/curl）
        │
        │  GET /ask/stream?question=...
        ▼
  FastAPI 返回 StreamingResponse（持续连接）
        │
        │  event_generator() 持续 yield SSE 消息
        ▼
  agent.py ask_stream() → Anthropic 流式 SDK
        │
        │  逐 token 返回
        ▼
data: {"type":"text_delta","text":"你"}   ← 实时推送
data: {"type":"text_delta","text":"好"}
data: [DONE]
```

---

## 2.7 给 `.env` 补充端口配置

确认 `.env` 里有这行（0.4 节已加过的话跳过）：

```dotenv
APP_PORT=8002
```

---

## 2.8 常见问题

**Q：浏览器访问 `http://localhost:8002/docs` 显示"无法访问"？**

A：
1. 确认服务正在运行（运行 `python main.py` 的终端没有报错）
2. 确认端口没被占用：`lsof -i :8002`（macOS/Linux）
3. 如果被占用，改 `.env` 里的 `APP_PORT=8003`

**Q：流式接口发出请求但没有实时输出，等很久才一次性出来？**

A：检查是否用了 curl 但忘了加 `-N` 参数。没有 `-N` 的话 curl 会缓冲全部内容再显示。

**Q：`POST /ask` 报错 `422 Unprocessable Entity`？**

A：这是 Pydantic 验证失败。常见原因是：
- 请求体的 JSON 格式不对（缺少字段、字段名拼错）
- `question` 字段是空字符串（`min_length=1` 不允许空字符串）
- 没有设置 `Content-Type: application/json` 头

---

## 2.9 本章检查清单

```
□ agent.py 新增了 ask_stream() 方法

□ main.py 已创建，包含 /health、/ask、/ask/stream 三个接口

□ 服务能正常启动（看到 "Uvicorn running on..."）

□ /health 接口返回 200（curl http://localhost:8002/health）

□ Swagger 文档能打开（http://localhost:8002/docs）

□ /ask 接口能返回完整回答（包含 text 和 usage）

□ /ask/stream 接口能看到逐块的 SSE 输出（curl -N ...）

□ 能理解 SSE 格式（"data: {...}\n\n"）
```

**全部打勾之后，进入第 3 章。**

---

# 第 3 章：阶段 3 —— 统一 Provider 抽象层 ★

> **本章目标**：用一套统一接口屏蔽所有 LLM Provider 的差异。
> 完成后，无论后端是 Claude、GPT-4、Gemini 还是本地 Ollama，上层代码一行不改，只改 `.env`。

---

## 3.1 为什么需要 Provider 抽象层

不同 LLM 提供商的 API 格式相差很大：

| Provider | SDK 调用方式 | 工具调用字段 | 流式事件格式 |
|---------|------------|------------|------------|
| Anthropic | `client.messages.create()` | `tool_use` 块 | SSE delta 事件 |
| OpenAI | `client.chat.completions.create()` | `tool_calls` 数组 | chunks |
| Gemini | `model.generate_content()` | `function_call` 部分 | 不同事件格式 |

如果每次换 Provider 都要改代码，维护成本极高。解决方案：

```
你的业务代码
      ↓  统一接口（BaseProvider）
ProviderRouter（读 .env 自动选择）
      ↓         ↓           ↓
AnthropicAdapter  OpenAIAdapter  GeminiAdapter
      ↓         ↓           ↓
Claude API    GPT/Ollama   Gemini API
```

每个 Adapter 负责把自家 API 的格式，转换成统一内部格式。

---

## 3.2 目录结构

```
my-agent/
├── providers/
│   ├── __init__.py
│   ├── base.py          ← 抽象基类：定义统一接口契约
│   ├── types.py         ← 统一内部消息格式（以 Anthropic 格式为标准）
│   ├── router.py        ← Provider 路由器（读 .env 选择 Provider）
│   ├── anthropic.py     ← Anthropic 适配器
│   ├── openai.py        ← OpenAI 适配器（兼容 Ollama/DeepSeek/vLLM）
│   └── gemini.py        ← Gemini 适配器
└── ...
```

初始化目录：

```bash
mkdir -p providers
touch providers/__init__.py
```

---

## 3.3 统一内部消息格式 `providers/types.py`

**为什么以 Anthropic 格式为内部标准？**

Anthropic 的格式更细粒度（把每种内容分成不同类型的 Block），表达能力更强，更容易转换到其他格式，而反过来可能丢失信息。

```python
# providers/types.py
"""
统一内部消息格式，以 Anthropic API 格式为基准。

所有 Provider 适配器的职责：
  - 发送前：把统一格式转成自家 API 格式
  - 接收后：把自家 API 格式转成统一格式

这样上层代码只需要和统一格式打交道。
"""
from typing import Literal, Union
from pydantic import BaseModel


# ── 内容块（Content Block）────────────────────────────────────────────────────
# 一条 LLM 消息的内容由一个或多个内容块组成。

class TextBlock(BaseModel):
    """普通文本块——LLM 说的话，或者用户的问题。"""
    type: Literal["text"] = "text"
    text: str


class ToolUseBlock(BaseModel):
    """
    工具调用块——LLM 决定要调用某个工具时产生此块。
    包含工具名和调用参数。第 5 章实现工具后会频繁见到。
    """
    type: Literal["tool_use"] = "tool_use"
    id: str          # 工具调用的唯一 ID（用于匹配 tool_result）
    name: str        # 工具名称（和注册时的名字一致）
    input: dict      # 工具调用参数（JSON 对象）


class ToolResultBlock(BaseModel):
    """
    工具结果块——工具执行完后把结果放进这个块，发回给 LLM。
    LLM 看到这个块，才能基于工具返回的数据给出最终回答。
    """
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str    # 对应哪次 tool_use 调用的 id
    content: str        # 工具的返回内容（字符串）
    is_error: bool = False   # 工具是否执行失败


# 内容块的联合类型
ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]


# ── 消息 ──────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """一条对话消息：角色（user/assistant）+ 内容块列表。"""
    role: Literal["user", "assistant"]
    content: list[ContentBlock]


# ── 工具定义 ──────────────────────────────────────────────────────────────────

class ToolDefinition(BaseModel):
    """
    向 LLM 描述一个工具。LLM 靠这个信息决定何时调用哪个工具。
    description 写得越清晰，LLM 调用工具越准确。
    """
    name: str
    description: str
    input_schema: dict   # JSON Schema 格式，描述工具接受什么参数


# ── Token 用量 ────────────────────────────────────────────────────────────────

class Usage(BaseModel):
    """LLM 调用的 Token 消耗统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0    # Anthropic Prompt Cache 命中（第 8 章详讲）
    cache_write_tokens: int = 0   # Anthropic Prompt Cache 写入


# ── 流式 chunk 类型 ───────────────────────────────────────────────────────────

class TextDelta(BaseModel):
    """流式传输中的文本片段。"""
    type: Literal["text_delta"] = "text_delta"
    text: str


class ToolInputDelta(BaseModel):
    """流式传输中的工具参数片段（工具参数可能很长，分段传输）。"""
    type: Literal["tool_input_delta"] = "tool_input_delta"
    tool_id: str
    partial_json: str


class MessageStart(BaseModel):
    """流式响应开始事件（包含初始 usage 信息）。"""
    type: Literal["message_start"] = "message_start"
    usage: Usage


class MessageStop(BaseModel):
    """
    流式响应结束事件。

    stop_reason 说明 LLM 为何停止生成：
    - "end_turn"：正常完成
    - "tool_use"：需要调用工具（第 5 章后会见到）
    - "max_tokens"：达到最大 Token 限制（回答被截断）
    """
    type: Literal["message_stop"] = "message_stop"
    stop_reason: str
    usage: Usage


# 流式 chunk 的联合类型
StreamChunk = Union[TextDelta, ToolInputDelta, MessageStart, MessageStop]


# ── Provider 完整响应 ─────────────────────────────────────────────────────────

class ProviderResponse(BaseModel):
    """一次非流式调用的完整响应。"""
    content: list[ContentBlock]   # 所有内容块（文本 + 工具调用）
    stop_reason: str
    usage: Usage
```

---

## 3.4 抽象基类 `providers/base.py`

```python
# providers/base.py
"""
BaseProvider：所有 Provider 适配器必须实现的接口契约。

上层代码（Agentic Loop）只和这个接口交互，
不关心底层是哪家 Provider，不关心 API 格式差异。
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator
from .types import Message, ToolDefinition, StreamChunk, ProviderResponse


class BaseProvider(ABC):

    @abstractmethod
    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:
        """
        非流式调用：等待 LLM 生成完整响应再返回。

        参数：
            messages  - 对话历史（user/assistant 交替）
            system    - 系统提示词（给 LLM 的工作说明）
            tools     - 可用工具列表（None 或空列表表示不用工具）
            max_tokens - 最大生成 Token 数
        """
        ...

    @abstractmethod
    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:
        """
        流式调用：边生成边 yield StreamChunk。
        调用方用 async for chunk in provider.stream(...) 处理。
        """
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        """当前 Provider 使用的模型名称（用于日志和监控）。"""
        ...

    @property
    def supports_tool_use(self) -> bool:
        """
        是否支持工具调用。
        不支持的 Provider（如某些本地模型）覆盖此属性返回 False。
        默认返回 True（主流 Provider 都支持）。
        """
        return True
```

---

## 3.5 Anthropic 适配器 `providers/anthropic.py`

```python
# providers/anthropic.py
"""
Anthropic Provider 适配器。

由于内部格式以 Anthropic 格式为基准，这个适配器的转换逻辑最少。
主要工作：把 Pydantic 模型转成 SDK 所需的字典格式。
"""
import anthropic
from typing import AsyncGenerator
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, Usage,
    TextDelta, MessageStart, MessageStop,
)


class AnthropicProvider(BaseProvider):

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,  # 支持代理地址
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _to_sdk_messages(self, messages: list[Message]) -> list[dict]:
        """
        把内部 Message 对象列表转成 Anthropic SDK 所需的字典格式。

        内部格式和 SDK 格式基本一致，主要差异是 Pydantic 模型要转成 dict。
        """
        result = []
        for msg in messages:
            blocks = []
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif hasattr(block, "tool_use_id"):  # ToolResultBlock
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    })
                else:  # TextBlock
                    blocks.append({"type": "text", "text": block.text})
            result.append({"role": msg.role, "content": blocks})
        return result

    def _to_sdk_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """把工具定义转成 Anthropic 格式。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in tools
        ]

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=self._to_sdk_messages(messages),
        )
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        resp = await self._client.messages.create(**kwargs)

        # 把 SDK 响应转回内部格式
        content = []
        for block in resp.content:
            if block.type == "text":
                content.append(TextBlock(text=block.text))
            elif block.type == "tool_use":
                content.append(ToolUseBlock(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))

        return ProviderResponse(
            content=content,
            stop_reason=resp.stop_reason or "end_turn",
            usage=Usage(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", 0),
                cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0),
            ),
        )

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=self._to_sdk_messages(messages),
        )
        if tools:
            kwargs["tools"] = self._to_sdk_tools(tools)

        async with self._client.messages.stream(**kwargs) as stream:
            # 先发出 MessageStart（包含初始 usage）
            yield MessageStart(usage=Usage())

            async for event in stream:
                if event.type == "content_block_delta":
                    if hasattr(event.delta, "text"):
                        yield TextDelta(text=event.delta.text)

            # 流结束后取最终消息（包含完整 usage）
            final = await stream.get_final_message()
            yield MessageStop(
                stop_reason=final.stop_reason or "end_turn",
                usage=Usage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                ),
            )
```

---

## 3.6 OpenAI 适配器 `providers/openai.py`

OpenAI 格式和内部格式的主要差异：

| 概念 | 内部格式 | OpenAI 格式 |
|------|---------|-----------|
| 工具调用（LLM 决定调用） | `ToolUseBlock` | `tool_calls` 数组 |
| 工具结果（发回给 LLM） | `ToolResultBlock`（user 角色里） | `role: "tool"` 单独消息 |
| 停止原因：需要工具 | `"tool_use"` | `"tool_calls"` |

```python
# providers/openai.py
"""
OpenAI Provider 适配器。

同时支持所有兼容 OpenAI Chat Completions API 的服务：
  - OpenAI（GPT-4o、o1 等）
  - Ollama（本地模型，如 llama3、qwen2.5）
  - DeepSeek（deepseek-chat、deepseek-reasoner）
  - Azure OpenAI
  - vLLM、LM Studio 等自托管服务

切换方式：只改 .env 里的 OPENAI_BASE_URL 和 OPENAI_MODEL，代码零改动。
"""
import json
from openai import AsyncOpenAI
from typing import AsyncGenerator
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)


class OpenAIProvider(BaseProvider):

    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self._model = model

    @property
    def model_name(self) -> str:
        return self._model

    def _to_openai_messages(self, messages: list[Message], system: str) -> list[dict]:
        """
        把内部消息格式转成 OpenAI 格式。

        关键转换：
        1. system 放在最前面，作为独立消息（OpenAI 不支持顶层 system 参数）
        2. ToolResultBlock 需要转成 role="tool" 的独立消息
        3. ToolUseBlock 需要放到 assistant 消息的 tool_calls 字段
        """
        result = []

        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            # 分析这条消息里有什么
            text_content = ""
            tool_calls = []
            tool_results = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_content = block.text
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input, ensure_ascii=False),
                        },
                    })
                elif isinstance(block, ToolResultBlock):
                    tool_results.append(block)

            if tool_results:
                # 工具结果：每个结果一条独立的 role="tool" 消息
                for tr in tool_results:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": tr.content,
                    })
            elif tool_calls:
                # assistant 消息包含工具调用
                m = {"role": "assistant", "content": text_content or None, "tool_calls": tool_calls}
                result.append(m)
            else:
                result.append({"role": msg.role, "content": text_content})

        return result

    def _to_openai_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _parse_stop_reason(self, finish_reason: str | None) -> str:
        # OpenAI 的 "tool_calls" 对应内部的 "tool_use"
        if finish_reason == "tool_calls":
            return "tool_use"
        return finish_reason or "end_turn"

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=self._to_openai_messages(messages, system),
        )
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0]

        content: list = []
        if choice.message.content:
            content.append(TextBlock(text=choice.message.content))
        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                content.append(ToolUseBlock(
                    id=tc.id,
                    name=tc.function.name,
                    input=json.loads(tc.function.arguments),
                ))

        return ProviderResponse(
            content=content,
            stop_reason=self._parse_stop_reason(choice.finish_reason),
            usage=Usage(
                input_tokens=resp.usage.prompt_tokens if resp.usage else 0,
                output_tokens=resp.usage.completion_tokens if resp.usage else 0,
            ),
        )

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:

        kwargs = dict(
            model=self._model,
            max_tokens=max_tokens,
            messages=self._to_openai_messages(messages, system),
            stream=True,
            stream_options={"include_usage": True},
        )
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        yield MessageStart(usage=Usage())

        async for chunk in await self._client.chat.completions.create(**kwargs):
            if not chunk.choices:
                if chunk.usage:
                    yield MessageStop(
                        stop_reason="end_turn",
                        usage=Usage(
                            input_tokens=chunk.usage.prompt_tokens,
                            output_tokens=chunk.usage.completion_tokens,
                        ),
                    )
                continue

            choice = chunk.choices[0]
            if choice.delta.content:
                yield TextDelta(text=choice.delta.content)
```

**接入不同服务只需改 `.env`：**

```dotenv
# Ollama 本地模型（无需 API Key）
LLM_PROVIDER=openai
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
OPENAI_MODEL=qwen2.5:7b

# DeepSeek
LLM_PROVIDER=openai
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=deepseek-chat
```

---

## 3.7 Provider 路由器 `providers/router.py`

```python
# providers/router.py
"""
根据 .env 配置自动选择并返回 Provider 实例。
全局单例：第一次调用时初始化，之后复用同一个实例。
"""
from functools import lru_cache
from .base import BaseProvider
from core.config import settings


@lru_cache(maxsize=1)
def get_provider() -> BaseProvider:
    """
    获取 Provider 单例。

    lru_cache 确保这个函数只执行一次（第一次调用时创建实例，
    之后直接返回缓存的实例，不重复创建客户端）。
    """
    name = settings.llm_provider.lower()

    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.llm_model,
        )

    elif name in ("openai", "ollama", "deepseek", "azure"):
        from .openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_model,
            base_url=settings.openai_base_url or None,
        )

    elif name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
        )

    else:
        raise ValueError(
            f"不支持的 Provider: '{name}'。"
            f"可选值：anthropic / openai / gemini。"
            f"检查 .env 里的 LLM_PROVIDER 配置。"
        )


def clear_provider_cache():
    """清除缓存（测试时切换 Provider 用）。"""
    get_provider.cache_clear()
```

---

## 3.8 Gemini 适配器 `providers/gemini.py`

Gemini 和 Anthropic/OpenAI 的格式差异最大，主要有三点：
1. **role 命名**：Gemini 用 `"model"` 代替 `"assistant"`
2. **system prompt**：不放在消息历史里，而是单独传 `system_instruction`
3. **工具调用**：用 `function_call` / `function_response` Part，而不是独立的消息块

```python
# providers/gemini.py
"""
Gemini Provider 适配器。

通过 google-generativeai SDK 接入 Google Gemini 系列模型。

Gemini API 和 Anthropic/OpenAI 的主要格式差异：
  1. 消息格式：role 只有 "user" / "model"（没有 "assistant"）
  2. 消息结构：content 用 Part 列表，不用 Block
  3. system prompt：单独传入 system_instruction，不放在消息历史里
  4. 工具调用：function_declarations 格式，和 OpenAI tool_calls 不同
  5. 流式：generate_content_async(stream=True) + async for chunk
"""
import json
import uuid
from typing import AsyncGenerator
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from .base import BaseProvider
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)


class GeminiProvider(BaseProvider):

    def __init__(self, api_key: str, model: str):
        genai.configure(api_key=api_key)
        self._model_name = model

    @property
    def model_name(self) -> str:
        return self._model_name

    def _to_gemini_messages(self, messages: list[Message]) -> list[dict]:
        """
        把内部消息格式转成 Gemini 格式。

        关键差异：
        - Gemini 的 role 是 "user" / "model"，没有 "assistant"
        - 工具调用：assistant 消息里的 ToolUseBlock → role="model" + function_call part
        - 工具结果：user 消息里的 ToolResultBlock → role="user" + function_response part
        """
        result = []
        for msg in messages:
            role = "model" if msg.role == "assistant" else "user"
            parts = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                elif isinstance(block, ToolUseBlock):
                    parts.append({
                        "function_call": {
                            "name": block.name,
                            "args": block.input,
                        }
                    })
                elif isinstance(block, ToolResultBlock):
                    parts.append({
                        "function_response": {
                            "name": block.tool_use_id,
                            "response": {
                                "result": block.content,
                                "is_error": block.is_error,
                            },
                        }
                    })

            if parts:
                result.append({"role": role, "parts": parts})

        return result

    def _to_gemini_tools(self, tools: list[ToolDefinition]) -> list[dict]:
        """把工具定义转成 Gemini function_declarations 格式。"""
        return [{
            "function_declarations": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                }
                for t in tools
            ]
        }]

    def _build_model(self, system: str, tools: list[ToolDefinition] | None):
        """创建带有 system_instruction 和工具的模型实例。"""
        kwargs = {"model_name": self._model_name}
        if system:
            kwargs["system_instruction"] = system
        if tools:
            kwargs["tools"] = self._to_gemini_tools(tools)
        return genai.GenerativeModel(**kwargs)

    def _parse_response(self, response) -> ProviderResponse:
        """把 Gemini 响应转成内部 ProviderResponse 格式。"""
        content = []
        stop_reason = "end_turn"

        for candidate in response.candidates:
            for part in candidate.content.parts:
                if hasattr(part, "text") and part.text:
                    content.append(TextBlock(text=part.text))
                elif hasattr(part, "function_call") and part.function_call:
                    fc = part.function_call
                    content.append(ToolUseBlock(
                        id=str(uuid.uuid4()),
                        name=fc.name,
                        input=dict(fc.args),
                    ))
                    stop_reason = "tool_use"

        usage = Usage()
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = Usage(
                input_tokens=response.usage_metadata.prompt_token_count or 0,
                output_tokens=response.usage_metadata.candidates_token_count or 0,
            )

        return ProviderResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
        )

    async def chat(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> ProviderResponse:

        model = self._build_model(system, tools)
        gemini_messages = self._to_gemini_messages(messages)

        response = await model.generate_content_async(
            gemini_messages,
            generation_config=GenerationConfig(max_output_tokens=max_tokens),
        )

        return self._parse_response(response)

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        tools: list[ToolDefinition] | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[StreamChunk, None]:

        model = self._build_model(system, tools)
        gemini_messages = self._to_gemini_messages(messages)

        yield MessageStart(usage=Usage())

        response = await model.generate_content_async(
            gemini_messages,
            generation_config=GenerationConfig(max_output_tokens=max_tokens),
            stream=True,
        )

        input_tokens = 0
        output_tokens = 0

        async for chunk in response:
            if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                input_tokens = chunk.usage_metadata.prompt_token_count or 0
                output_tokens = chunk.usage_metadata.candidates_token_count or 0

            if chunk.candidates:
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        yield TextDelta(text=part.text)

        yield MessageStop(
            stop_reason="end_turn",
            usage=Usage(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            ),
        )
```

---

## 3.9 更新 `agent.py` 使用 Provider 抽象层

```python
# agent.py（替换原有内容）
from dataclasses import dataclass
from typing import AsyncGenerator
from providers.router import get_provider
from providers.types import Message, TextBlock, Usage


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int


SYSTEM_PROMPT = "你是一个智能助手，请用中文回答问题，回答要简洁准确。"


async def ask(question: str) -> AskResult:
    """非流式调用：等待完整响应。"""
    provider = get_provider()   # 根据 .env 自动选择 Provider

    response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=question)])],
        system=SYSTEM_PROMPT,
    )

    # 从响应内容中提取文本
    text = ""
    for block in response.content:
        if isinstance(block, TextBlock):
            text += block.text

    return AskResult(
        text=text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )


async def ask_stream(question: str) -> AsyncGenerator[str, None]:
    """流式调用：逐 token yield 文本片段。"""
    from providers.types import TextDelta

    provider = get_provider()

    async for chunk in provider.stream(
        messages=[Message(role="user", content=[TextBlock(text=question)])],
        system=SYSTEM_PROMPT,
    ):
        if isinstance(chunk, TextDelta):
            yield chunk.text
```

---

## 3.10 Provider 切换测试

```bash
# 测试 Anthropic（默认）
python cli.py

# 临时切换到 OpenAI（不改 .env，命令行覆盖环境变量）
LLM_PROVIDER=openai OPENAI_MODEL=gpt-4o python cli.py

# 测试 Ollama（需要本地运行 Ollama：ollama serve）
LLM_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1 \
  OPENAI_API_KEY=ollama OPENAI_MODEL=llama3.2 python cli.py
```

---

## 3.11 本章检查清单

```
□ providers/ 目录结构完整（types.py、base.py、router.py、anthropic.py、openai.py、gemini.py）

□ get_provider() 能正常返回 Provider 实例
  验证：python -c "from providers.router import get_provider; p = get_provider(); print(p.model_name)"

□ agent.py 已更新，使用 provider.chat() 而非 anthropic.AsyncAnthropic()

□ cli.py 和 main.py 不需要改动，依然正常工作

□ 修改 .env 的 LLM_PROVIDER 后重启，cli.py 用新 Provider 回答

□ （可选）Ollama 本地模型能正常响应
```

---

# 第 4 章：阶段 4 —— 自实现 Agentic Loop ★

> **本章目标**：实现 Agent 的"大脑"——Agentic Loop（循环推理执行器）。
> 这是取代黑箱 SDK 的核心章节：Agent 的工具调用逻辑完全自己掌控，可读、可改、可调试。

---

## 4.1 什么是 Agentic Loop？

**没有 Agentic Loop 时**，LLM 只能做一问一答：

```
用户问 → LLM 回答 → 结束
```

有了 Agentic Loop，LLM 可以：

```
用户问
  ↓
LLM 思考 → "我需要查数据库"
  ↓ 调用工具
数据库返回结果
  ↓ 结果发回给 LLM
LLM 再思考 → "还需要查汇率"
  ↓ 调用工具
汇率 API 返回
  ↓ 结果发回给 LLM
LLM 最终回答 → "项目 A 的美元收入是 XXX"
```

每次"工具调用 + 等待结果 + 继续思考"算一轮（turn）。Loop 在没有工具调用时结束，或达到最大轮次时强制结束。

**直观理解**：把 LLM 想象成一个能做事的助手，Agentic Loop 是他的"工作流程"：接到任务 → 思考 → 用工具查 → 看结果 → 再思考 → ... → 给出结论。

---

## 4.2 目录结构

```
my-agent/
├── agent/
│   ├── __init__.py      ← 只做导出（from .api import ...）
│   ├── api.py           ← 对外接口（ask、ask_stream、AskResult）
│   ├── loop.py          ← ★ Agentic Loop 核心实现
│   ├── state.py         ← Loop 状态对象
│   └── executor.py      ← 工具并行执行器
├── providers/           ← 第 3 章
└── ...
```

```bash
mkdir -p agent
touch agent/__init__.py
```

---

## 4.3 Loop 状态对象 `agent/state.py`

```python
# agent/state.py
"""
Agentic Loop 的状态对象。

设计原则：不可变（frozen=True）。
每次迭代不修改已有状态，而是创建一个包含更新后数据的新对象。
这样可以保留完整的状态历史，便于调试和审计。
"""
from dataclasses import dataclass, field, replace
from providers.types import Message, Usage


@dataclass(frozen=True)   # frozen=True：创建后不允许修改任何字段
class LoopState:
    """
    Loop 的完整状态快照。

    每次迭代开始和结束时，Loop 都有一个对应的 LoopState。
    调试时可以打印每一轮的状态，观察 Loop 的行为。
    """
    messages: tuple          # 消息历史（用 tuple 而非 list 保证不可变）
    turn_count: int = 0      # 已完成的轮次数
    total_usage: Usage = field(default_factory=Usage)  # 累计 Token 用量
    last_transition: str = "initial"   # 上次是为何继续（调试用）

    def with_messages(self, new_messages: list[Message]) -> "LoopState":
        """返回一个 messages 更新了的新状态（其他字段不变）。"""
        return replace(self, messages=tuple(new_messages))

    def next_turn(self, transition: str, additional_usage: Usage | None = None) -> "LoopState":
        """返回一个轮次 +1 的新状态。"""
        new_usage = self.total_usage
        if additional_usage:
            new_usage = Usage(
                input_tokens=self.total_usage.input_tokens + additional_usage.input_tokens,
                output_tokens=self.total_usage.output_tokens + additional_usage.output_tokens,
                cache_read_tokens=self.total_usage.cache_read_tokens + additional_usage.cache_read_tokens,
            )
        return replace(
            self,
            turn_count=self.turn_count + 1,
            total_usage=new_usage,
            last_transition=transition,
        )
```

---

## 4.4 工具执行器 `agent/executor.py`

```python
# agent/executor.py
"""
并行工具执行器。

LLM 在一轮里可能同时决定调用多个工具（比如"查天气"和"查汇率"同时执行）。
ToolExecutor 用 asyncio.gather() 并行执行，比串行快很多。

任意一个工具失败时，返回 is_error=True 的结果，而不是整体崩溃，
这样 LLM 能看到错误信息并决定如何继续（重试、换工具、或告知用户）。
"""
import asyncio
from providers.types import ToolUseBlock, ToolResultBlock


class ToolExecutor:

    def __init__(self, registry):
        """
        registry：工具注册表，根据工具名找到工具实现。
        第 5 章会实现 ToolRegistry，现在暂时传入 None 也能运行（没有工具的情况）。
        """
        self.registry = registry

    async def execute_all(self, tool_calls: list[ToolUseBlock]) -> list[ToolResultBlock]:
        """
        并行执行所有工具调用。

        返回和 tool_calls 顺序一致的 ToolResultBlock 列表。
        即使某个工具失败，也会返回对应位置的错误结果，不跳过。
        """
        if not tool_calls:
            return []

        tasks = [self._execute_one(tc) for tc in tool_calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_one(self, tool_call: ToolUseBlock) -> ToolResultBlock:
        """执行单个工具调用，捕获所有异常，包装成 ToolResultBlock。"""

        if self.registry is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content="错误：未配置工具注册表（ToolRegistry）",
                is_error=True,
            )

        tool = self.registry.get(tool_call.name)

        if tool is None:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"错误：工具 '{tool_call.name}' 未注册。已注册的工具：{self.registry.list_names()}",
                is_error=True,
            )

        try:
            result = await tool.execute(tool_call.input)
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=str(result),
                is_error=False,
            )
        except Exception as e:
            return ToolResultBlock(
                tool_use_id=tool_call.id,
                content=f"工具执行失败（{tool_call.name}）: {type(e).__name__}: {e}",
                is_error=True,
            )
```

---

## 4.5 ★ 核心实现：Agentic Loop `agent/loop.py`

```python
# agent/loop.py
"""
Agentic Loop 核心实现。

这是整个项目最关键的文件，实现了：
  1. while True 主循环（驱动 Agent 持续工作）
  2. 工具调用检测 → 并行执行 → 结果回填
  3. 多种终止条件（无工具调用 / 超出轮次限制 / 外部中断）
  4. 流式和非流式两种模式

与 claude-agent-sdk 的黑箱不同，这里每一行都是你能读、改、调试的代码。
"""
from dataclasses import dataclass
from typing import Callable
from providers.base import BaseProvider
from providers.types import (
    Message, ToolDefinition, TextBlock, ToolUseBlock,
    ContentBlock, MessageStop, TextDelta, Usage,
)
from .state import LoopState
from .executor import ToolExecutor


# ── 终止原因常量 ───────────────────────────────────────────────────────────────

STOP_COMPLETED = "completed"   # 正常完成（LLM 不再需要工具）
STOP_MAX_TURNS = "max_turns"   # 达到最大轮次
STOP_ABORTED   = "aborted"     # 被外部中断（未来扩展用）


@dataclass
class LoopResult:
    """Agentic Loop 的最终结果。"""
    text: str           # LLM 的最终回答文字
    total_usage: Usage  # 全部轮次的累计 Token 用量
    turn_count: int     # 实际执行的轮次数
    stop_reason: str    # 终止原因（completed / max_turns / aborted）


async def run_agent_loop(
    prompt: str,
    provider: BaseProvider,
    system: str = "",
    tools: list[ToolDefinition] | None = None,
    executor: ToolExecutor | None = None,
    max_turns: int = 10,
    max_tokens: int = 4096,
    on_text_delta: Callable[[str], None] | None = None,
) -> LoopResult:
    """
    Agentic Loop 主函数。

    参数说明：
        prompt         用户的问题或任务描述
        provider       LLM Provider（第 3 章实现的抽象层）
        system         系统提示词（给 LLM 的工作说明）
        tools          LLM 可以使用的工具列表（空列表表示无工具）
        executor       工具执行器（有工具时必须传入）
        max_turns      最多循环几轮（防止无限循环，消耗过多 Token）
        max_tokens     每轮最大输出 Token
        on_text_delta  流式文本回调（传入则启用流式模式）

    返回：
        LoopResult 包含最终文字、用量统计、轮次数、终止原因
    """
    # 初始化状态：消息历史只有用户的第一条消息
    initial_messages = [Message(role="user", content=[TextBlock(text=prompt)])]
    state = LoopState(messages=tuple(initial_messages))

    # ── 主循环 ─────────────────────────────────────────────────────────────────
    while True:

        # [检查] 轮次限制
        if state.turn_count >= max_turns:
            return LoopResult(
                text=f"（已达最大轮次限制 {max_turns}，任务可能未完成）",
                total_usage=state.total_usage,
                turn_count=state.turn_count,
                stop_reason=STOP_MAX_TURNS,
            )

        # [执行] 调用 LLM，收集本轮输出
        text_chunks: list[str] = []
        tool_calls: list[ToolUseBlock] = []
        turn_usage = Usage()

        if on_text_delta:
            # 流式模式：边生成边通过回调传出文本
            # 注意：工具调用参数也是流式传输的，需要累积后解析
            pending_tool_inputs: dict[str, str] = {}  # tool_id → 累积的 JSON 字符串
            pending_tool_meta: dict[str, tuple] = {}  # tool_id → (name,)

            async for chunk in provider.stream(
                messages=list(state.messages),
                system=system,
                tools=tools or None,
                max_tokens=max_tokens,
            ):
                from providers.types import ToolInputDelta, MessageStart

                if isinstance(chunk, TextDelta):
                    text_chunks.append(chunk.text)
                    on_text_delta(chunk.text)

                elif isinstance(chunk, ToolInputDelta):
                    pending_tool_inputs.setdefault(chunk.tool_id, "")
                    pending_tool_inputs[chunk.tool_id] += chunk.partial_json

                elif isinstance(chunk, MessageStop):
                    turn_usage = chunk.usage
                    # 流结束后，从累积的 JSON 重建 ToolUseBlock
                    # （这里简化处理，实际需要从流事件中获取 tool name）

        else:
            # 非流式模式：等待完整响应
            response = await provider.chat(
                messages=list(state.messages),
                system=system,
                tools=tools or None,
                max_tokens=max_tokens,
            )

            for block in response.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(block)

            turn_usage = response.usage

        # 累积 Token 用量
        state = state.next_turn("processing", turn_usage)

        # [判断] 是否有工具调用
        if not tool_calls:
            # 没有工具调用 → LLM 给出了最终答案，Loop 结束
            return LoopResult(
                text="".join(text_chunks),
                total_usage=state.total_usage,
                turn_count=state.turn_count,
                stop_reason=STOP_COMPLETED,
            )

        # [执行] 并行执行所有工具
        if executor is None:
            # 有工具调用但没有 executor，这是编程错误
            return LoopResult(
                text="（错误：Agent 决定使用工具，但未配置 ToolExecutor）",
                total_usage=state.total_usage,
                turn_count=state.turn_count,
                stop_reason=STOP_ABORTED,
            )

        print(f"  [Loop] 第 {state.turn_count} 轮，执行 {len(tool_calls)} 个工具调用")
        tool_results = await executor.execute_all(tool_calls)

        # [构建] 下一轮的消息历史
        # 协议要求：
        #   1. 把本轮的 assistant 回复（包含 ToolUseBlock）加入历史
        #   2. 把工具结果（ToolResultBlock）作为 user 消息加入历史
        new_messages = list(state.messages)

        # assistant 消息：本轮文字 + 工具调用决策
        assistant_content: list[ContentBlock] = []
        if text_chunks:
            assistant_content.append(TextBlock(text="".join(text_chunks)))
        assistant_content.extend(tool_calls)
        new_messages.append(Message(role="assistant", content=assistant_content))

        # user 消息：工具执行结果
        new_messages.append(Message(role="user", content=tool_results))

        # [更新] 状态，进入下一轮
        state = LoopState(
            messages=tuple(new_messages),
            turn_count=state.turn_count,
            total_usage=state.total_usage,
            last_transition="tool_use",
        )
        # → 继续 while True
```

---

## 4.6 新建 `agent/agent.py`（对外接口）

> **注意**：第 1-3 章的 `agent.py` 到这里可以删除。
> 第 4 章起改用 `agent/` 包，`agent.py` 会被 `agent/` 目录遮蔽。
> `agent/__init__.py` 只做导出，实现放在 `agent/agent.py`。

```python
# agent/agent.py
# 对外暴露的简洁接口：ask() 和 ask_stream()
# cli.py、main.py、测试代码都从这里导入，不直接接触 loop.py

import asyncio
from asyncio import Queue
from dataclasses import dataclass
from typing import AsyncGenerator

from .loop import run_agent_loop, LoopResult
from providers.router import get_provider

SYSTEM_PROMPT = "你是一个智能助手，请用中文回答问题，回答要简洁准确。"


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1


async def ask(question: str) -> AskResult:
    """非流式调用，使用 Agentic Loop。"""
    provider = get_provider()

    result: LoopResult = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        max_turns=10,
    )

    return AskResult(
        text=result.text,
        input_tokens=result.total_usage.input_tokens,
        output_tokens=result.total_usage.output_tokens,
        turn_count=result.turn_count,
    )


async def ask_stream(question: str) -> AsyncGenerator[str, None]:
    """流式调用，通过 on_text_delta 回调实时传出文本。"""
    queue: Queue[str | None] = Queue()

    def on_delta(text: str):
        queue.put_nowait(text)

    async def run_loop():
        provider = get_provider()
        await run_agent_loop(
            prompt=question,
            provider=provider,
            system=SYSTEM_PROMPT,
            max_turns=10,
            on_text_delta=on_delta,
        )
        queue.put_nowait(None)

    loop_task = asyncio.create_task(run_loop())

    while True:
        chunk = await queue.get()
        if chunk is None:
            break
        yield chunk

    await loop_task
```

`agent/__init__.py` 只做一行导出，不写任何实现：

```python
# agent/__init__.py
from .api import ask, ask_stream, AskResult
```

这样 `from agent import ask` 依然正常工作，`__init__.py` 保持干净。

> **后续更新**：`agent/agent.py` 这套"单 Agent 直接对话"接口在第 10 章引入
> Coordinator 架构后被逐步架空——`ask_stream()`/`clear_session()` 先搬到
> `coordinator/agent.py`（10.4b），最终 `ask()` 本身也统一改成调用
> `CoordinatorAgent.run()`（10.5），`agent/agent.py` 整个文件被删除。
> `agent/loop.py`、`agent/executor.py` 这些底层组件保留，继续被
> `sub_agents/base.py` 复用；只是不再有 `agent/agent.py` 这层"对外接口"，
> CLI 和 Web 接口统一走 `coordinator/agent.py`。
>
> **再后续更新**：`agent/agent.py` 删除之后，`agent/` 这个包名本身也不太
> 准确了——里面已经没有任何"Agent"（没有 system prompt、没有对话入口），
> 纯粹是被 `sub_agents/base.py` 复用的底层执行引擎。于是整个目录改名为
> `agent_core/`：`agent/loop.py`→`agent_core/loop.py`，
> `agent/executor.py`→`agent_core/executor.py`，
> `agent/state.py`→`agent_core/state.py`，
> `agent/context.py`→`agent_core/context.py`。`sub_agents/base.py` 里
> `from agent.loop import run_agent_loop` 等导入相应改成
> `from agent_core.loop import run_agent_loop`。

---

## 4.7 Agentic Loop 的工作流程图

```
run_agent_loop(prompt, provider, tools, ...)
        │
        │ 初始化：state = LoopState(messages=[用户消息])
        │
        ▼
┌─────────────── while True ───────────────────────┐
│                                                   │
│  [1] 轮次限制检查                                 │
│      turn_count >= max_turns ──→ 返回 STOP_MAX_TURNS │
│                                                   │
│  [2] 调用 LLM（provider.chat 或 provider.stream） │
│      收集文本片段（text_chunks）                  │
│      收集工具调用（tool_calls）                   │
│                                                   │
│  [3] 没有工具调用？                               │
│      是 ──→ 返回最终文字，STOP_COMPLETED           │
│                                                   │
│  [4] 并行执行所有工具                             │
│      executor.execute_all(tool_calls)             │
│      → 返回 tool_results 列表                     │
│                                                   │
│  [5] 构建下一轮消息历史                           │
│      messages += [assistant 消息（含工具调用）]   │
│      messages += [user 消息（工具执行结果）]      │
│                                                   │
│  [6] 更新 state，继续循环                         │
└───────────────────────────────────────────────────┘
```

---

## 4.8 测试 Agentic Loop

```bash
# 基础测试（目前没有工具，只测单轮对话）
python cli.py
# 输入任何问题，验证仍然能正常回答

# 验证 turn_count
python -c "
import asyncio
from agent import ask
async def test():
    result = await ask('1+1等于几')
    print('turn_count:', result.turn_count)   # 无工具时应该是 1
asyncio.run(test())
"
```

---

## 4.9 本章检查清单

```
□ agent/ 目录结构完整（loop.py、state.py、executor.py）

□ run_agent_loop() 无工具时单轮完成，stop_reason="completed"
  验证：python -c "import asyncio; from agent import ask; ..."

□ agent.py 已更新使用 run_agent_loop()

□ cli.py 和 main.py 不需要改动，功能正常

□ 能理解 Agentic Loop 的工作流程（什么时候继续，什么时候结束）

□ 能理解为什么工具结果要以 user 角色发回给 LLM
```

**全部打勾之后，进入第 5 章。**

---

# 第 5 章：阶段 5 —— 工具调用

> **本章目标**：让 Agent 不再只会聊天，能调用真实工具（函数、API、数据库）获取数据。
> 这是 Agent 和普通聊天机器人最本质的区别。

---

## 5.1 本章新增内容

```
my-agent/
├── tools/
│   ├── __init__.py
│   ├── base.py          ← 工具抽象基类
│   ├── registry.py      ← 工具注册表
│   └── builtin/
│       ├── __init__.py
│       └── calculator.py  ← 示例：计算器工具
└── ...
```

```bash
mkdir -p tools/builtin
touch tools/__init__.py tools/builtin/__init__.py
```

---

## 5.2 工具是怎么工作的？

先看完整的工具调用流程：

```
1. 你注册工具：把 CalculatorTool 注册到 ToolRegistry
   （告诉 LLM："我有一个叫 calculator 的工具，能计算数学表达式"）

2. 用户提问："12345 乘以 67890 等于多少？"

3. LLM 思考：
   "这是数学计算，我有 calculator 工具，我要调用它"
   → 返回 ToolUseBlock{name="calculator", input={"expression": "12345 * 67890"}}

4. Agentic Loop 检测到 ToolUseBlock，调用 ToolExecutor

5. ToolExecutor 找到 CalculatorTool，执行 calculator.execute({"expression": "12345 * 67890"})
   → 返回 "838102050"

6. 结果以 ToolResultBlock 形式发回给 LLM

7. LLM 看到结果，给出最终回答："12345 乘以 67890 等于 838,102,050"
```

关键：LLM 自己决定**是否调用工具**以及**调用哪个工具**，你的代码负责**执行工具**并**把结果发回**。

---

## 5.3 工具基类 `tools/base.py`

```python
# tools/base.py
"""
工具（Tool）的抽象基类。

所有工具都继承 BaseTool，实现以下抽象属性和方法：
  - name：工具名称（LLM 用这个名字来"点名"调用工具）
  - description：工具描述（★ 非常重要！LLM 靠这段描述决定何时调用）
  - input_schema：参数定义（JSON Schema 格式，LLM 据此填写参数）
  - execute()：实际执行逻辑，返回字符串结果
"""
from abc import ABC, abstractmethod
from providers.types import ToolDefinition


class BaseTool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """
        工具名称。
        命名规范：小写字母+下划线，如 "calculator"、"query_weather"。
        LLM 通过这个名字来调用工具。
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """
        工具描述。★ 这是最重要的字段！

        写作原则：
        1. 说清楚"什么时候用这个工具"（触发条件）
        2. 说清楚"能做什么、不能做什么"（能力边界）
        3. 如果有特殊格式要求，在这里说明

        好的描述：
          "计算数学表达式，返回数值结果。
           当用户需要进行加减乘除、乘方、取余等数学运算时使用。
           输入必须是有效的 Python 数学表达式（如 '2 + 3 * 4'）。"

        差的描述：
          "计算"  ← 太模糊，LLM 不知道什么时候用
        """
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """
        工具接受的参数定义，JSON Schema 格式。

        标准格式：
        {
          "type": "object",
          "properties": {
            "参数名": {
              "type": "参数类型",         # string / number / integer / boolean / array / object
              "description": "参数说明"   # LLM 填参数时参考的说明
            }
          },
          "required": ["必填参数列表"]
        }
        """
        ...

    @property
    def definition(self) -> ToolDefinition:
        """返回 ToolDefinition 对象（发给 LLM 时用）。不需要重写。"""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    @abstractmethod
    async def execute(self, inputs: dict) -> str:
        """
        工具执行逻辑。

        参数：
            inputs - LLM 填写的参数字典（和 input_schema 定义的字段对应）

        返回：
            字符串结果（LLM 会读这个字符串来了解工具执行的结果）

        注意：
            - 永远不要抛异常！有错误时返回描述错误的字符串。
            - 返回内容越精简越好（LLM 需要处理这些内容，Token 有限）。
            - 只返回 LLM 需要的信息，过滤掉无关字段。
        """
        ...
```

---

## 5.4 工具注册表 `tools/registry.py`

```python
# tools/registry.py
"""
工具注册表（ToolRegistry）。

统一管理所有工具的注册和查找。
Agentic Loop 里的 ToolExecutor 通过注册表找到工具实现。
"""
from .base import BaseTool
from providers.types import ToolDefinition


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> "ToolRegistry":
        """注册一个工具。返回 self，支持链式调用。"""
        self._tools[tool.name] = tool
        print(f"[ToolRegistry] 已注册工具：{tool.name}")
        return self

    def get(self, name: str) -> BaseTool | None:
        """根据名称查找工具，找不到返回 None。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """返回所有已注册工具的名称列表（用于错误提示）。"""
        return list(self._tools.keys())

    def get_all_definitions(self) -> list[ToolDefinition]:
        """
        返回所有工具的 ToolDefinition 列表。
        发给 LLM 时传入这个列表，LLM 就知道有哪些工具可用。
        """
        return [t.definition for t in self._tools.values()]

    @classmethod
    def default(cls) -> "ToolRegistry":
        """
        创建包含所有内置工具的默认注册表。

        ★ 替换点：在这里添加你自己的工具。
        """
        from .builtin.calculator import CalculatorTool

        registry = cls()
        registry.register(CalculatorTool())
        # 按需添加更多工具：
        # registry.register(WeatherTool())
        # registry.register(DatabaseTool())
        return registry
```

---

## 5.5 Coding Agent 核心工具

本项目是一个 Coding Agent，工具全部围绕代码任务设计。以下三个工具是基础，后续子 Agent 会各自使用其中一部分。

---

### 工具 1：读取文件 `tools/builtin/read_file.py`

```python
# tools/builtin/read_file.py
"""
读取本地文件内容。
Coding Agent 最常用的工具——让 Agent 能看到用户的代码文件。
安全限制：只允许读取工作目录内的文件，禁止路径穿越（../）。
"""
from pathlib import Path
from tools.base import BaseTool
from core.workspace import get_workspace


class ReadFileTool(BaseTool):

    def __init__(self, workspace: str | Path | None = None):
        # 默认使用全局统一工作目录（环境变量 WORKSPACE）；传参仅用于测试/临时覆盖。
        # 这样 sub_agents 里 ReadFileTool() 无参实例化就自动跟其他工具用同一个目录。
        self.workspace = Path(workspace).resolve() if workspace is not None else get_workspace()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "读取指定路径的文件内容，返回完整代码文本。"
            "当用户提到某个文件、让你审查代码、或需要了解已有实现时调用此工具。"
            "支持 .py、.js、.ts、.go、.java、.md 等文本格式。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径，相对于工作目录，如 'src/auth.py' 或 'main.go'",
                }
            },
            "required": ["path"],
        }

    async def execute(self, inputs: dict) -> str:
        raw_path = inputs.get("path", "").strip()
        if not raw_path:
            return "错误：path 不能为空"

        # 安全检查：解析绝对路径，确保不超出工作目录
        target = (self.workspace / raw_path).resolve()
        if not str(target).startswith(str(self.workspace)):
            return f"错误：禁止访问工作目录之外的文件（路径穿越检测）"

        if not target.exists():
            return f"错误：文件不存在：{raw_path}"

        if not target.is_file():
            return f"错误：{raw_path} 是目录，请指定具体文件路径"

        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"错误：{raw_path} 不是文本文件（可能是二进制文件）"

        # 返回完整文件内容，不做长度截断。
        # 截断会让 Agent 看到的代码残缺（比如只看到前 200 行就去审查/修改），
        # 直接影响判断的正确性。上下文窗口超限由 agent_core/context.py 的
        # 压缩机制统一兜底，不应在读文件这一层提前砍内容。
        lines = text.splitlines()
        return f"文件：{raw_path}（{len(lines)} 行）\n{'─' * 40}\n{text}\n{'─' * 40}"
```

---

### 工具 2：执行 Python 代码 `tools/builtin/run_python.py`

```python
# tools/builtin/run_python.py
"""
在沙箱环境中执行 Python 代码片段，返回 stdout / stderr。

用途：
- 让 Agent 验证生成的代码是否能跑通
- 执行单元测试，看输出结果
- 快速验算某个逻辑

安全限制：
- 超时 10 秒自动终止（防止死循环）
- 禁止 import os.system / subprocess（防止命令注入）
- 在子进程中运行，不影响主进程
"""
import asyncio
import sys
import textwrap
from tools.base import BaseTool
from core.workspace import get_workspace

# 危险模块黑名单（在代码字符串层面做简单检查）
_BLOCKED_IMPORTS = [
    "subprocess", "os.system", "shutil.rmtree",
    "open('/", 'open("/', "__import__",
]


class RunPythonTool(BaseTool):

    @property
    def name(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "执行一段 Python 代码并返回输出结果（stdout + stderr）。"
            "用于：验证生成的代码是否正确、运行单元测试、快速验证逻辑。"
            "超时限制 10 秒。不能访问网络或文件系统。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码（字符串，支持多行）",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（秒），默认 10，最大 30",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 30,
                },
            },
            "required": ["code"],
        }

    async def execute(self, inputs: dict) -> str:
        code = inputs.get("code", "").strip()
        timeout = min(int(inputs.get("timeout", 10)), 30)

        if not code:
            return "错误：code 不能为空"

        # 简单安全检查
        for blocked in _BLOCKED_IMPORTS:
            if blocked in code:
                return f"错误：代码包含被禁止的操作（{blocked}），出于安全考虑无法执行"

        # 用 asyncio.create_subprocess_exec 在子进程运行，隔离环境
        # cwd 设为统一工作目录：代码里的相对路径都相对工作目录，和其他工具一致
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", textwrap.dedent(code),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(get_workspace()),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"错误：代码执行超时（>{timeout}s），进程已终止"

        output_parts = []
        if stdout:
            output_parts.append(f"[stdout]\n{stdout.decode('utf-8', errors='replace')}")
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")
        if proc.returncode != 0:
            output_parts.append(f"[退出码] {proc.returncode}")

        return "\n".join(output_parts) if output_parts else "[无输出，代码执行成功]"
```

---

### 工具 3：搜索代码符号 `tools/builtin/search_code.py`

```python
# tools/builtin/search_code.py
"""
在工作目录中搜索代码符号（函数名、类名、关键词）。
相当于 grep，帮助 Agent 在不知道具体文件路径时定位代码位置。
"""
import re
from pathlib import Path
from tools.base import BaseTool
from core.workspace import get_workspace

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".go", ".java", ".cpp", ".c", ".rs", ".md"}


class SearchCodeTool(BaseTool):

    def __init__(self, workspace: str | Path | None = None):
        # 默认使用全局统一工作目录（环境变量 WORKSPACE）；传参仅用于测试/临时覆盖。
        self.workspace = Path(workspace).resolve() if workspace is not None else get_workspace()

    @property
    def name(self) -> str:
        return "search_code"

    @property
    def description(self) -> str:
        return (
            "在代码库中搜索函数名、类名、变量名或任意关键词，返回匹配的文件和行号。"
            "当不知道某个函数定义在哪里、或需要找所有使用某个变量的地方时使用。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要搜索的关键词、函数名或类名",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "文件名通配符，如 '*.py'（默认搜索所有代码文件）",
                    "default": "*",
                },
                "max_results": {
                    "type": "integer",
                    "description": "最多返回多少条结果（默认 20）",
                    "default": 20,
                },
            },
            "required": ["keyword"],
        }

    async def execute(self, inputs: dict) -> str:
        keyword = inputs.get("keyword", "").strip()
        file_pattern = inputs.get("file_pattern", "*")
        # 默认 20 条只是给 Agent 一个合理起点，不再强制封顶——
        # 之前的 min(..., 50) 硬上限会让 Agent 即便显式要更多结果也拿不到，
        # 排查"某符号所有引用"这类需求时会漏掉真正相关的位置。
        max_results = int(inputs.get("max_results", 20))

        if not keyword:
            return "错误：keyword 不能为空"

        matches = []
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)

        # 遍历工作目录
        for path in self.workspace.rglob(file_pattern):
            if not path.is_file():
                continue
            if path.suffix not in _CODE_EXTENSIONS:
                continue
            if any(part.startswith(".") or part == "__pycache__" for part in path.parts):
                continue

            try:
                lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue

            rel_path = path.relative_to(self.workspace)
            for lineno, line in enumerate(lines, 1):
                if pattern.search(line):
                    matches.append(f"{rel_path}:{lineno}  {line.strip()}")
                    if len(matches) >= max_results:
                        break

            if len(matches) >= max_results:
                break

        if not matches:
            return f"未找到包含 '{keyword}' 的代码（搜索范围：{file_pattern}）"

        result = f"搜索 '{keyword}' 找到 {len(matches)} 条结果：\n"
        result += "\n".join(matches)
        if len(matches) >= max_results:
            result += f"\n\n（仅显示前 {max_results} 条，使用更精确的关键词缩小范围）"
        return result
```

---

## 5.6 把工具集成到 Agentic Loop

更新 `agent.py`，注册三个 Coding 工具：

```python
# agent.py（完整替换）
from dataclasses import dataclass
from typing import AsyncGenerator
from agent.loop import run_agent_loop
from agent.executor import ToolExecutor
from providers.router import get_provider
from tools.registry import ToolRegistry
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from tools.builtin.search_code import SearchCodeTool

SYSTEM_PROMPT = """
你是一个专业的 Coding Agent，帮助用户完成代码相关任务。

你有以下工具：
- read_file：读取代码文件内容
- search_code：在代码库中搜索函数名、类名或关键词
- run_python：执行 Python 代码片段并返回结果

工作原则：
1. 先用工具了解现有代码，再给出建议，不要凭空猜测
2. 发现问题时给出具体的文件名和行号
3. 生成代码后主动用 run_python 验证能否正常执行
4. 输出代码时使用代码块格式（```python）
"""


@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1


# 全局工具注册（启动时初始化一次）
_registry = ToolRegistry()
_registry.register(ReadFileTool(workspace="."))
_registry.register(RunPythonTool())
_registry.register(SearchCodeTool(workspace="."))
_executor = ToolExecutor(_registry)


async def ask(question: str) -> AskResult:
    provider = get_provider()

    result = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        tools=_registry.get_all_definitions(),
        executor=_executor,
        max_turns=10,
    )

    return AskResult(
        text=result.text,
        input_tokens=result.total_usage.input_tokens,
        output_tokens=result.total_usage.output_tokens,
        turn_count=result.turn_count,
    )
```

---

## 5.7 测试工具调用

启动服务：`python main.py`

**测试代码搜索（应触发 search_code 工具）：**

```bash
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "帮我找一下项目里定义了哪些 class"}'
```

**测试代码执行（应触发 run_python 工具）：**

```bash
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "帮我写一个快速排序函数并验证它能正确运行"}'
```

预期：终端出现工具调用日志（`[ToolExecutor] run_python`），响应包含代码和执行结果。

---

## 5.8 扩展更多 Coding 工具（参考模板）

以下是两个可选扩展工具，需要时按同样模式添加到 `tools/builtin/` 并注册：

**写入文件工具 `tools/builtin/write_file.py`（让 Agent 能生成并保存代码）：**

写入必须走 `core/workspace.py` 的 `write_text_sandboxed()`——它会把路径限定在工作目录内，
拦截 `../` 穿越和绝对路径。**绝不要直接 `Path(inputs["path"]).write_text(...)`**，那样
模型可以写到任意系统路径（尤其飞书/HTTP 是外部入口）：

```python
from tools.base import BaseTool
from core.workspace import write_text_sandboxed, PathTraversalError


class WriteFileTool(BaseTool):
    @property
    def name(self): return "write_file"
    @property
    def description(self): return "把生成的代码写入指定路径的文件（限定在工作目录内）。仅在用户明确要求保存时使用。"
    @property
    def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径，相对工作目录，如 'out/auth.py'"},
                "content": {"type": "string", "description": "写入的文件内容"},
            },
            "required": ["path", "content"],
        }
    async def execute(self, inputs: dict) -> str:
        # 限定在工作目录内，禁止写到工作目录之外（含绝对路径 / 路径穿越）
        try:
            target = write_text_sandboxed(inputs["path"], inputs["content"])
        except PathTraversalError as e:
            return f"错误：{e}"
        return f"已写入 {target}（{len(inputs['content'])} 字符）"
```

**列出目录工具 `tools/builtin/list_dir.py`（让 Agent 了解项目结构）：**

同样限定在工作目录内，`path` 相对工作目录解析：

```python
from tools.base import BaseTool
from core.workspace import get_workspace, resolve_safe_path, PathTraversalError


class ListDirTool(BaseTool):
    @property
    def name(self): return "list_dir"
    @property
    def description(self): return "列出指定目录下的文件和子目录。了解项目结构时使用。"
    @property
    def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，相对工作目录，默认为工作目录根", "default": "."},
                "depth": {"type": "integer", "description": "显示深度（1=仅当前层，默认 2）", "default": 2},
            },
        }
    async def execute(self, inputs: dict) -> str:
        # 限定在工作目录内，禁止路径穿越
        try:
            base = resolve_safe_path(inputs.get("path", "."))
        except PathTraversalError as e:
            return f"错误：{e}"
        if not base.exists():
            return f"错误：目录不存在：{inputs.get('path', '.')}"
        depth = min(int(inputs.get("depth", 2)), 4)
        lines = [str(base.relative_to(get_workspace())) or "."]
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(base)
            if len(rel.parts) > depth: continue
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts): continue
            indent = "  " * (len(rel.parts) - 1)
            lines.append(f"{indent}{'📁 ' if p.is_dir() else '📄 '}{p.name}")
        # 返回完整目录树，不截断——截断到前 100 项会让 Agent 误以为
        # 项目里没有后面的文件，从而漏读关键代码。depth 参数已能控制展开层级。
        return "\n".join(lines)
```

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 替换成你使用的天气 API
                resp = await client.get(
                    f"https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "q": city,
                        "appid": "你的 API Key",
                        "units": "metric",   # 摄氏度
                        "lang": "zh_cn",     # 中文描述
                    }
                )
                resp.raise_for_status()
                data = resp.json()

                # 只返回 LLM 需要的关键字段
                return (
                    f"城市：{data['name']}\n"
                    f"温度：{data['main']['temp']}°C\n"
                    f"天气：{data['weather'][0]['description']}\n"
                    f"湿度：{data['main']['humidity']}%"
                )
        except httpx.TimeoutException:
            return "错误：天气 API 请求超时，请稍后重试"
        except httpx.HTTPStatusError as e:
            return f"错误：API 返回 {e.response.status_code}，城市名可能不正确"
        except Exception as e:
            return f"查询天气失败：{e}"
```

在 `ToolRegistry.default()` 里注册：

```python
# tools/registry.py 的 default() 方法里加一行
from .builtin.weather import WeatherTool
registry.register(WeatherTool())
```

---

## 5.9 工具设计最佳实践

| 原则 | 说明 | 反例 → 正例 |
|------|------|-----------|
| **描述要具体** | LLM 靠描述决定何时调用 | "查询" → "查询指定订单号的物流状态，当用户询问包裹到哪里了时使用" |
| **参数要少而精** | 每多一个参数，LLM 填错的概率增加 | 5个可选参数 → 只保留必要的1-2个 |
| **永远不抛异常** | 出错返回描述错误的字符串 | `raise ValueError(...)` → `return "错误：..."` |
| **返回要精简** | 只返回 LLM 需要的字段 | 返回整个 API 响应 JSON → 只返回关键 3-5 个字段 |
| **参数有示例** | 在 description 里给出例子 | "日期" → "日期，格式 YYYY-MM-DD，如 2025-01-01" |

---

## 5.10 本章检查清单

```
□ tools/ 目录结构完整（base.py、registry.py、builtin/calculator.py）

□ CalculatorTool 能正确执行
  验证：python -c "
  import asyncio
  from tools.builtin.calculator import CalculatorTool
  tool = CalculatorTool()
  result = asyncio.run(tool.execute({'expression': '12345 * 67890'}))
  print(result)   # 应该输出：838102050
  "

□ agent.py 已更新，向 run_agent_loop 传入工具列表

□ 发送含数学计算的问题，Agent 调用了计算器工具并给出正确答案
  验证：curl -X POST .../ask -d '{"question": "234 乘以 567 等于多少"}'
  终端应出现：[Loop] 第 1 轮，执行 1 个工具调用

□ 普通问题不触发工具调用（turn_count 仍然是 1）
```

**全部打勾之后，进入第 6 章。**

---

# 第 6 章：阶段 6 —— Coordinator 模式（主 Agent 编排）

> **本章目标**：把"一个 Agent 干所有事"改成"一个主 Agent 编排，多个专家 Agent 各司其职"。
> 这是从单 Agent 升级到多 Agent 架构的关键章节。

---

## 6.1 为什么需要多 Agent 架构

**单 Agent 全做** 的问题：

1. **系统提示词越来越长**：所有领域的知识、所有工具的说明都堆在一起，LLM 容易混淆
2. **工具越来越多**：工具一多，LLM 容易调用错误的工具
3. **难以维护**：改 A 功能可能影响 B 功能的行为

**Coordinator 模式** 的解法：

```
用户请求
    ↓
CoordinatorAgent（主 Agent）
  └─ 理解意图
  └─ 拆分任务
  └─ 分发子任务给对应专家
  └─ 聚合结果返回给用户
    ↓         ↓         ↓
SubAgent A  SubAgent B  SubAgent C
（代码审查）（文档生成）（测试编写）
```

每个子 Agent 只关注自己领域：系统提示词短而精准，工具少而专，互不干扰。

---

## 6.2 目录结构

```
my-agent/
├── coordinator/
│   ├── __init__.py
│   ├── agent.py         ← CoordinatorAgent（主 Agent，理解意图 + 编排）
│   ├── planner.py       ← 任务拆分（调用 LLM 输出 JSON 计划）
│   └── dispatcher.py    ← 分发执行 + 结果聚合
├── sub_agents/
│   ├── __init__.py
│   ├── base.py          ← SubAgent 基类
│   ├── code_writer.py   ← ★ 代码生成专家（根据需求写代码）
│   ├── code_reviewer.py ← ★ 代码审查专家（发现 Bug、安全漏洞）
│   ├── debugger.py      ← ★ 调试专家（定位并修复 Bug）
│   └── test_writer.py   ← ★ 测试专家（生成单元测试）
└── ...
```

```bash
mkdir -p coordinator sub_agents
touch coordinator/__init__.py sub_agents/__init__.py
```

**四个子 Agent 的分工：**

| 子 Agent | 触发场景 | 使用的工具 |
|---------|---------|----------|
| `code_writer` | 新功能开发、代码生成 | `read_file`（了解上下文）、`run_python`（验证）、`write_file` |
| `code_reviewer` | 审查已有代码、PR Review | `read_file`、`search_code` |
| `debugger` | Bug 复现、错误修复 | `read_file`、`run_python`（复现 Bug）、`search_code` |
| `test_writer` | 生成单元测试 | `read_file`（读源码）、`run_python`（运行测试） |

---

## 6.3 核心概念讲解

### 6.3.1 任务计划的 JSON 格式

Coordinator 的核心是让 LLM 输出一个**结构化任务计划**，然后由 Python 代码来执行这个计划（而不是让 LLM 直接执行）。

为什么这样做？
- **LLM 决策，Python 执行**：LLM 擅长理解意图和规划，Python 擅长精确执行和并发控制
- **可预测**：Python 的分发逻辑 100% 可预测，出错有明确报错
- **可审计**：每次任务计划都是 JSON，可以记录、回放、调试

任务计划格式（Coding Agent 场景示例）：

```json
{
  "tasks": [
    {
      "id": "t1",
      "agent": "code_reviewer",
      "input": "审查 auth.py 中的 login() 函数，重点检查 SQL 注入风险和认证逻辑漏洞",
      "depends_on": []
    },
    {
      "id": "t2",
      "agent": "debugger",
      "input": "根据 t1 的审查结果，定位并修复发现的 Bug，提供修复后的代码",
      "depends_on": ["t1"]
    },
    {
      "id": "t3",
      "agent": "test_writer",
      "input": "为修复后的 login() 函数编写单元测试，覆盖正常登录、密码错误、SQL 注入三个场景",
      "depends_on": ["t2"]
    }
  ]
}
```

`depends_on` 字段控制执行顺序：
- `"depends_on": []`：没有依赖，可以和其他无依赖任务**并行执行**
- `"depends_on": ["t1"]`：等 t1 完成后才执行（t1 的结果会注入到 input 里）

### 6.3.2 拓扑排序实现并行执行

"拓扑排序"听起来复杂，其实思路很简单：

```
把所有任务分成"波次"（wave）：
  波次 1：没有任何依赖的任务（can run now）
  波次 2：只依赖波次 1 中任务的任务
  波次 3：只依赖前两个波次的任务
  ...

同一波次内的任务并行执行（asyncio.gather）
不同波次按顺序执行
```

示例：

```
任务：A（无依赖）、B（无依赖）、C（依赖A）、D（依赖B和C）

波次 1：A、B     ← 同时执行
波次 2：C        ← 等 A 完成后执行
波次 3：D        ← 等 B、C 都完成后执行
```

---

## 6.4 子 Agent 基类 `sub_agents/base.py`

```python
# sub_agents/base.py
"""
SubAgent 基类。

所有专家子 Agent 都继承这个类，只需要实现 system_prompt 和 run() 方法。
run() 方法接收任务描述（字符串），返回处理结果（字符串）。
"""
from abc import ABC, abstractmethod
from agent.loop import run_agent_loop
from agent.executor import ToolExecutor
from providers.router import get_provider
from tools.registry import ToolRegistry


class SubAgent(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """子 Agent 的名称标识（和任务计划里的 agent 字段一致）。"""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """
        子 Agent 的系统提示词（工作说明书）。

        写作要点：
        1. 明确角色：你是谁，专注什么领域
        2. 明确职责：你能做什么，不能做什么
        3. 输出格式：你的结果应该是什么格式
        """
        ...

    @property
    def tools(self):
        """子 Agent 可用的工具列表（默认无工具，子类按需覆盖）。"""
        return []

    async def run(self, task: str, context: dict | None = None) -> str:
        """
        执行任务。

        参数：
            task    - 任务描述（自然语言）
            context - 前置任务的结果（由 dispatcher 注入）

        返回：
            任务结果（字符串）
        """
        # 把上下文注入到任务描述里
        full_task = task
        if context:
            context_str = "\n".join(f"【{k}的结果】\n{v}" for k, v in context.items())
            full_task = f"{task}\n\n参考信息（来自前置任务）：\n{context_str}"

        provider = get_provider()

        # 使用 Agentic Loop 执行任务
        registry = ToolRegistry()
        for tool in self.tools:
            registry.register(tool)

        executor = ToolExecutor(registry) if self.tools else ToolExecutor(None)

        result = await run_agent_loop(
            prompt=full_task,
            provider=provider,
            system=self.system_prompt,
            tools=registry.get_all_definitions() if self.tools else None,
            executor=executor,
            max_turns=10,
        )

        return result.text
```

> **后续更新**：`agent/` 包在第 10 章之后改名为 `agent_core/`（见 4.6 更新说明），
> 上面 `from agent.loop import run_agent_loop`、`from agent.executor import
> ToolExecutor` 两行导入相应变成 `from agent_core.loop import run_agent_loop`、
> `from agent_core.executor import ToolExecutor`，其余逻辑不变。

---

## 6.5 四个 Coding 专家子 Agent

### Agent 1：代码生成 `sub_agents/code_writer.py`

````python
# sub_agents/code_writer.py
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from .base import SubAgent


class CodeWriterAgent(SubAgent):

    @property
    def name(self) -> str:
        return "code_writer"

    @property
    def system_prompt(self) -> str:
        return """
你是一名资深软件工程师，专注于根据需求生成高质量代码。

工作流程：
1. 先用 read_file 了解已有代码结构和风格（如有相关文件）
2. 根据需求编写代码，遵循现有代码的风格规范
3. 用 run_python 执行生成的代码，验证无语法错误和运行时错误
4. 如果测试失败，自动修复并重新验证

输出格式：
- 先说明设计思路（2-3 句）
- 给出完整可运行的代码（用 ```python 代码块）
- 说明关键实现细节和注意事项

代码质量要求：
- 函数必须有类型注解（Type Hints）
- 关键逻辑必须有注释
- 不要引入不必要的依赖
"""

    @property
    def tools(self):
        return [ReadFileTool(), RunPythonTool()]
````

---

### Agent 2：代码审查 `sub_agents/code_reviewer.py`

```python
# sub_agents/code_reviewer.py
from tools.builtin.read_file import ReadFileTool
from tools.builtin.search_code import SearchCodeTool
from .base import SubAgent


class CodeReviewerAgent(SubAgent):

    @property
    def name(self) -> str:
        return "code_reviewer"

    @property
    def system_prompt(self) -> str:
        return """
你是一名资深代码审查工程师（10 年以上经验），专注于发现代码中的问题。

审查优先级（从高到低）：
1. Critical（致命）：SQL 注入、命令注入、路径穿越、硬编码密码、未处理的用户输入
2. Warning（警告）：逻辑错误、边界条件遗漏、空指针、并发问题、明显性能问题
3. Suggestion（建议）：可读性改进、重复代码、命名不规范

工作流程：
1. 用 read_file 读取要审查的文件
2. 用 search_code 查找相关函数的调用方，了解使用上下文
3. 逐行分析，按优先级列出问题

每个问题的输出格式：
[Critical/Warning/Suggestion] 文件名:行号（如有）
问题：具体描述
建议：修复方案或改进方向

如果代码没有明显问题，说明：代码质量良好，列出 1-2 条可选优化建议。
"""

    @property
    def tools(self):
        return [ReadFileTool(), SearchCodeTool()]
```

---

### Agent 3：调试修复 `sub_agents/debugger.py`

````python
# sub_agents/debugger.py
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from tools.builtin.search_code import SearchCodeTool
from .base import SubAgent


class DebuggerAgent(SubAgent):

    @property
    def name(self) -> str:
        return "debugger"

    @property
    def system_prompt(self) -> str:
        return """
你是一名专业的调试工程师，专注于定位和修复 Bug。

调试思路（二分法定位）：
1. 先用 read_file 读取出错的文件，理解代码逻辑
2. 用 run_python 复现 Bug（写一个最小可复现的测试用例）
3. 用 search_code 查找相关函数，追踪 Bug 根因
4. 构造修复方案，用 run_python 验证修复有效
5. 确认修复后没有引入新的问题

输出格式：
**Bug 根因**：（一句话说明根本原因）
**影响范围**：（会影响哪些场景）
**修复方案**：
```python
# 修复后的代码
```
**验证结果**：（run_python 执行结果截图或输出）

如果 Bug 无法复现，说明已尝试的场景和推测可能的原因。
"""

    @property
    def tools(self):
        return [ReadFileTool(), RunPythonTool(), SearchCodeTool()]
````

---

### Agent 4：测试生成 `sub_agents/test_writer.py`

````python
# sub_agents/test_writer.py
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from .base import SubAgent


class TestWriterAgent(SubAgent):

    @property
    def name(self) -> str:
        return "test_writer"

    @property
    def system_prompt(self) -> str:
        return """
你是一名测试工程师，专注于编写高质量的单元测试。

测试覆盖原则：
1. 正常路径（happy path）：功能正常工作的场景
2. 边界条件：空值、最大值、最小值、空列表等
3. 异常路径：传入非法参数、外部服务失败时的行为
4. 安全场景（如涉及用户输入）：SQL 注入、XSS 尝试

工作流程：
1. 用 read_file 读取要测试的源码，理解函数签名和行为
2. 编写 pytest 风格的测试用例
3. 用 run_python 运行测试，确认全部通过
4. 如果测试失败，分析是测试写错了还是被测代码有 Bug，并说明

输出格式：
```python
# test_xxx.py
import pytest
# ... 完整测试代码
```
测试覆盖说明：列出覆盖了哪些场景
运行结果：附上 run_python 的执行输出
"""

    @property
    def tools(self):
        return [ReadFileTool(), RunPythonTool()]
````

---

## 6.6 任务规划器 `coordinator/planner.py`

````python
# coordinator/planner.py
"""
任务规划器：调用 LLM，把用户请求拆分成结构化任务计划（JSON）。
"""
import json
import re
from providers.router import get_provider
from providers.types import Message, TextBlock
from dataclasses import dataclass, field


@dataclass
class TaskSpec:
    """单个任务的规格。"""
    id: str
    agent: str           # 对应哪个子 Agent 的 name
    input: str           # 任务描述（发给子 Agent 的 prompt）
    depends_on: list[str] = field(default_factory=list)


# Coordinator 的系统提示词（★ 替换点：修改可用子 Agent 列表）
_COORDINATOR_SYSTEM = """
你是一个 Coding Agent 的任务协调者。接收用户的代码相关请求，拆分为子任务并以 JSON 格式返回计划。

可用的子 Agent：
- code_writer：代码生成专家，根据需求编写新代码，生成后会自动验证
- code_reviewer：代码审查专家，检查安全漏洞、逻辑错误、代码质量
- debugger：调试专家，复现并修复 Bug，会运行代码验证修复效果
- test_writer：测试专家，生成 pytest 单元测试，覆盖正常/边界/异常场景

任务拆分原则：
- 代码审查 + 修复 + 写测试 → 三个串行任务（review → debug → test_writer）
- 多个独立文件的审查 → 并行任务
- 简单的代码生成请求 → 单个 code_writer 任务

输出格式（严格遵守，不要有其他文字）：
{
  "tasks": [
    {
      "id": "t1",
      "agent": "agent名称（四选一）",
      "input": "给该 agent 的具体任务描述（需包含文件名、函数名等上下文）",
      "depends_on": []
    }
  ]
}

规则：
- depends_on 为 [] 表示可与其他无依赖任务并行执行
- depends_on 为 ["t1"] 表示需要 t1 完成后才能执行（t1 的结果会自动注入）
- 只输出 JSON，不要有任何其他文字
- 如果请求不需要任何子 Agent（如只是聊天），返回 {"tasks": [], "reply": "直接回复的内容"}
"""


async def make_plan(user_request: str) -> tuple[list[TaskSpec], str | None]:
    """
    调用 LLM 生成任务计划。

    返回：
        (TaskSpec 列表, 直接回复文字 or None)
        - 如果有任务：(tasks, None)
        - 如果不需要子 Agent：([], 直接回复文字)
    """
    provider = get_provider()

    response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=user_request)])],
        system=_COORDINATOR_SYSTEM,
        max_tokens=2048,
    )

    raw_text = ""
    for block in response.content:
        if isinstance(block, TextBlock):
            raw_text += block.text

    return _parse_plan(raw_text)


def _parse_plan(text: str) -> tuple[list[TaskSpec], str | None]:
    """解析 LLM 输出的 JSON 任务计划。"""
    # 去掉可能的 Markdown 代码块标记
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()

    # 找到 JSON 边界
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1:
        # 找不到 JSON，把整段文字当作直接回复
        return [], text.strip()

    try:
        plan = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"[Planner] JSON 解析失败：{e}\n原始文本：{text[:300]}")
        return [], text.strip()

    tasks_raw = plan.get("tasks", [])
    direct_reply = plan.get("reply")

    if not tasks_raw:
        return [], direct_reply or text.strip()

    specs = [
        TaskSpec(
            id=t.get("id", f"t{i}"),
            agent=t.get("agent", ""),
            input=t.get("input", ""),
            depends_on=t.get("depends_on", []),
        )
        for i, t in enumerate(tasks_raw)
    ]

    return specs, None
````

---

## 6.7 分发器 `coordinator/dispatcher.py`

```python
# coordinator/dispatcher.py
"""
任务分发器：根据任务计划，按拓扑顺序执行子任务（并行/串行）。
"""
import asyncio
from collections import defaultdict, deque
from .planner import TaskSpec


def _get_sub_agents() -> dict:
    """
    返回"agent名称 → SubAgent实例"的映射。
    ★ 替换点：添加新子 Agent 时在这里注册。
    """
    from sub_agents.code_writer import CodeWriterAgent
    from sub_agents.code_reviewer import CodeReviewerAgent
    from sub_agents.debugger import DebuggerAgent
    from sub_agents.test_writer import TestWriterAgent
    return {
        "code_writer":   CodeWriterAgent(),
        "code_reviewer": CodeReviewerAgent(),
        "debugger":      DebuggerAgent(),
        "test_writer":   TestWriterAgent(),
    }


_agents = None


def _get_agent(name: str):
    global _agents
    if _agents is None:
        _agents = _get_sub_agents()
    return _agents.get(name)


async def dispatch(tasks: list[TaskSpec]) -> dict[str, str]:
    """
    按拓扑顺序执行所有任务，返回 {task_id: 结果文字} 的映射。

    算法（拓扑排序 + 波次并行）：
    1. 找出所有没有依赖的任务（入度为 0）→ 波次 1
    2. 并行执行当前波次
    3. 更新依赖计数，找出新解锁的任务 → 波次 2
    4. 重复直到所有任务完成
    """
    if not tasks:
        return {}

    # 建立索引
    spec_by_id = {t.id: t for t in tasks}
    results: dict[str, str] = {}
    errors: dict[str, str] = {}

    # 依赖计数（入度）
    in_degree = {t.id: len(t.depends_on) for t in tasks}

    # 反向依赖：dependents["t1"] = ["t2", "t3"] 表示 t2、t3 依赖 t1
    dependents: dict[str, list[str]] = defaultdict(list)
    for t in tasks:
        for dep in t.depends_on:
            dependents[dep].append(t.id)

    # 初始就绪队列（入度为 0 的任务）
    ready = deque(t.id for t in tasks if not t.depends_on)

    while ready:
        # 取出当前所有就绪任务（这一批并行执行）
        wave = list(ready)
        ready.clear()

        # 分类：可执行 vs 被阻断（前置任务失败）
        runnable = []
        for tid in wave:
            spec = spec_by_id[tid]
            failed_dep = next(
                (dep for dep in spec.depends_on if dep in errors),
                None,
            )
            if failed_dep:
                errors[tid] = f"前置任务 '{failed_dep}' 失败，跳过本任务"
                print(f"[Dispatcher] 跳过 {tid}：{errors[tid]}")
            else:
                runnable.append(spec)

        # 并行执行这一波次的所有任务
        if runnable:
            coros = [_run_one(spec, results) for spec in runnable]
            done = await asyncio.gather(*coros, return_exceptions=True)

            for spec, outcome in zip(runnable, done):
                if isinstance(outcome, Exception):
                    errors[spec.id] = str(outcome)
                    print(f"[Dispatcher] 任务 {spec.id} 失败：{outcome}")
                else:
                    results[spec.id] = outcome

        # 更新依赖计数，解锁下一波次
        for tid in wave:
            for child_id in dependents[tid]:
                if child_id in results or child_id in errors:
                    continue
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    ready.append(child_id)

    return results


async def _run_one(spec: TaskSpec, prior_results: dict[str, str]) -> str:
    """执行单个任务，把前置任务的结果注入 context。"""
    agent = _get_agent(spec.agent)
    if agent is None:
        raise ValueError(f"未知子 Agent：'{spec.agent}'。已注册：{list(_get_sub_agents().keys())}")

    # 把前置任务的结果作为 context 传入
    context = {dep: prior_results[dep] for dep in spec.depends_on if dep in prior_results}

    print(f"[Dispatcher] → {spec.agent} | {spec.input[:60]}")
    result = await agent.run(task=spec.input, context=context or None)
    print(f"[Dispatcher] ← {spec.agent} | 完成（{len(result)} 字符）")
    return result
```

---

## 6.8 Coordinator 主体 `coordinator/agent.py`

```python
# coordinator/agent.py
from .planner import make_plan
from .dispatcher import dispatch


class CoordinatorAgent:
    """主协调 Agent：接收用户请求，规划任务，分发执行，聚合结果。"""

    async def run(self, user_request: str) -> str:
        print(f"[Coordinator] 收到请求：{user_request[:60]}")

        # 第一步：规划任务
        tasks, direct_reply = await make_plan(user_request)

        # 如果不需要子 Agent，直接返回 LLM 的回复
        if direct_reply is not None:
            return direct_reply

        if not tasks:
            return "无法理解请求，请提供更多信息。"

        print(f"[Coordinator] 规划了 {len(tasks)} 个子任务")

        # 第二步：分发执行
        results = await dispatch(tasks)

        # 第三步：聚合结果
        return _aggregate(tasks, results)


def _aggregate(tasks, results: dict[str, str]) -> str:
    """把多个子任务结果拼合成完整回复。"""
    if len(results) == 1:
        # 只有一个任务，直接返回
        return next(iter(results.values()))

    parts = []
    for task in tasks:
        if task.id in results:
            parts.append(f"**[{task.agent}]**\n{results[task.id]}")

    return "\n\n---\n\n".join(parts)
```

---

## 6.9 更新 `main.py`

```python
# main.py 里的 ask_endpoint 改为使用 CoordinatorAgent
from coordinator.agent import CoordinatorAgent

_coordinator = CoordinatorAgent()

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    start = time.time()
    try:
        result = await _coordinator.run(req.question)
        return AskResponse(text=result, usage={})
    except Exception as e:
        return AskResponse(text="", usage={}, error=str(e))
```

---

## 6.10 测试 Coordinator

```bash
# 启动服务
python main.py

# 场景 1：单任务——代码审查
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "帮我审查以下代码是否有安全问题：\ndef login(username, password):\n    sql = f\"SELECT * FROM users WHERE name=\'{username}\'\"\n    return db.execute(sql)"}'

# 场景 2：串行任务——审查后修复（应看到 t1 完成后才启动 t2）
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "帮我审查 auth.py 的 login 函数，找到 Bug 后修复它，最后写单元测试"}'

# 场景 3：并行任务——同时审查两个独立文件（观察日志时间戳，两个任务几乎同时开始）
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "分别审查 auth.py 和 payment.py 这两个文件的代码质量"}'
```

> 📌 **进阶备注**（不影响本章已实现内容）：Coordinator 的 DAG 是一次性规划的，若 Planner
> 规划有误（漏依赖、粒度错），现状只能等所有波次跑完才发现、无法中途纠正。2026 行业趋势
> 评估把这列为可改进点，训练无关的**局部重新规划**容错方案见 **第 18 章 18.1 节**。

---

## 6.11 本章检查清单

```
□ CoordinatorAgent 能正确调用 LLM 并输出 JSON 任务计划
  验证：打开日志，查看 [Planner] 和 [Dispatcher] 的输出

□ 单任务正常执行并返回结果

□ 多任务并行执行（观察日志时间戳，无依赖的任务几乎同时开始）

□ 串行任务（depends_on 非空）按顺序执行，后续任务能拿到前置任务的结果

□ 未知 agent 名称时有清晰的错误提示（不崩溃）
```

---

# 第 7 章：阶段 7 —— Swarm 模式（持久化团队 + 任务白板）

> **本章目标**：实现 Agent 团队的长期持久化协作——Agent 不再是无状态的"一次性"调用，
> 而是有自己的角色和记忆，通过共享"任务白板"（Blackboard）协调工作。

---

## 7.1 Coordinator 模式 vs Swarm 模式

| 对比维度 | Coordinator 模式（第 6 章） | Swarm 模式（本章） |
|---------|--------------------------|-----------------|
| Agent 生命周期 | 每次请求创建，用完即弃 | 持久化运行，保持状态 |
| 通信方式 | Coordinator 直接调用子 Agent | 通过共享白板（发布/认领任务） |
| 适用场景 | 明确的任务拆分，边界清晰 | 自组织协作，涌现式行为 |
| 一致性保证 | 强（Coordinator 是唯一入口） | 弱（Agent 自主决策） |

**什么时候用 Swarm 模式？**

- Agent 需要处理**持续流入的任务流**（而不是一次性请求）
- 不同 Agent 需要根据自身负载**动态认领任务**
- 任务之间的依赖关系**事先不确定**（运行时才知道）

### 7.1.1 接入方式：两个入口，而不是一个开关

Coordinator 和 Swarm 不是同一个问题的两种实现方式，不存在"哪个更好、切换过去"的关系——
它们对应的调用模式本身就不同（一次性问答 vs 持续任务流），所以**两个模式都保留**，
但通过**不同的 HTTP 入口**区分，而不是加一个全局配置项让用户"切换模式"：

- **`POST /ask`**：走 Coordinator（第 6 章）。用户问一句话，规划 → 分发 → 聚合 → 返回，
  一次请求一次结果，边界清晰、好调试，适合当前的对话场景。
- **`POST /swarm/ask`**（本章新增，见 7.5 节）：走 Blackboard（本章）。调用方式跟
  `/ask` 保持一致——一次调用、等结果返回，但内部实现完全不同：任务先发布到白板，
  由常驻的 Swarm Agent 异步认领执行，接口只是在背后帮你等结果。这样调用方不需要
  关心"这次请求走的是哪种编排方式"，两个入口用起来的手感是一样的，区别只在于
  背后的架构（直接调用 vs 白板认领），未来要扩展成"持续跑的任务队列"时也可以在
  `/swarm/ask` 之外加只读的状态查询接口，不影响现有调用方式。
- 两个入口都挂在同一个 `main.py` 里，`uvicorn main:app` 启动一次，两种设计模式的
  Agent 就都能访问到，不需要像之前草案那样再单独跑一个 `swarm/main.py` 脚本。

为什么不做成一个全局开关（比如 `.env` 里配 `AGENT_MODE=coordinator|swarm`，所有请求走同一个
`/ask` 再内部分流）：

1. **背后的执行模型完全不同**——Coordinator 是"主 Agent 直接调用子 Agent"，Swarm 是
   "任务发布到白板、被动认领"，硬塞进同一个函数内部用 if/else 分流，只是把复杂度从
   "两个接口"转移成了"一个接口内部两套分支逻辑"，可读性更差，两套逻辑还是要分别维护。
2. **调用方通常一开始就知道自己要什么**——想要"主 Agent 帮我拆解任务"用 `/ask`，
   想要"扔进一个可能会自动派生后续任务（比如审查发现问题自动转 debug）的队列"用
   `/swarm/ask`，这是调用前就能确定的选择，不需要运行时开关。
3. **两个入口各自独立演化**——`/ask` 改 Coordinator 内部实现、`/swarm/ask` 改
   Swarm Agent 的种类和数量，互不牵连；全局开关会让两套实现被迫耦合在同一个
   请求处理路径上。

---

## 7.2 任务白板 `swarm/blackboard.py`

```python
# swarm/blackboard.py
"""
任务白板（Blackboard）：Swarm 中所有 Agent 共享的任务池。

黑板模式（Blackboard Pattern）：
  - 发布者（任何 Agent 或用户）往白板上写任务
  - 消费者（专家 Agent）从白板上认领自己能做的任务
  - 白板保证：同一个任务不会被两个 Agent 重复认领（加锁保证原子性）
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    """白板上的一个任务。"""
    id: str
    type: str                # 任务类型（Agent 根据类型决定要不要认领）
    payload: Any             # 任务内容（自由格式）
    status: str = "pending"  # pending | claimed | done | failed
    owner: str | None = None # 认领这个任务的 Agent ID
    result: Any = None       # 任务完成后的结果
    error: str | None = None # 任务失败时的错误信息


class Blackboard:
    """
    线程安全的任务白板。

    核心保证：claim() 操作是原子的——即使多个 Agent 同时调用，
    每个任务只会被一个 Agent 认领。
    """

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._new_task_cond = asyncio.Condition()  # 有新任务时 notify_all，唤醒等待的 Agent
        self._known_types: set[str] = set()        # 已注册消费者的任务类型，post() 时校验用

    def register_consumer(self, task_types: list[str]):
        """
        登记"有 Agent 能处理这些任务类型"。

        由 SwarmAgent.__init__ 在创建实例时自动调用（见 7.3 节的 agent_base.py），
        不需要手动调用。post() 发布任务时会检查类型是否在这个集合里，
        避免出现"发布了但没有任何 Agent 会认领"的任务永远卡在 pending。
        """
        self._known_types.update(task_types)

    async def post(self, task_type: str, payload: Any) -> str:
        """
        发布一个新任务到白板。返回任务 ID。

        任何 Agent 或外部代码都可以调用这个方法发布任务。

        如果 task_type 不在任何已注册 Agent 的 task_types 里，说明发布了
        一个没人处理的任务类型（比如手写字符串时的笔误），直接抛异常，
        而不是让任务静默地永远停在 pending。
        """
        if self._known_types and task_type not in self._known_types:
            raise ValueError(
                f"未知任务类型：'{task_type}'。已注册消费者的类型：{sorted(self._known_types)}"
            )

        task_id = str(uuid.uuid4())[:8]   # 短 ID，方便日志查看
        task = Task(id=task_id, type=task_type, payload=payload)

        async with self._lock:
            self._tasks[task_id] = task

        # 通知所有等待中的 Agent 有新任务了
        async with self._new_task_cond:
            self._new_task_cond.notify_all()

        print(f"[Blackboard] 新任务 {task_id}（类型：{task_type}）")
        return task_id

    async def claim(self, task_type: str, agent_id: str) -> Task | None:
        """
        尝试认领一个指定类型的 pending 任务。

        这是白板最关键的方法——必须保证原子性（用锁）：
        从"读取 pending 任务"到"标记为 claimed"这两个操作不可被打断，
        否则两个 Agent 可能同时认领同一个任务。

        返回认领的任务，如果没有匹配的 pending 任务则返回 None。
        """
        async with self._lock:
            for task in self._tasks.values():
                if task.type == task_type and task.status == "pending":
                    task.status = "claimed"
                    task.owner = agent_id
                    return task
        return None

    async def complete(self, task_id: str, result: Any):
        """标记任务为已完成并保存结果。"""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "done"
                self._tasks[task_id].result = result
                print(f"[Blackboard] 任务 {task_id} 完成")

    async def fail(self, task_id: str, error: str):
        """标记任务为失败并记录错误。"""
        async with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id].status = "failed"
                self._tasks[task_id].error = error
                print(f"[Blackboard] 任务 {task_id} 失败：{error}")

    async def wait_for_task(self, timeout: float = 5.0):
        """等待新任务到来（供 Agent 的轮询循环使用）。"""
        try:
            async with self._new_task_cond:
                await asyncio.wait_for(self._new_task_cond.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def get_all(self) -> list[Task]:
        return list(self._tasks.values())

    def summary(self) -> dict:
        """返回白板状态摘要（用于监控）。"""
        from collections import Counter
        counts = Counter(t.status for t in self._tasks.values())
        return dict(counts)
```

**`_known_types` / `register_consumer()` 是干什么的？零基础也要懂这一段的动机**：

刚开始写 Swarm 的时候，`post()` 是不做任何检查的——只要有人调用
`await blackboard.post("随便什么字符串", 数据)`，任务就会被塞进 `self._tasks` 字典，
状态是 `pending`，然后**永远停在这里**，除非正好有个 Agent 声明了同名的 `task_types`
去认领它。真实踩过的坑：`DebuggerSwarmAgent`（7.4b 节）修复完代码后会自动发布一个
`task_type="test_write"` 的任务，但当时 `swarm/` 目录下没有任何 Agent 声明处理
`"test_write"`——任务发出去了，日志也打印了"已发布"，但**没有任何异常、没有任何提示**，
只有翻白板数据才能发现这个任务一直卡在 `pending`。

`_known_types` 就是为了在"发布时"就能发现这类问题：它是一个集合，记录"当前有哪些
任务类型是有 Agent 会处理的"。`register_consumer(task_types)` 负责往这个集合里添加，
调用时机是**每个 SwarmAgent 实例被创建的时候**（见 7.3 节 `SwarmAgent.__init__`
里的那一行调用，不需要手动维护一份额外的注册表）。这样 `post()` 里就能加一句
「如果 `task_type` 不在 `_known_types` 里，直接抛异常」——把"发布了没人处理的任务"
从"运行时悄悄卡死"变成"发布那一刻就报错"，7.5.3 节会看到 `main.py` 怎么接住这个异常
返回给调用方，而不是让服务直接崩掉。

顺带解释一下 `if self._known_types and task_type not in self._known_types` 里为什么
要加 `self._known_types and` 这个前半句：如果一个 Agent 都还没创建（`_known_types`
是空集合），这个检查会直接跳过，不会误报"没人处理"——毕竟这时候确实还没轮到检查的
时候（比如测试代码里可能想在没启动任何 Agent 的情况下单独测 `post()` 本身）。

---

## 7.3 Swarm Agent 基类 `swarm/agent_base.py`

```python
# swarm/agent_base.py
"""
Swarm Agent 基类。

和 SubAgent 的区别：
  - SubAgent：无状态，每次调用是独立的
  - SwarmAgent：持久化运行，有自己的 agent_id，持续监听白板
"""
import asyncio
from abc import ABC, abstractmethod
from .blackboard import Blackboard, Task


class SwarmAgent(ABC):

    def __init__(self, blackboard: Blackboard, agent_id: str | None = None):
        self.blackboard = blackboard
        # 允许子类传固定 agent_id（方便看日志）；不传则自动生成一个，
        # 保证同一类型开多个实例做负载均衡时 ID 不会撞在一起
        self.agent_id = agent_id or f"{type(self).__name__}-{id(self) % 10000}"
        self._running = False
        # 把自己声明的 task_types 登记到白板，供 post() 校验任务类型是否有人处理
        self.blackboard.register_consumer(self.task_types)

    @property
    @abstractmethod
    def task_types(self) -> list[str]:
        """这个 Agent 处理哪些类型的任务。"""
        ...

    @abstractmethod
    async def handle(self, task: Task) -> str:
        """
        处理一个任务，返回结果字符串。

        任务处理失败时抛出异常（SwarmAgent 会自动标记为 failed）。
        """
        ...

    async def start(self):
        """启动 Agent，持续监听白板上的任务。"""
        self._running = True
        print(f"[{self.agent_id}] 已启动，监听任务类型：{self.task_types}")

        while self._running:
            claimed_any = False

            for task_type in self.task_types:
                task = await self.blackboard.claim(task_type, self.agent_id)
                if task:
                    claimed_any = True
                    try:
                        result = await self.handle(task)
                        await self.blackboard.complete(task.id, result)
                    except Exception as e:
                        await self.blackboard.fail(task.id, str(e))

            if not claimed_any:
                # 没有任务，等待新任务到来（最多等 5 秒，然后再轮询）
                await self.blackboard.wait_for_task(timeout=5.0)

    def stop(self):
        """停止 Agent。"""
        self._running = False
        print(f"[{self.agent_id}] 已停止")
```

> **对照实际代码看**：这个基类定义的抽象成员是 `task_types`（这个 Agent 能处理哪些
> 任务类型）和 `handle()`（怎么处理一个任务），7.4/7.4b 节的 `ReviewerSwarmAgent` /
> `DebuggerSwarmAgent` 也是按这两个名字实现的——如果你本地的 `swarm/agent_base.py`
> 还是旧版本（`name`/`handles`/`process`），子类会因为方法名不匹配报
> `TypeError: Can't instantiate abstract class ... with abstract method`，
> 记得同步改成上面这版。另外注意 `__init__` 最后新增的
> `self.blackboard.register_consumer(self.task_types)` 这一行——它是 7.2 节
> `_known_types` 校验能生效的前提，如果子类没有调用 `super().__init__(...)`
> （比如自己重写了一个 `__init__` 却忘了调用父类的），这一步就不会执行，
> 这个 Agent 声明的 `task_types` 也就不会被白板知道，会出现"明明写了 `task_types`
> 但 `post()` 还是报未知类型"的诡异现象——排查时先检查这一点。

---

## 7.3b 任务类型的唯一定义来源 `swarm/task_types.py`

7.2 节的 `_known_types` 解决了"发布了没人处理的任务会静默卡死"这一个问题，但还留了
一个隐患：**任务类型本身还是裸字符串**。`main.py` 的请求体、`Blackboard.Task.type`、
每个 `SwarmAgent.task_types` 里都是各自手写 `"code_review"` / `"debug"` /
`"test_write"` 这样的字符串，没有任何地方规定"合法的类型只有这三种"——如果某个
Agent 手写时打错一个字（比如写成 `"test_write "`，多了个空格，或者 `"testwrite"`
少了个下划线），Python 不会报任何语法错误，只会在运行时表现成"这个任务又没人处理了"，
排查起来跟 7.2 节说的坑几乎一样麻烦，只是触发原因从"忘了写 Agent"变成了"写错了字符串"。

解决思路很直接：把"到底有哪些合法的任务类型"这件事**只写在一个地方**，其他所有
用到任务类型的代码都从这一个地方导入，而不是各自重复敲字符串——这样输入错误会
在 `import` 或者 `Literal` 类型校验阶段就被发现，而不是等到运行时才发现任务卡住了：

```python
# swarm/task_types.py
"""
Swarm 模式下所有合法任务类型的唯一定义来源。

之前的问题：task_type 在 API 层（SwarmAskRequest）、白板（Task.type）、
各 SwarmAgent 的 task_types 声明里都是裸字符串，三处互不校验——
DebuggerSwarmAgent 发布了一个 "test_write" 任务，但没有任何 Agent
声明处理这个类型，任务永远卡在 pending，也不会报错。

这里把"合法范围"集中定义一次，API 校验和 Blackboard 运行时校验都引用
同一份定义，新增任务类型时只需要改这一个文件。
"""
from typing import Literal

TASK_TYPE_CODE_REVIEW = "code_review"
TASK_TYPE_DEBUG = "debug"
TASK_TYPE_TEST_WRITE = "test_write"

# 供 Pydantic 请求体做值域校验：传入范围外的字符串会在 API 层直接 422，
# 不会等到进了白板才发现没人处理。
TaskType = Literal["code_review", "debug", "test_write"]

ALL_TASK_TYPES: tuple[str, ...] = (
    TASK_TYPE_CODE_REVIEW,
    TASK_TYPE_DEBUG,
    TASK_TYPE_TEST_WRITE,
)
```

逐段解释，专门讲给没接触过 `Literal` 的读者：

- **为什么不直接用普通字符串常量就够了**（比如只写 `TASK_TYPE_DEBUG = "debug"`）：
  常量能防止*代码里*手写打错字（写 `TASK_TYPE_DEBUG` 打错了 Python 会直接报
  `NameError`，比字符串打错字要显眼得多），但常量本身不能拦住"外部调用方通过
  HTTP 请求传了一个乱七八糟的字符串"这种情况——那是 7.5.1 节 `TaskType` 要解决的
  另一层问题。
- **`Literal["code_review", "debug", "test_write"]` 是什么**：这是 Python
  `typing` 模块提供的一种类型标注，意思是"这个变量的取值只能是这几个字符串中的
  一个，不能是任意 `str`"。单独写在业务代码里它不会自动帮你做检查（Python 本身
  不强制类型标注），但 **Pydantic**（`main.py` 用来定义请求体的库）认识
  `Literal`，会在校验请求体的时候真正拿它当"合法值枚举"来用——这就是 7.5.1 节
  `SwarmAskRequest.task_type: TaskType` 能在 API 层直接返回 422 的原理，本节先
  只关注这个类型是在哪定义的。
- **为什么 `TASK_TYPE_XXX` 常量和 `TaskType`（`Literal`）要同时留着，不是重复**：
  前者是给*写代码的人*用的（`from .task_types import TASK_TYPE_DEBUG`，IDE 能自动
  补全、拼错会报错），后者是给*Pydantic 做请求体校验*用的（HTTP 请求体本质是
  JSON 字符串，Pydantic 需要一个"合法字符串范围"的类型标注才能在校验阶段拦截，
  不能拿 Python 常量做这件事）——两者服务的是同一份"合法范围"，但用在不同层，
  所以都保留，且都从这一个文件派生，保证不会出现"改了一处、另一处忘了改"的情况。
- **`ALL_TASK_TYPES` 目前谁在用**：本次改动里暂时没有代码直接用到这个元组（比如
  遍历打印所有合法类型），先留着是因为它比 `Literal["code_review", ...]` 更容易
  在运行时被代码遍历使用（`Literal` 的可选值要用
  `typing.get_args(TaskType)` 才能在运行时取出来，不如直接遍历一个元组直观）——
  如果之后要写一个"打印当前支持哪些任务类型"的接口或日志，直接用这个元组即可，
  不需要再额外定义一份。

新增任务类型时的操作步骤（新手可以照抄这个顺序）：

1. 在 `swarm/task_types.py` 里加一行 `TASK_TYPE_XXX = "xxx"`，同时把 `"xxx"`
   加进 `TaskType` 的 `Literal[...]` 列表和 `ALL_TASK_TYPES` 元组。
2. 写一个新的 `SwarmAgent` 子类，`task_types` 属性里返回 `[TASK_TYPE_XXX]`
   （参考 7.4/7.4b/7.4c 节的写法）。
3. 在 `main.py` 的 `lifespan` 里把这个新 Agent 加进 `agents` 列表（7.5.2 节）。

漏了第 2、3 步中的任何一步，7.2 节的 `_known_types` 校验都会在 `post()` 时立刻
报错提示"未知任务类型"，不会再出现本章开头说的那种"静默卡死"。

---

## 7.4 示例 Swarm Agent：代码审查 `swarm/reviewer_agent.py`

在 Swarm 模式里，CodeReviewer 持续轮询白板，认领 `code_review` 类型的任务并执行。

```python
# swarm/reviewer_agent.py
"""
Swarm 模式下的代码审查 Agent。

持续运行，从 Blackboard 认领 code_review 任务，
执行完后把结果写回 Blackboard，并可自动发布后续任务（如需要修复则发 debug 任务）。
"""
import asyncio
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_CODE_REVIEW, TASK_TYPE_DEBUG


class ReviewerSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "reviewer-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        """声明这个 Agent 能处理哪些类型的任务。"""
        return [TASK_TYPE_CODE_REVIEW]

    async def handle(self, task) -> str:
        """
        处理一个 code_review 任务。

        task.payload 格式：
        {
            "code": "要审查的代码字符串",
            "file": "文件名（可选，仅用于显示）",
            "focus": "重点关注的方面（可选，如 'security'）"
        }
        """
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "unknown.py")
        focus = payload.get("focus", "全面审查")

        print(f"[{self.agent_id}] 开始审查：{filename}")

        # 调用 LLM 执行审查
        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        system = """
你是一名资深代码审查工程师。
审查维度：SQL 注入、命令注入、硬编码密码、逻辑错误、边界条件、性能问题。
每个问题输出：[Critical/Warning/Suggestion] 行号 - 问题描述 - 建议修复。
发现 Critical 级别问题时，最后一行输出 NEEDS_FIX:true，否则输出 NEEDS_FIX:false。
"""
        prompt = f"文件：{filename}\n重点关注：{focus}\n\n```python\n{code}\n```"

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        # 如果发现严重问题，自动发布 debug 任务（这就是 Swarm 的涌现式行为）
        if "NEEDS_FIX:true" in result:
            debug_task_id = await self.blackboard.post(
                task_type=TASK_TYPE_DEBUG,
                payload={
                    "code": code,
                    "file": filename,
                    "review_result": result,
                    "origin_task": task.id,
                }
            )
            print(f"[{self.agent_id}] 发现 Critical 问题，已发布 debug 任务 {debug_task_id}")

        return result
```

跟旧版本比，唯一的区别是把 `task_type="debug"` 这种手写字符串换成了
`task_type=TASK_TYPE_DEBUG`（从 7.3b 节的 `swarm/task_types.py` 导入）——
逻辑完全没变，纯粹是把"合法字符串"这件事交给一个地方统一管理，如果以后
`TASK_TYPE_DEBUG` 对应的字符串值需要改，只需要改 `task_types.py` 这一处，
不需要满项目搜索所有硬编码的 `"debug"`。

---

## 7.4b 调试 Swarm Agent `swarm/debugger_agent.py`

```python
# swarm/debugger_agent.py
"""
Swarm 模式下的调试修复 Agent。
认领 ReviewerAgent 自动发布的 debug 任务。
"""
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_DEBUG, TASK_TYPE_TEST_WRITE


class DebuggerSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "debugger-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        return [TASK_TYPE_DEBUG]

    async def handle(self, task) -> str:
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "")
        review_result = payload.get("review_result", "")

        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        system = """
你是一名调试工程师，根据代码审查结果修复 Bug。
输出格式：
1. Bug 根因（一句话）
2. 修复后的完整代码（```python 代码块）
3. 修复说明（改了什么）
"""
        prompt = (
            f"文件：{filename}\n\n"
            f"审查结果：\n{review_result}\n\n"
            f"原始代码：\n```python\n{code}\n```\n\n"
            "请根据审查结果修复所有 Critical 级别的问题。"
        )

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        # 修复完成后，自动发布 test_write 任务
        await self.blackboard.post(
            task_type=TASK_TYPE_TEST_WRITE,
            payload={
                "code": result,
                "file": filename,
                "origin_task": task.id,
            }
        )
        print(f"[{self.agent_id}] 修复完成，已发布 test_write 任务")
        return result
```

跟 7.4 节一样，这里也只是把裸字符串换成 `task_types.py` 里的常量，`task_type=TASK_TYPE_TEST_WRITE`
对应的字符串值还是 `"test_write"`，行为完全不变。

> **这一行曾经是本章最大的一个坑**：`DebuggerSwarmAgent` 会自动发布一个
> `test_write` 任务，但如果 `swarm/` 目录下**没有任何 Agent 声明 `task_types`
> 包含这个类型**、`main.py` 的 `lifespan`（7.5.2 节）里也没有实例化对应的 Agent，
> 这个任务会被正常发布（日志会打印"已发布 test_write 任务"），但**永远没有人认领**，
> 状态永远停在 `pending`——不报错、不提示，只能靠人工翻白板数据才能发现。7.4c 节
> 的 `TestWriterSwarmAgent` 就是为了补上这个缺口；配合 7.2 节 `_known_types` 的
> 校验，现在如果再出现"发布了没人处理的类型"，`post()` 会直接抛异常，不会再是
> 静默卡死。

---

## 7.4c 测试生成 Swarm Agent `swarm/test_writer_agent.py`

`review → debug` 这条链路本来在 `DebuggerSwarmAgent` 修复完代码后就结束了，但
`debugger_agent.py` 里其实已经顺手发布了一个 `test_write` 任务（7.4b 节代码块的
最后几行）——这是"提前设计好、但当时没有 Agent 去接"的一个任务类型。
`TestWriterSwarmAgent` 就是补上的这个消费者，认领 `test_write` 任务，调用 LLM
为修复后的代码生成 pytest 单元测试，让 `code_review → debug → test_write`
这条链路第一次真正跑完整：

````python
# swarm/test_writer_agent.py
"""
Swarm 模式下的测试生成 Agent。
认领 DebuggerSwarmAgent 自动发布的 test_write 任务，链路收尾：
review → debug → test_write。
"""
from .blackboard import Blackboard
from .agent_base import SwarmAgent
from .task_types import TASK_TYPE_TEST_WRITE


class TestWriterSwarmAgent(SwarmAgent):

    def __init__(self, blackboard: Blackboard, agent_id: str = "test-writer-1"):
        super().__init__(blackboard, agent_id)

    @property
    def task_types(self) -> list[str]:
        return [TASK_TYPE_TEST_WRITE]

    async def handle(self, task) -> str:
        """
        处理一个 test_write 任务。

        task.payload 格式（由 DebuggerSwarmAgent 发布，见 debugger_agent.py）：
        {
            "code": "修复后的代码（DebuggerSwarmAgent 的完整输出，含说明文字）",
            "file": "文件名",
            "origin_task": "最初 code_review 任务的 id（仅用于追溯，不参与生成逻辑）"
        }
        """
        payload = task.payload
        code = payload.get("code", "")
        filename = payload.get("file", "unknown.py")

        print(f"[{self.agent_id}] 开始为 {filename} 生成测试")

        from providers.router import get_provider
        from providers.types import Message, TextBlock

        provider = get_provider()

        system = """
你是一名测试工程师，专注于编写高质量的 pytest 单元测试。

测试覆盖原则：
1. 正常路径（happy path）
2. 边界条件：空值、最大值、最小值、空列表等
3. 异常路径：非法参数、外部依赖失败时的行为
4. 安全场景（如涉及用户输入）：SQL 注入、XSS 尝试

输出格式：
```python
# test_xxx.py
import pytest
# ... 完整测试代码
```
测试覆盖说明：列出覆盖了哪些场景
"""
        prompt = (
            f"文件：{filename}\n\n"
            f"以下是修复后的代码（可能夹杂修复说明文字，重点关注其中的代码块）：\n"
            f"{code}\n\n"
            "请为这段代码编写 pytest 单元测试。"
        )

        response = await provider.chat(
            messages=[Message(role="user", content=[TextBlock(text=prompt)])],
            system=system,
        )

        result = ""
        for block in response.content:
            if hasattr(block, "text"):
                result += block.text

        print(f"[{self.agent_id}] 测试生成完成")
        return result
````

这个类的结构跟 7.4/7.4b 节的 `ReviewerSwarmAgent`/`DebuggerSwarmAgent` 完全是
同一个模板，逐点对照着看：

- **`task_types` 只声明了一个类型** `[TASK_TYPE_TEST_WRITE]`——跟前两个 Agent
  一样，一个 Agent 可以声明处理多种类型（`task_types` 返回的是列表），这里只是
  当前只需要处理这一种。
- **`handle()` 里没有再发布新任务**——`review → debug → test_write` 这条链到
  这一步就结束了，测试生成完之后直接 `return result`，不会再触发下一个自动派生
  的任务。如果以后想让链路更长（比如生成测试后自动跑一遍测试、根据结果再触发
  一轮修复），可以参考 7.4/7.4b 节 `handle()` 结尾调用 `self.blackboard.post(...)`
  的写法，在这里也加一段。
- **依然没有工具调用（Tool Calling）**——跟 `swarm/` 目录下其它 Agent 一样，
  这里是直接把 `payload["code"]` 拼进 prompt 交给 LLM，全程没有 `open()` 读文件、
  没有执行代码。如果想对照"能调用工具的测试生成 Agent"是什么样的，可以看
  `sub_agents/test_writer.py`（Coordinator 模式下的实现，用了 `ReadFileTool`/
  `RunPythonTool`）——两者测试覆盖的设计原则（正常路径/边界条件/异常路径/安全场景）
  是一致的，只是 Swarm 版本更简单，不依赖工具执行。
- **`payload.get("code", "")` 里的 `code` 到底是什么**：注意看 7.4b 节
  `DebuggerSwarmAgent.handle()` 最后发布 `test_write` 任务时传的
  `"code": result`——这个 `result` 是 LLM 修复代码后的**完整输出**，可能夹杂
  "Bug 根因"、"修复说明"这些说明文字，不是纯粹的代码。所以这里 prompt 里特意写了
  "可能夹杂修复说明文字，重点关注其中的代码块"，提醒 LLM 自己从中提取需要测试的
  代码部分，而不是假设这个字段一定是干净的源代码。

记得在 7.5.2 节的 `lifespan` 里把这个新 Agent 加进 `agents` 列表——只是写好这个类
本身不会自动生效，`SwarmAgent` 必须被实例化、调用 `start()` 才会真正开始轮询白板。

---

## 7.5 接入 `main.py`：`POST /swarm/ask`

不再单独写一个 `swarm/main.py` 脚本去跑 Swarm——那样的话 Swarm 和 Coordinator 是两个
互不相关的进程，跟 7.1.1 节"两个入口、一次启动"的设计不符。这里直接把白板和常驻的
Swarm Agent 接进 `main.py` 现有的 `lifespan`，跟 Coordinator 用同一个 FastAPI 进程、
同一次 `uvicorn main:app` 启动。

### 7.5.1 请求/响应模型

```python
# main.py 新增：Swarm 相关的请求/响应模型
from typing import Any
from swarm.task_types import TaskType


class SwarmAskRequest(BaseModel):
    """POST /swarm/ask 的请求体格式"""
    task_type: TaskType = Field(..., description="任务类型，范围见 swarm/task_types.py", examples=["code_review"])
    payload: dict = Field(..., description="任务内容，字段随 task_type 变化，如 {'code': '...', 'file': 'a.py'}")
    timeout: float = Field(default=30.0, ge=1.0, le=120.0, description="最长等待秒数，超时仍未完成则返回当前状态（一般是 pending）")


class SwarmAskResponse(BaseModel):
    """POST /swarm/ask 的响应体格式"""
    task_id: str
    status: str                 # pending | claimed | done | failed
    result: Any = None
    error: str | None = None
```

`task_type` 的类型从裸 `str` 换成了 `TaskType`（7.3b 节 `swarm/task_types.py` 里定义的
`Literal["code_review", "debug", "test_write"]`）。这一个改动的效果是：请求体里
`task_type` 传入范围外的字符串时，**FastAPI/Pydantic 会在进入 `swarm_ask_endpoint`
函数体之前就直接拒绝请求**，返回 `422 Unprocessable Entity`，错误信息里会带出合法
取值范围，比如：

```json
{"detail":[{"type":"literal_error","loc":["body","task_type"],
  "msg":"Input should be 'code_review', 'debug' or 'test_write'", "...": "..."}]}
```

注意这跟 7.3b 节 `Blackboard.post()` 里的校验不是同一层——这里的 422 是"请求格式
本身不合法"，请求还没进到业务代码；`post()` 里的 `ValueError` 校验的是"字符串格式
合法，但没有 Agent 声明处理这个类型"，这属于服务内部的配置遗漏，两层校验分别堵住
不同来源的问题，7.5.3 节会看到 `post()` 抛出的 `ValueError` 具体是怎么被接住的。

### 7.5.2 lifespan 里常驻启动 Swarm Agent

```python
# main.py 顶部新增 import（跟原有的 fastapi/pydantic import 放一起）
import asyncio
from swarm.blackboard import Blackboard
from swarm.reviewer_agent import ReviewerSwarmAgent
from swarm.debugger_agent import DebuggerSwarmAgent
from swarm.test_writer_agent import TestWriterSwarmAgent


# 全局白板 + Swarm Agent 后台任务（跟 _coordinator 一样，启动时初始化一次）
_blackboard = Blackboard()
_swarm_agent_tasks: list[asyncio.Task] = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("服务启动中...")
    print(f"API 文档地址：http://localhost:8002/docs")
    print(f"流式测试：curl -N 'http://localhost:8002/ask/stream?question=你好'")

    # 常驻 Swarm Agent：跟 FastAPI 服务同生命周期，不是每次请求创建/销毁
    agents = [
        ReviewerSwarmAgent(_blackboard),
        DebuggerSwarmAgent(_blackboard),
        TestWriterSwarmAgent(_blackboard),
    ]
    _swarm_agent_tasks.extend(asyncio.create_task(a.start()) for a in agents)

    yield

    for task in _swarm_agent_tasks:
        task.cancel()
    await asyncio.gather(*_swarm_agent_tasks, return_exceptions=True)
    print("服务已关闭。")
```

`agents` 列表比之前多了一个 `TestWriterSwarmAgent(_blackboard)`——对应 7.4c 节新增的
Agent。这里有个容易被忽略但很关键的顺序问题：`ReviewerSwarmAgent`/`DebuggerSwarmAgent`/
`TestWriterSwarmAgent` 三个实例化语句执行时，各自的 `__init__`（7.3 节）都会同步调用
`self.blackboard.register_consumer(self.task_types)`，也就是说**这三行代码执行完，
白板的 `_known_types` 就已经包含了全部三种任务类型**，跟这三个实例是否已经
`start()` 起来轮询无关（`_swarm_agent_tasks.extend(...)` 那一行才是真正开始轮询）。
如果这里漏写了 `TestWriterSwarmAgent(_blackboard)` 这一行（就是 7.4b 节提到的
"曾经的坑"当时的状态），`_known_types` 里就不会有 `"test_write"`，`DebuggerSwarmAgent`
发布 `test_write` 任务时 7.3b 节的校验就会直接抛 `ValueError`——这正是本次修复
补上的那一行。

### 7.5.3 `POST /swarm/ask` 接口

调用方式跟 `/ask` 保持一致——一次调用等到结果再返回；区别只在于背后是任务先进白板，
由常驻 Agent 异步认领执行的，接口内部用一个简单的轮询循环等结果：

```python
# main.py 新增接口
@app.post("/swarm/ask", response_model=SwarmAskResponse)
async def swarm_ask_endpoint(req: SwarmAskRequest) -> SwarmAskResponse:
    """
    用法跟 /ask 类似：提交一个任务、等结果、拿到最终状态再返回。
    跟 /ask 的区别在于编排方式——这里任务是发布到 Blackboard，由常驻的
    Swarm Agent 通过 claim() 认领执行的，不是主 Agent 直接调用子 Agent。
    """
    try:
        task_id = await _blackboard.post(req.task_type, req.payload)
    except ValueError as e:
        # 正常情况下 task_type 已经被 TaskType（Literal）挡在 API 校验层，
        # 这里兜底的是"合法类型但没有 Agent 注册"这种内部配置错误
        return SwarmAskResponse(task_id="", status="failed", error=str(e))

    # Blackboard 目前只在"有新任务发布"时唤醒等待者，没有"某个任务完成"的专属信号，
    # 所以这里用最简单的方式实现"等结果"：固定间隔轮询任务状态，直到 done/failed 或超时
    deadline = time.time() + req.timeout
    task = _blackboard.get_task(task_id)
    while task.status in ("pending", "claimed") and time.time() < deadline:
        await asyncio.sleep(0.3)
        task = _blackboard.get_task(task_id)

    return SwarmAskResponse(
        task_id=task_id, status=task.status,
        result=task.result, error=task.error,
    )
```

新增的 `try/except ValueError` 接住的是 7.3b 节 `Blackboard.post()` 里那句
`raise ValueError(f"未知任务类型：...")`。正常使用时这段代码几乎不会被触发——
外部传入的非法字符串已经被 7.5.1 节的 `TaskType`（`Literal`）挡在 Pydantic 校验层，
根本进不到这个函数体里面；这里兜底的是"请求体格式合法（`task_type` 确实是
`code_review`/`debug`/`test_write` 三者之一），但当前进程里没有任何 Agent 声明
处理它"这种**内部配置错误**，比如某次改动删掉了 `TestWriterSwarmAgent` 但忘了同步
从 `TaskType` 的 `Literal` 里去掉 `"test_write"`。捕获后返回
`SwarmAskResponse(task_id="", status="failed", error=str(e))`，调用方拿到的是一个
正常的 `200` 响应、`status="failed"`，而不是没处理异常导致的 `500` 服务器错误——
这是一个"服务内部确实出了配置问题，但不应该把整个请求处理流程搞挂"的防御性写法。

超时之后 `status` 还是 `pending`/`claimed` 也不算错误——说明任务确实还在跑（比如
LLM 调用比较慢，或者 `review → debug → test_write` 这种自动派生的任务链还没走完），
调用方可以按需加大 `timeout`，或者后面 7.7 节再加一个只读的状态查询接口按 `task_id`
补查。

### 7.5.4 测试

```bash
curl -X POST http://localhost:8002/swarm/ask \
  -H "Content-Type: application/json" \
  -d '{
        "task_type": "code_review",
        "payload": {"code": "def login(u, p): return db.execute(f\"SELECT * FROM users WHERE name={u}\")", "file": "auth.py"}
      }'
# {"task_id": "a1b2c3d4", "status": "done", "result": "...审查结果...", "error": null}
```

因为审查发现的是 SQL 注入这种 Critical 问题，`ReviewerSwarmAgent` 会自动往白板发一个
`debug` 任务，`DebuggerSwarmAgent` 接着认领执行——这个派生过程完全在后台发生，
`/swarm/ask` 这次调用只等最初那个 `code_review` 任务的结果，不会等到派生的
`debug` 任务也跑完，这也是 Swarm"涌现式协作"和 Coordinator"预先规划好任务图"
的直观区别（对比 6.3.1 节 Coordinator 的 JSON 任务计划，那里任务依赖是预先定好的）。

**验证 7.5.1 节的 API 层校验**：故意传一个不在 `TaskType` 范围里的字符串，确认会被
Pydantic 直接拦下、返回 422，而不是进入业务逻辑：

```bash
curl -s -X POST http://127.0.0.1:8002/swarm/ask \
  -H "Content-Type: application/json" \
  -d '{"task_type":"foo_bar","payload":{"code":"x=1","file":"a.py"}}'
# {"detail":[{"type":"literal_error","loc":["body","task_type"],
#   "msg":"Input should be 'code_review', 'debug' or 'test_write'", ...}]}
# HTTP 422
```

**验证 7.4c 节新增的 `TestWriterSwarmAgent` 能被单独直接调用**（不一定非要走
`code_review → debug` 派生出来，`test_write` 本身也是一个合法的、可以直接提交的
任务类型）：

```bash
curl -s -X POST http://127.0.0.1:8002/swarm/ask \
  -H "Content-Type: application/json" \
  -d '{"task_type":"test_write","payload":{"code":"def add(a,b): return a+b","file":"calc.py"},"timeout":40}'
# {"task_id":"...", "status":"done", "result":"```python\n# test_calc.py\n...", "error":null}
```

能看到 `status` 变成 `"done"`、`result` 里是生成的 pytest 代码，说明 `test_write`
这个任务类型现在有 Agent 会认领并跑完，不会再像本章开头描述的那样永远停在
`pending`。

---

## 7.6 把白板状态写入 Redis（持久化）

> 本节面向"零基础"读者，把每一步拆到能直接照抄命令的程度。如果你只想先跑通 Swarm 本身，
> 这一节可以整节跳过——`Blackboard` 不装 Redis 也能正常工作，只是进程一重启，
> 所有任务状态就没了。

### 7.6.0 为什么要用 Redis？先搞清楚问题是什么

回头看 `swarm/blackboard.py`：所有任务都存在 `self._tasks` 这个 Python 字典里。
字典是存在**进程内存**里的——一旦这个 Python 进程被杀掉（崩溃、重启、`Ctrl+C`），
内存被操作系统收回，字典里的数据**全部消失**，无法恢复。

Redis 是一个独立运行的、常驻后台的"内存数据库"服务。关键区别：它是**另一个进程**，
甚至可以在另一台机器上。你的 Swarm 程序只是通过网络连接过去"存一下 / 取一下"数据。
所以即使 Swarm 程序重启了，只要 Redis 服务本身没关，数据还在——这就是"持久化"的意思。

可以把 Redis 想象成一个只认"键值对"的超快哈希表，装在网络那头：
`redis_client.set("某个键", "某段字符串")`，之后随时 `redis_client.get("某个键")` 取回来，
换一个进程、换一台机器都能取。

### 7.6.1 安装并启动 Redis（Windows 环境，推荐用 Docker）

Redis 官方不提供 Windows 原生安装包，最省事的方式是用 Docker 跑一个 Redis 容器：

1. 安装 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（如果还没装），装完确保它在后台运行（系统托盘能看到鲸鱼图标）。
2. 打开终端（PowerShell 或本项目用的 Git Bash），执行：

   ```bash
   docker run -d --name redis-dev -p 6379:6379 redis:7-alpine
   ```

   命令拆解（新手逐字看）：
   - `docker run` —— 启动一个新容器
   - `-d` —— 后台运行（不占着当前终端窗口）
   - `--name redis-dev` —— 给这个容器起个好记的名字，之后可以用 `docker start/stop redis-dev` 控制它
   - `-p 6379:6379` —— 把容器内部的 `6379` 端口（Redis 默认端口）映射到你电脑的 `6379` 端口，这样电脑上的 Python 程序才能连上
   - `redis:7-alpine` —— 使用官方 Redis 7 镜像的 `alpine`（最小体积）版本

3. 确认启动成功：

   ```bash
   docker ps
   ```

   能看到一行 `redis-dev`，`STATUS` 列显示 `Up ...` 就说明跑起来了。

> 以后每次开机想再用 Redis，不用重新 `docker run`（那会报"容器名已存在"），
> 直接 `docker start redis-dev` 就行；不想用了执行 `docker stop redis-dev`。
>
> 备选方案（不想装 Docker）：Windows 下装 [WSL2](https://learn.microsoft.com/windows/wsl/install)，
> 进去之后就是一个 Linux 环境，`sudo apt install redis-server` 再 `redis-server` 即可，效果一样。

### 7.6.2 验证 Redis 能连上（用 redis-cli 手动操作）

`redis-cli` 是 Redis 自带的命令行客户端，专门用来手动查、改数据，调试时非常有用。
容器里已经自带这个工具，直接借用：

```bash
docker exec -it redis-dev redis-cli ping
```

正常应该返回 `PONG`。如果报错 / 卡住，说明容器没起来，回 7.6.1 用 `docker ps` 排查。

顺手练习一下"存"和"取"，建立直觉——这也是之后 Python 代码在做的事，只是换成了程序调用：

```bash
docker exec -it redis-dev redis-cli set hello world
docker exec -it redis-dev redis-cli get hello
# 应该打印：world
```

### 7.6.3 安装 Python 客户端库

`requirements.txt` 里只需要一行（0.2 节已同步更新，去掉了旧文档里多余的 `aioredis`）：

```bash
pip install "redis>=5.0.0"
```

**新手常踩的坑**：网上很多教程会让你额外 `pip install aioredis`。这是因为很早以前，
`redis` 这个包只支持"同步"调用（会阻塞程序），异步能力要靠另一个叫 `aioredis` 的包补充。
但从 `redis-py` 5.x 版本开始，异步能力已经合并进 `redis` 包本身（`redis.asyncio` 模块），
`aioredis` 项目已经停止维护。所以现在**只装 `redis` 一个包就够了**，装了 `aioredis`
也不会报错，但完全用不到，容易让人误以为两者缺一不可——本节及配套代码统一只用 `redis`。

### 7.6.4 创建 Redis 客户端连接

之前第 10 章的 `SessionStore` 例子里有一句注释「实际中应该在应用启动时初始化
`redis_client`」——这一节把这句注释真正落成代码。新建一个小文件专门负责"连接"这一件事：

```python
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
```

逐行解释：
- `redis.asyncio` —— `redis` 包里专门给 `async/await` 代码用的一套接口，和 Blackboard 里
  `async def` 的写法配套；如果写的是不带 `async` 的普通脚本，改成 `import redis` 用
  `redis.Redis(...)` 即可，用法基本一致。
- `os.getenv("REDIS_HOST", "localhost")` —— 优先读 `.env` 里的 `REDIS_HOST`，没配就默认
  连本机 `localhost`（也就是我们 7.6.1 里用 Docker 映射出来的地址）。
- `decode_responses=True` —— **强烈建议加上**。默认情况下 Redis 客户端返回的是
  `bytes`（比如 `b'{"a": 1}'`），拿去 `json.loads()` 之前还要手动 `.decode("utf-8")`，
  加上这个参数后直接拿到 `str`，少一步，也少一个新手容易忘记处理的报错来源。

在 `.env` 里加上（本机跑 Docker，密码留空即可）：

```dotenv
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
```

### 7.6.5 Blackboard 新增 save_to_redis / load_from_redis

相比旧版本，这里补上了异常处理：Redis 连不上时不应该让整个 Swarm 崩掉，而是打日志、
降级为"纯内存模式"继续跑（和第 10 章 `SessionStore` 的降级逻辑保持一致）。

```python
# swarm/blackboard.py 新增方法（在 Blackboard 类里）
import json
import dataclasses

async def save_to_redis(self, redis_client):
    """
    把当前白板状态保存到 Redis，支持重启后恢复。

    Redis 只能存字符串/字节，不能直接存 Python 对象，所以要先把
    Task（dataclass）转成 dict（dataclasses.asdict），再转成 JSON 字符串（json.dumps）。
    """
    try:
        async with self._lock:
            tasks_data = {tid: dataclasses.asdict(t) for tid, t in self._tasks.items()}
        await redis_client.set("blackboard:tasks", json.dumps(tasks_data))
    except Exception as e:
        # Redis 挂了不应该让 Swarm 崩溃，打日志降级为纯内存模式即可
        print(f"[Blackboard] 保存到 Redis 失败（降级为纯内存模式）：{e}")

async def load_from_redis(self, redis_client):
    """从 Redis 恢复白板状态（进程启动时调用一次）。"""
    try:
        data = await redis_client.get("blackboard:tasks")
    except Exception as e:
        print(f"[Blackboard] 连接 Redis 失败，跳过恢复，从空白板启动：{e}")
        return

    if not data:
        print("[Blackboard] Redis 里没有历史任务数据，从空白板启动")
        return

    tasks_data = json.loads(data)   # data 已经是 str（因为建连时设了 decode_responses=True）
    async with self._lock:
        for tid, t_dict in tasks_data.items():
            # 只恢复未完成的任务；claimed 的任务说明上次可能是处理到一半就被杀掉了，
            # 重新放回 pending，让某个 Agent 重新认领执行一遍
            if t_dict["status"] in ("pending", "claimed"):
                t_dict["status"] = "pending"
                t_dict["owner"] = None
                self._tasks[tid] = Task(**t_dict)
    print(f"[Blackboard] 从 Redis 恢复了 {len(self._tasks)} 个未完成任务")
```

### 7.6.6 接入 `main.py` 的 lifespan

不再有单独的 `swarm/main.py` 进程，Redis 持久化也直接接进 7.5.2 节 `main.py` 的
`lifespan`：启动时先恢复，运行期间定期备份，关闭前再存一次、最后关闭连接。

```python
# main.py 顶部新增 import
from persistence.redis_client import create_redis_client


async def periodic_save(blackboard: Blackboard, redis_client, interval: float = 5.0):
    """后台循环：每隔 interval 秒把白板状态备份到 Redis，直到被取消。"""
    while True:
        await asyncio.sleep(interval)
        await blackboard.save_to_redis(redis_client)


_redis_client = create_redis_client()
_swarm_save_task: asyncio.Task | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _swarm_save_task

    print("服务启动中...")
    print(f"API 文档地址：http://localhost:8002/docs")
    print(f"流式测试：curl -N 'http://localhost:8002/ask/stream?question=你好'")

    # 1. 启动时先尝试从 Redis 恢复上次未完成的任务
    await _blackboard.load_from_redis(_redis_client)

    # 2. 常驻 Swarm Agent
    agents = [
        ReviewerSwarmAgent(_blackboard),
        DebuggerSwarmAgent(_blackboard),
        TestWriterSwarmAgent(_blackboard),
    ]
    _swarm_agent_tasks.extend(asyncio.create_task(a.start()) for a in agents)

    # 3. 后台定期备份，不阻塞主流程
    _swarm_save_task = asyncio.create_task(periodic_save(_blackboard, _redis_client))

    yield

    # 4. 收尾：停 Agent、停后台备份任务、退出前再存一次、关闭连接
    for task in _swarm_agent_tasks:
        task.cancel()
    await asyncio.gather(*_swarm_agent_tasks, return_exceptions=True)

    _swarm_save_task.cancel()
    await _blackboard.save_to_redis(_redis_client)   # 退出前最后再存一次，避免丢掉最新几秒的变化
    await _redis_client.close()
    print("服务已关闭。")
```

为什么用"定期备份"而不是"每次 post/complete/fail 都存一次"：后者对 Redis 的写入压力
更大（任务多的时候几乎每次状态变化都要写一次网络请求），教学上先用简单的定时任务即可；
生产场景可以按需改成状态变化时立即存，二者不冲突，可以都要（定时兜底 + 关键节点立即存）。

### 7.6.7 手把手验证："杀掉进程再重启，任务还在"

这是验证持久化真的生效的最直接方式，全程只需要启动/重启 `main.py` 这一个进程：

1. 确认 Redis 容器在跑：`docker ps` 看到 `redis-dev` 状态 `Up`。
2. 正常启动服务：`python main.py`（或 `uvicorn main:app --reload`）。
3. 用 7.5.4 节的 `curl` 命令发一个 `/swarm/ask` 请求，**不用等它返回**（比如把
   `timeout` 设成 `1`，让接口很快因为超时返回一个 `pending`/`claimed` 状态，任务本身
   还在后台跑），等终端打印过一次 `periodic_save` 触发的写入（或者直接等 5 秒以上，
   对应 `interval=5.0`），然后按 `Ctrl+C` 强行中断进程，模拟"崩溃"。
4. 用 `redis-cli` 直接查看 Redis 里存了什么：

   ```bash
   docker exec -it redis-dev redis-cli get blackboard:tasks
   ```

   应该能看到一大段 JSON 字符串，里面是任务的 `id` / `type` / `status` 等字段。
5. 重新运行 `python main.py`，观察启动日志，应该出现：

   ```
   [Blackboard] 从 Redis 恢复了 N 个未完成任务
   ```

   看到这行，说明"进程重启、任务不丢"的持久化闭环跑通了——而且 `/ask` 和
   `/swarm/ask` 这两个入口是跟着同一次 `python main.py` 一起起来的，不需要
   分两个进程分别启动/重启。

### 7.6.8 常见问题排查

| 现象 | 原因 | 解决方法 |
|------|------|---------|
| `ConnectionRefusedError` / `Connection refused (6379)` | Redis 没启动，或者端口没映射对 | `docker ps` 确认容器状态；停了就 `docker start redis-dev` |
| `redis.exceptions.AuthenticationError` | `.env` 里配了 `REDIS_PASSWORD`，但 Redis 服务本身没设密码（或反过来） | 本机 Docker 跑的 Redis 默认没密码，`.env` 里 `REDIS_PASSWORD` 留空即可 |
| `TypeError: Object of type Task is not JSON serializable` | 忘了先用 `dataclasses.asdict()` 把 `Task` 转成 dict，就直接 `json.dumps(task)` | 检查 `save_to_redis` 里是否先 `asdict` 再 `json.dumps` |
| 重启后日志没打印"恢复了 N 个任务"，Redis 里也查不到 key | `periodic_save` 还没跑到第一次（`interval` 设太长）就被 `Ctrl+C` 杀掉了 | 调小 `interval` 测试，或者等够 `interval` 秒再中断 |
| `docker: command not found` | 没装 Docker Desktop，或装了但没加进终端 PATH | 重新安装 Docker Desktop，安装后重开一个终端窗口 |

---

## 7.7 可选：按 `task_id` 补查状态 `GET /swarm/tasks/{task_id}`

`POST /swarm/ask`（7.5 节）已经是主要的调用入口，正常情况下不需要别的接口。但 7.5.3
节提到过：如果任务超过 `timeout` 还没跑完（比如审查发现问题后自动派生的 `debug`
任务排在后面，链路比较长），`/swarm/ask` 只能告诉你当时的状态是 `pending`/`claimed`，
这时候需要一个只读的补查接口，用 `task_id` 随时再查一次最新状态——这不是给调用方
日常使用的主入口，纯粹是给"任务还没完成"这一种情况兜底：

```python
# main.py 顶部追加 import
from fastapi import HTTPException


@app.get("/swarm/tasks/{task_id}", response_model=SwarmAskResponse)
async def get_swarm_task(task_id: str) -> SwarmAskResponse:
    """按 task_id 补查任务最新状态，配合 /swarm/ask 超时后的场景使用。"""
    task = _blackboard.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")
    return SwarmAskResponse(
        task_id=task.id, status=task.status,
        result=task.result, error=task.error,
    )


@app.get("/swarm/tasks")
async def list_swarm_tasks() -> dict:
    """白板整体状态摘要，如 {"pending": 2, "done": 5}，运维/监控用，不是业务调用入口。"""
    return _blackboard.summary()
```

```bash
curl http://localhost:8002/swarm/tasks/a1b2c3d4
# {"task_id": "a1b2c3d4", "status": "done", "result": "...", "error": null}
```

`/ask` 的代码（6.9 节）完全不用动——`/ask` 和 `/swarm/ask`（+ 这个可选的补查接口）
各自独立、互不干扰，这正是 7.1.1 节说的"由调用方选接口决定模式"，而不是共用一个
接口再靠开关分流。

---

## 7.9 派生任务可获取：`derived_task_ids`

### 7.9.0 问题：派生任务的 `task_id` 只活在 `print()` 里

7.4 节 `ReviewerSwarmAgent` 判定 `NEEDS_FIX:true` 后会 `await self.blackboard.post(...)`
自动发一个 `debug` 任务，7.4b 节 `DebuggerSwarmAgent` 修复完再发一个 `test_write`
任务——这条 `code_review → debug → test_write` 的链路本身没问题，但派生出来的
`task_id` 只在 Agent 内部 `print(f"...已发布 debug 任务 {debug_task_id}")` 里出现过
一次，从来没有被写回 `Task` 本身，也没有透传到 `/swarm/ask` 的响应体。调用方拿到
最初那个 `code_review` 任务的结果后，**没有任何编程接口能知道后台还发生过什么**：
链路是不是自动闭环处理了问题，闭环处理的结果是什么，只能翻服务器日志肉眼找。
7.7 节的 `GET /swarm/tasks/{task_id}` 能补查状态，但前提是你已经知道要查哪个
`task_id`——而派生任务的 `task_id` 恰恰是缺失的那一环。

这一节把"派生出的子任务 ID"变成 `Task` 对象上的一个正式字段，`post()` 和
`post_derived()`（新方法）分工明确：外部提交的入口任务走 `post()`；Agent 内部
因为处理某个任务而自动派生新任务，走 `post_derived()`，由白板自己维护父子关系。

### 7.9.1 `swarm/blackboard.py`：`Task` 新增字段 + `post_derived()`

```python
# swarm/blackboard.py
@dataclass
class Task:
    """白板上的一个任务。"""
    id: str
    type: str
    payload: Any
    status: str = "pending"
    owner: str | None = None
    result: Any = None
    error: str | None = None
    derived_task_ids: list[str] = field(default_factory=list)  # 由这个任务自动派生出的子任务 ID


class Blackboard:
    # ... post() / claim() / complete() / fail() 保持不动 ...

    async def post_derived(self, parent_task_id: str, task_type: str, payload: Any) -> str:
        """
        Agent 处理任务时自动派生新任务用这个方法，而不是直接调 post()。

        效果跟 post() 完全一样（一样会经过 _known_types 校验、一样会唤醒等待中的
        Agent），多做的唯一一件事是：把新生成的 child_id 记到父任务的
        derived_task_ids 里，让"这个任务后来触发了什么"变得可查询，而不是只存在于
        日志里。
        """
        child_id = await self.post(task_type, payload)

        async with self._lock:
            parent = self._tasks.get(parent_task_id)
            if parent is not None:
                parent.derived_task_ids.append(child_id)
            else:
                # 父任务不存在（比如传错了 ID）不应该导致子任务发布失败——
                # 子任务本身是合法、独立的任务，只是丢了追溯关系，打日志即可
                print(f"[Blackboard] post_derived: 父任务 {parent_task_id} 不存在，"
                      f"子任务 {child_id} 仍正常发布，但不会出现在任何 derived_task_ids 里")

        return child_id
```

`post()` 本身不用改一行——它仍然是"任何 Agent 或外部代码都能调用的通用发布入口"，
`/swarm/ask`（7.5.3 节）提交的入口任务继续走它。`post_derived()` 是在它外面包了一层
"记父子关系"的逻辑，两者不是互斥关系。

父任务不存在时选择**降级为普通 `post()` + 打警告**，而不是抛异常：设想
`DebuggerSwarmAgent` 修复完代码后 `origin_task` 字段被传错，直接让整条链路因为
一个记录不上的追溯关系而失败，这个代价比"丢一条追溯关系，但功能本身正常跑完"
大得多——两种失败模式里，教学上选后者。

### 7.9.2 `reviewer_agent.py` / `debugger_agent.py`：改用 `post_derived`

```python
# swarm/reviewer_agent.py —— NEEDS_FIX 分支
if "NEEDS_FIX:true" in result:
    debug_task_id = await self.blackboard.post_derived(
        parent_task_id=task.id,
        task_type=TASK_TYPE_DEBUG,
        payload={
            "code": code,
            "file": filename,
            "review_result": result,
            "origin_task": task.id,
        }
    )
    print(f"[{self.agent_id}] 发现 Critical 问题，已发布 debug 任务 {debug_task_id}")
```

```python
# swarm/debugger_agent.py —— handle() 结尾
test_write_task_id = await self.blackboard.post_derived(
    parent_task_id=task.id,
    task_type=TASK_TYPE_TEST_WRITE,
    payload={
        "code": result,
        "file": filename,
        "origin_task": task.id,
    }
)
print(f"[{self.agent_id}] 修复完成，已发布 test_write 任务 {test_write_task_id}")
```

两处改动只是把 `self.blackboard.post(...)` 换成 `self.blackboard.post_derived(task.id, ...)`，
`print()` 日志照旧保留（排障时还是有用），区别是这次返回的 `task_id` 不再是"打完日志
就丢掉的局部变量"，而是真正落到了 `task.derived_task_ids` 里。

### 7.9.3 `main.py`：`SwarmAskResponse` 暴露 `derived_task_ids`

```python
# main.py
class SwarmAskResponse(BaseModel):
    """POST /swarm/ask 的响应体格式"""
    task_id: str
    status: str
    result: Any = None
    error: str | None = None
    derived_task_ids: list[str] = Field(default_factory=list, description="这个任务自动派生出的子任务 ID 列表，可用 GET /swarm/tasks/{id} 逐个查询")


@app.post("/swarm/ask", response_model=SwarmAskResponse)
async def swarm_ask_endpoint(req: SwarmAskRequest) -> SwarmAskResponse:
    try:
        task_id = await _blackboard.post(req.task_type, req.payload)
    except ValueError as e:
        return SwarmAskResponse(task_id="", status="failed", error=str(e))

    deadline = time.time() + req.timeout
    task = _blackboard.get_task(task_id)
    while task.status in ("pending", "claimed") and time.time() < deadline:
        await asyncio.sleep(0.3)
        task = _blackboard.get_task(task_id)

    return SwarmAskResponse(
        task_id=task_id, status=task.status,
        result=task.result, error=task.error,
        derived_task_ids=task.derived_task_ids,
    )
```

**同时这一节把 7.7 节只是"描述"过、但当时 `main.py` 里还没有真正写出来的两个只读
接口正式落地**（7.7 节写的代码块可以原样抄进 `main.py`，这里不重复贴）：

- `GET /swarm/tasks/{task_id}` → 返回 `SwarmAskResponse`（含 `result`/`error`/
  `derived_task_ids`），任务不存在时 `raise HTTPException(404, ...)`；
- `GET /swarm/tasks` → 返回 `_blackboard.summary()`，形如 `{"pending": 2, "done": 5}`。

有了 `derived_task_ids` 之后，这两个接口不再只是"7.5.3 节超时兜底"用的备用查询，
而是变成调用方追一条完整链路的正式方式：

```bash
# 1. 提交入口任务，立刻拿到响应（不等派生链路跑完）
curl -s -X POST http://127.0.0.1:8002/swarm/ask -H "Content-Type: application/json" -d '{
  "task_type": "code_review",
  "payload": {"code": "def login(u): return db.execute(f\"SELECT * FROM users WHERE name={u}\")", "file": "auth.py"}
}'
# {"task_id":"a1b2c3d4","status":"done","result":"...","error":null,"derived_task_ids":["e5f6g7h8"]}

# 2. derived_task_ids 里的 debug 任务，按 ID 轮询直到 done
curl -s http://127.0.0.1:8002/swarm/tasks/e5f6g7h8
# {"task_id":"e5f6g7h8","status":"done","result":"...修复后的代码...","error":null,"derived_task_ids":["i9j0k1l2"]}

# 3. debug 任务的 derived_task_ids 里又能挖出 test_write 任务，同理继续查
curl -s http://127.0.0.1:8002/swarm/tasks/i9j0k1l2
```

`derived_task_ids` 是一层一层的——`code_review` 任务的 `derived_task_ids` 里只有它
直接派生的 `debug` 任务，`debug` 任务再派生的 `test_write` 任务要去 `debug` 任务自己
的 `derived_task_ids` 里找，这跟树形结构逐层展开是一致的，调用方要走完整条链路
需要递归地查下去，而不是指望入口任务的响应体里一次性拿到所有子孙任务 ID。

---

## 7.10 显式落地：`POST /swarm/tasks/{task_id}/apply`

### 7.10.0 问题：Agent 的产出是文本，从来没有真正写回文件

7.4b 节 `DebuggerSwarmAgent` 的输出格式里第 2 项是"修复后的完整代码（\`\`\`python
代码块）"，7.4c 节 `TestWriterSwarmAgent` 的输出也是一个 \`\`\`python 代码块——
但这两个 Agent 的 `handle()` 最后都只是 `return result`，`result` 是一整段夹杂着
说明文字的纯文本，写进 `Task.result` 就停在那了。7.9 节让调用方能查到这段文本，
但"查到"和"落地"是两件事：调用方要么自己写正则从 `result` 里抠代码块再手动存盘，
要么这段修复干脆就只停留在一次 HTTP 响应里，改完了等于没改。这一节补上"显式落地"
这最后一步：新增一个接口，从任务结果里抽代码、写到调用方指定的路径。

**apply 接口和 `write_file` 工具的关系？** 两者写盘时**共用同一套校验**——都调
`core/workspace.py` 的 `write_text_sandboxed()`，都限定在统一工作目录（`WORKSPACE`）内、
都拦截 `../` 穿越和绝对路径。`write_file` 是给 Agentic Loop 里的 LLM 主动调用的工具
（第 5 章），apply 是给 HTTP 调用方用的接口，但只要涉及"把字符串路径写盘"，就绝不能
不校验——否则调用方（或被诱导的 LLM）传一个 `"../../../etc/bad"` 就能往工作目录之外
写文件，这是真实的路径穿越漏洞。

apply 之所以还单独存在、不是直接调 `write_file`，是因为它比"写盘"多了**前置一步**：
Agent 的任务结果是"说明文字 + \`\`\`python 代码块"混在一起的自由文本，得先把代码块
抽出来（下面的 `code_extractor.py`），再把**纯代码**交给 `write_text_sandboxed()` 落地。
写盘这一层，apply 和 `write_file` 走的是同一个函数。

### 7.10.1 `swarm/code_extractor.py`：从任务结果里抽代码块

```python
# swarm/code_extractor.py
"""
从 Agent 的自由文本产出里抽出可写盘的 Python 代码。

DebuggerSwarmAgent / TestWriterSwarmAgent 的输出格式（7.4b / 7.4c 节的 system
prompt 里约定的）都是"说明文字 + ```python 代码块"混在一起的一整段文本，
apply 接口只关心代码块本身，说明文字要剥掉。
"""
import re

_PYTHON_FENCE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)


def extract_python_code(text: str) -> tuple[str | None, str]:
    """
    从文本里抽出代码块，返回 (代码, 来源标记)。

    优先级：
      1. 第一个 ```python ... ``` 围栏 —— DebuggerSwarmAgent/TestWriterSwarmAgent
         的 system prompt 明确要求了这个格式，是最可信的情况
      2. 兜底：任意 ``` ... ``` 围栏（LLM 有时候会漏写语言标记）
      3. 都没有：返回 (None, "none")，调用方（main.py 的 apply 接口）据此返回 400

    来源标记透传给响应体，方便调用方知道这次写盘的内容有没有经过降级兜底。
    """
    m = _PYTHON_FENCE.search(text)
    if m:
        return m.group(1).strip(), "python_block"

    m = _ANY_FENCE.search(text)
    if m:
        return m.group(1).strip(), "fenced_block"

    return None, "none"
```

只取**第一个**代码块——`DebuggerSwarmAgent` 的输出里理论上只有一段"修复后的完整
代码"，如果 LLM 输出里出现了多个代码块（比如举例说明"改之前 vs 改之后"），
取第一个通常就是我们要的那段；真要支持"抽取所有代码块"，教学上先不做，等真的
遇到这种输出再加。

### 7.10.2 apply 接口的写盘校验：复用 `core/workspace.py`

apply 接口写文件同样要做路径穿越校验。这套逻辑已经在第 0 章的 `core/workspace.py`
里实现（`resolve_safe_path` / `write_text_sandboxed`），并被所有工具共用——apply
接口不单独维护一套，直接 `from core.workspace import ...` 复用同一个工作目录
（环境变量 `WORKSPACE`），保证"read_file 读的目录"和"apply 写的目录"完全一致：

```python
# main.py 里 apply 接口直接从 core.workspace 引入
from core.workspace import resolve_safe_path, PathTraversalError
```

> **一套沙箱，全项目共用。** 早期设计曾让 swarm 用独立的 `swarm/sandbox.py` + `SWARM_WORKSPACE` 环境变量，想着"写盘目录可能和读文件目录不同"。但实践下来，一个 Agent 读的、搜的、写的、执行的应该是**同一个工作区**才符合直觉——尤其飞书/HTTP 是外部入口，多套目录配置反而是隐患。所以工作目录统一收敛到 `core/workspace.py` 的单一来源（`WORKSPACE`），apply 接口、5 个工具都调它，不再有第二份实现。

`core/workspace.py` 里的穿越检测（`workspace not in target.parents`）配合 `resolve()`
是关键——`resolve()` 会把 `..` 这种相对片段真正计算掉，拿到操作系统层面"最终落到哪"
的绝对路径，再用 `Path.parents` 做层级判断（比字符串 `startswith` 更严谨，不会把
`/data/ws-evil` 误判成在 `/data/ws` 内），而不是对字符串里有没有 `".."` 做黑名单匹配
（黑名单容易被 `....//` 之类变形绕过）。

### 7.10.3 `main.py`：`ApplyRequest`/`ApplyResponse` + 接口实现

```python
# main.py 新增 import
from swarm.code_extractor import extract_python_code
from core.workspace import resolve_safe_path, PathTraversalError


class ApplyRequest(BaseModel):
    """POST /swarm/tasks/{task_id}/apply 的请求体格式"""
    path: str = Field(..., min_length=1, description="写入的目标路径，相对工作目录（WORKSPACE），如 'out/auth.fixed.py'")


class ApplyResponse(BaseModel):
    """POST /swarm/tasks/{task_id}/apply 的响应体格式"""
    path: str
    bytes_written: int
    source: str   # python_block | fenced_block（见 7.10.1 节 extract_python_code）


@app.post("/swarm/tasks/{task_id}/apply", response_model=ApplyResponse)
async def apply_swarm_task(task_id: str, req: ApplyRequest) -> ApplyResponse:
    """
    把任务结果里的代码块真正写到磁盘。

    不限制 task_type——debug / test_write 的结果本来就是代码块，能正常抽取；
    code_review 的结果是审查意见（散文），大概率抽不出代码块，走到下面的
    400（这是预期行为，不针对 task_type 单独加白名单/黑名单）。
    """
    task = _blackboard.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    if task.status != "done":
        raise HTTPException(status_code=409, detail=f"任务 {task_id} 当前状态是 {task.status}，未完成不能落地")

    if not isinstance(task.result, str) or not task.result.strip():
        raise HTTPException(status_code=400, detail=f"任务 {task_id} 没有可写入的文本结果")

    code, source = extract_python_code(task.result)
    if code is None:
        raise HTTPException(status_code=400, detail="任务结果里没有找到可抽取的代码块（可能是 code_review 这类审查意见，本身不含代码）")

    try:
        target = resolve_safe_path(req.path)
    except PathTraversalError as e:
        raise HTTPException(status_code=400, detail=str(e))

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(code, encoding="utf-8")

    return ApplyResponse(path=str(target), bytes_written=len(code.encode("utf-8")), source=source)
```

四个失败分支对应四种"调用方搞错了什么"，故意用不同的 HTTP 状态码区分，而不是
统一包一层 `{"error": "..."}` 用 200 返回：

| 状态码 | 触发条件 | 含义 |
|--------|---------|------|
| 404 | `task_id` 不存在 | 拼错 ID，或者早就没这个任务 |
| 409 | 任务存在但 `status != "done"` | 还没跑完（`pending`/`claimed`）或跑失败了（`failed`），语义上跟"资源当前状态跟请求的操作冲突"（HTTP 409 Conflict 的标准用法）一致 |
| 400（无结果） | `task.result` 不是非空字符串 | 理论上 `status == "done"` 时 `result` 应该有内容，这里是防御性兜底 |
| 400（抽不出代码） | `extract_python_code` 返回 `(None, "none")` | 最常见触发场景就是拿 `code_review` 任务去调 apply |
| 400（路径穿越） | `resolve_safe_path` 抛 `PathTraversalError` | 7.10.2 节的沙箱校验没通过 |

**不覆盖原文件，除非调用方显式传原文件路径**——这一点没有做成"默认拒绝覆盖已有
文件"这种额外校验，而是直接遵循"`path` 是调用方主动指定的，指定成什么就写到哪"：
如果调用方想覆盖 `auth.py`，把 `req.path` 传成 `"auth.py"` 本身就是"显式"这件事
的全部含义；接口不做隐藏的"重命名成 `.bak`"或"追加时间戳"之类的自作聪明，那样反而
会让调用方摸不清最终文件到底叫什么。

### 7.10.4 验证

```bash
# 1. 启动服务（Redis 是 7.6 节的可选项，不装不影响这一节）
python main.py

# 2. 提交一个带 SQL 注入的 code_review 任务
curl -s -X POST http://127.0.0.1:8002/swarm/ask -H "Content-Type: application/json" -d '{
  "task_type": "code_review",
  "payload": {"code": "def login(u): return db.execute(f\"SELECT * FROM users WHERE name={u}\")", "file": "auth.py"},
  "timeout": 60
}'
# 期望响应里 derived_task_ids 非空（至少一个 debug 任务，对应 7.9 节）

# 3. 轮询派生的 debug 任务直到 done
curl -s http://127.0.0.1:8002/swarm/tasks/<debug_task_id>

# 4. 落地写盘
curl -s -X POST http://127.0.0.1:8002/swarm/tasks/<debug_task_id>/apply \
  -H "Content-Type: application/json" -d '{"path": "out/auth.fixed.py"}'
# {"path":"/.../out/auth.fixed.py","bytes_written":...,"source":"python_block"}
# 期望：out/auth.fixed.py 被创建，内容是抽出来的修复后代码

# 5. 路径穿越回归：应被拒绝
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8002/swarm/tasks/<debug_task_id>/apply \
  -H "Content-Type: application/json" -d '{"path": "../../../etc/bad"}'
# 400

# 6. 白板整体摘要
curl -s http://127.0.0.1:8002/swarm/tasks
# {"done": N, ...}
```

---

## 7.11 本章检查清单

```
□ Blackboard 的 claim() 是原子操作（并发认领同一任务不会出现竞态）
  验证：同时启动 3 个 ReviewerSwarmAgent，发布 1 个任务，
        只有 1 个 Agent 认领成功（其他 claim() 返回 None）

□ 任务完成后 blackboard.get_task(id).status == "done"

□ Agent 失败时 status == "failed"，error 字段有错误信息

□ swarm/agent_base.py 的抽象接口（task_types / handle）跟
  reviewer_agent.py / debugger_agent.py / test_writer_agent.py 的实现一致，
  实例化 ReviewerSwarmAgent(blackboard) 不报 TypeError（参考 7.3 节末尾的提醒）

□ task_type 值域校验（7.3b / 7.5.1 节）：
  1. 提交一个不在 swarm/task_types.py 的 TaskType 范围内的 task_type（如 "foo_bar"），
     收到 HTTP 422，错误信息里带出合法取值范围
  2. swarm/blackboard.py 的 register_consumer() 已在 SwarmAgent.__init__ 里
     自动调用，_known_types 集合里能看到三个 Agent 各自声明的类型

□ test_write 链路补全（7.4c 节）：
  1. main.py 的 lifespan 的 agents 列表里有 ReviewerSwarmAgent / DebuggerSwarmAgent /
     TestWriterSwarmAgent 三个实例
  2. 直接提交 task_type="test_write" 能收到 status == "done" 的结果（不需要先走
     code_review → debug 派生）
  3. 提交会被判定为 Critical 的 code_review 任务后，观察终端日志，能看到完整链路：
     code_review 完成 → 自动派生 debug 任务 → debug 完成 → 自动派生 test_write 任务 →
     test_write 完成，每一步都有对应 Agent 认领（不再有任务永远停在 pending 且无人处理）

□ 只启动一个 `python main.py` 进程，/ask 和 /swarm/ask 都能访问到：
  1. POST /ask 走 Coordinator，一次调用拿到最终答案
  2. POST /swarm/ask 走 Blackboard，内部 post() 后轮询直到 done/failed 或超时返回
  3. 这期间两个接口互不影响——调用 /swarm/ask 不会拖慢 /ask 的响应时间

□ /swarm/ask 提交一个会被 ReviewerSwarmAgent 判定为 Critical 的 code_review 任务：
  1. 响应里能看到 status == "done" 的审查结果
  2. 响应体的 derived_task_ids 非空（7.9 节），能看到自动派生的 debug 任务 ID
     （不需要再翻日志——这是 7.9 节相对本章早期版本的改进点）

□ （可选）Redis 持久化：
  1. docker exec -it redis-dev redis-cli ping 返回 PONG
  2. 运行中 docker exec -it redis-dev redis-cli get blackboard:tasks 能看到 JSON 数据
  3. Ctrl+C 杀掉 `python main.py` 后重新启动，日志出现「从 Redis 恢复了 N 个未完成任务」，
     且未完成任务重新出现在 pending 队列（而不是永久消失）

□ 派生任务追踪（7.9 节）：
  1. Task dataclass 有 derived_task_ids 字段，post_derived() 会把 child_id 追加到
     父任务的 derived_task_ids 里（父任务不存在时降级为普通 post()，只打警告不报错）
  2. reviewer_agent.py / debugger_agent.py 都已改成调用 post_derived(task.id, ...)
     而不是 post(...)
  3. /swarm/ask 的响应体、GET /swarm/tasks/{task_id} 的响应体都带 derived_task_ids，
     能顺着这个字段递归查到 debug → test_write 整条派生链路

□ apply 接口落地写盘（7.10 节）：
  1. swarm/code_extractor.py 的 extract_python_code() 对 ```python 代码块、无语言
     标记的裸代码块、纯审查意见（无代码块）三种输入分别返回预期的 (代码, 来源) /
     (None, "none")
  2. core/workspace.py 的 resolve_safe_path() 对 "../../../etc/bad" 这类穿越路径
     抛 PathTraversalError，对正常相对路径能正确解析到统一工作目录（WORKSPACE）内
  3. POST /swarm/tasks/{task_id}/apply 四种失败分支状态码分别正确：
     任务不存在 → 404；任务未完成（pending/claimed/failed）→ 409；
     无代码块可抽取（如拿 code_review 任务去调）→ 400；路径穿越 → 400
  4. 对一个 status == "done" 的 debug/test_write 任务调用 apply，指定路径下
     真的生成了文件，内容是抽取出的代码本身（不含说明文字）
  5. 对同一个任务用同一个 path 再调一次 apply，文件被正常覆盖（没有隐藏的
     重命名/加时间戳行为——"不覆盖除非显式传原路径"完全由调用方通过 path 控制）
```

---

# 第 8 章：阶段 8 —— 上下文管理

> **本章目标**：优雅处理长对话。Token 超限时自动压缩历史，同时用 Prompt Cache 降低重复成本。

---

## 8.1 三个核心问题

1. **Token 超限**：对话历史积累到一定程度，`context_length_exceeded` 错误
2. **成本浪费**：每次都把完整的 system prompt（可能几千 Token）重新发送
3. **信息丢失**：压缩时如何保留关键信息，不让 Agent 忘事

---

## 8.2 Token 预算策略

```python
# agent/context.py
"""
上下文管理：Token 预算检查 + 历史压缩。
"""
from providers.types import Message, TextBlock

# Claude 的上下文窗口（不同模型不同，claude-sonnet-4 是 200k）
MAX_CONTEXT_TOKENS = 180_000   # 留 20k 余量
COMPRESS_THRESHOLD = 0.8       # 达到 80% 时触发压缩（180k * 0.8 = 144k）


def estimate_tokens(messages: list[Message]) -> int:
    """
    估算消息列表的 Token 数。

    精确计算需要 tokenizer，这里用简单的字符数估算：
    - 中文：约 1 字 = 1.5 Token
    - 英文：约 4 字符 = 1 Token

    这是粗估，误差约 20%，足够用于触发压缩的判断。
    """
    total_chars = 0
    for msg in messages:
        for block in msg.content:
            if isinstance(block, TextBlock):
                total_chars += len(block.text)
            else:
                total_chars += 100  # 工具调用块的估计大小

    # 简单估算：平均每字符约 0.8 Token
    return int(total_chars * 0.8)


def should_compress(messages: list[Message]) -> bool:
    """判断是否需要压缩历史。"""
    estimated = estimate_tokens(messages)
    threshold = MAX_CONTEXT_TOKENS * COMPRESS_THRESHOLD
    if estimated > threshold:
        print(f"[Context] 估算 {estimated} tokens，超过阈值 {threshold}，触发压缩")
        return True
    return False
```

---

## 8.3 滑动窗口压缩

```python
# agent/context.py（续）

async def compress_messages(
    messages: list[Message],
    provider,
    keep_recent_turns: int = 8,
) -> list[Message]:
    """
    压缩对话历史，保留最近 N 轮 + 对早期历史的摘要。

    压缩策略：
    1. 把最近 keep_recent_turns 轮（每轮 = 1 user + 1 assistant）保留完整内容
    2. 对更早的历史，调用 LLM 生成摘要（一段简洁的总结）
    3. 返回：[摘要消息] + [最近 N 轮]

    为什么不直接截断？
    截断会丢失对话的前因后果，Agent 会忘记之前的重要决定。
    摘要保留了关键信息，即使不是一字不差的原文。
    """
    # 每轮 = 2 条消息（user + assistant），保留最近 N 轮
    keep_count = keep_recent_turns * 2

    if len(messages) <= keep_count:
        return messages   # 历史不够长，不需要压缩

    old_messages = messages[:-keep_count]
    recent_messages = messages[-keep_count:]

    # 构建摘要提示词。这里喂给摘要模型的是每条历史消息的完整内容，不截断——
    # 摘要只能覆盖它看得到的文本，若在这一步先砍到 500 字，被砍掉的决策/数据
    # 就永远进不了摘要，等于历史直接丢失。摘要输出的 max_tokens 已经控制了压缩后的体积。
    history_text = ""
    for msg in old_messages:
        role_label = "用户" if msg.role == "user" else "助手"
        for block in msg.content:
            if isinstance(block, TextBlock):
                history_text += f"[{role_label}]: {block.text}\n"

    summary_prompt = f"""
请对以下历史对话进行简洁的摘要，保留所有重要决策、数据、用户需求和问题。
摘要应当让读者理解对话的主要脉络，不需要完整重现每一句话。

历史对话：
{history_text}

请输出摘要：
"""

    summary_response = await provider.chat(
        messages=[Message(role="user", content=[TextBlock(text=summary_prompt)])],
        system="你是专业的对话摘要助手，请提炼对话要点。",
        max_tokens=800,
    )

    summary_text = ""
    for block in summary_response.content:
        if isinstance(block, TextBlock):
            summary_text += block.text

    # 把摘要作为第一条"用户消息"注入（对话必须以 user 开头）
    summary_message = Message(
        role="user",
        content=[TextBlock(text=f"【对话历史摘要】\n{summary_text}")]
    )

    print(f"[Context] 压缩完成：{len(messages)} 条 → 1条摘要 + {len(recent_messages)} 条")
    return [summary_message] + recent_messages
```

---

## 8.4 在 Agentic Loop 中集成上下文管理

```python
# agent/loop.py 的 run_agent_loop 函数里，在每轮 LLM 调用前加：

from .context import should_compress, compress_messages

# 在 while True 循环的开头，调用 provider 之前：
current_messages = list(state.messages)

if should_compress(current_messages):
    current_messages = await compress_messages(current_messages, provider)
    state = state.with_messages(current_messages)

# 然后用 current_messages 而不是 state.messages 来调用 provider
```

---

## 8.5 Anthropic Prompt Cache（降低 90% 成本）

**Prompt Cache 是什么？**

Anthropic 支持对 system prompt 启用缓存。第一次调用时，Anthropic 把 system prompt 缓存 5 分钟。后续 5 分钟内的请求命中缓存，这部分 Token 的费用降低约 90%。

适用场景：system prompt 很长（1000+ Token），且同一 Agent 短时间内被频繁调用。

```python
# providers/anthropic.py — 在 chat() 和 stream() 里添加 cache_control

# 发送时，给 system prompt 加 cache_control 标记：
kwargs["system"] = [
    {
        "type": "text",
        "text": system,
        "cache_control": {"type": "ephemeral"},   # ephemeral = 5 分钟 TTL
    }
]

# 查看是否命中缓存（在 usage 里）：
# resp.usage.cache_read_input_tokens > 0   → 命中了缓存（费用约 10%）
# resp.usage.cache_creation_input_tokens > 0 → 第一次写入缓存（费用约 125%，但后续调用省钱）
```

> 💡 **什么时候启用 Prompt Cache？**
> - system prompt 超过 1024 Token（太短缓存意义不大）
> - 同一 Agent 被频繁调用（稀少调用命中率低）
> - 使用 Anthropic Provider（其他 Provider 不支持此特性）

---

## 8.6 本章检查清单

```
□ estimate_tokens() 对中等长度对话的估算误差在 30% 以内

□ should_compress() 能正确判断触发条件

□ compress_messages() 压缩后，历史条数减少，摘要条数为 1

□ 压缩后 Agent 仍能理解"刚才说的"（通过摘要）

□ （Anthropic 用户）Prompt Cache 生效
  验证：第二次调用同一 system prompt，usage.cache_read_input_tokens > 0
```

---

# 第 9 章：阶段 9 —— Skills 系统

> **本章目标**：让 LLM 通过工具调用**按需加载**专业知识，而不是把所有知识塞进 system prompt。
> 解决"system prompt 越来越长"的根本问题。
> 核心机制：**system prompt 常驻一张极短的「技能索引表」，LLM 语义匹配后自主调用
> `get_skill_guide(skill_name)` 工具加载对应 SKILL.md 全文。**

---

## 9.1 为什么需要 Skills 系统？

随着业务发展，Agent 需要掌握的知识越来越多：代码审查规范、数据库查询技巧、业务流程说明……

**把所有知识塞进一个 system prompt** 的问题：

| 问题 | 症状 |
|------|------|
| system prompt 太长 | Token 浪费，LLM 注意力分散，回答质量下降 |
| 多领域知识混杂 | LLM 可能把 A 领域的规则用到 B 领域 |
| 更新困难 | 改一处可能影响全局行为 |

**Skills 系统的解法**：把每个专业领域写成独立的 SKILL.md 文件，**由 LLM 自己决定何时加载哪一个**。

具体做法分两层：

1. **常驻的技能索引表**：把每个 Skill 的 `name + 一句话描述`（不是全文！）渲染成一张很短的表，
   拼进 system prompt。LLM 因此始终"知道有哪些技能可用、各自管什么"，但不占用多少 token。
2. **按需加载工具 `get_skill_guide`**：LLM 判断当前任务需要某个 Skill 时，**主动发起一次工具调用**
   `get_skill_guide(skill_name)`，工具读取该 SKILL.md 全文，作为 `tool_result` 注入对话历史。
   之后 LLM 就带着这份完整指南继续工作。

```
用户请求："帮我审查这段 Python 代码"
  ↓
system prompt 里常驻的索引表告诉 LLM：有个 code-review 技能，管代码审查
  ↓
LLM 自主决定：这个任务该用 code-review → 发起工具调用 get_skill_guide("code-review")
  ↓
工具读取 code-review.md 全文 → 作为 tool_result 回填进对话
  ↓
Agent 带着代码审查专业知识（含审查流程、输出格式）继续执行任务
```

### 两种哲学的对比：预注入 vs 按需加载

早期实现（本章的旧版本）是**代码预注入**：每次 `run()` 时用 TF-IDF 之类的算法搜出相关 Skill，
把全文强行拼进 system prompt。这一版改成**LLM 自主按需加载**，差异如下：

| 维度 | 预注入（旧） | 按需加载（本章） |
|------|-------------|-----------------|
| 谁决定加载哪个 Skill | 代码里的检索算法（TF-IDF/关键词） | LLM 自己按语义判断 |
| 加载时机 | 每次调用**无条件**搜索并拼接 | 仅当 LLM 认为需要时才发起工具调用 |
| system prompt 体积 | 塞进 top-N 全文，越来越大 | 只常驻极短索引表 |
| 不需要 Skill 的任务 | 照样塞一堆无关规范，稀释注意力 | LLM 不调工具，零额外开销 |
| 能否加载多个 / 追加加载 | 固定 top-N，一次性 | LLM 可在推理中多次、按需追加加载 |

> 这正是 Claude Code、以及本项目参考的 DDH-main-agent 等成熟 Agent 采用的模式：
> **把"要不要用某个能力、什么时候用"的决策权交还给模型**，代码只负责提供索引和加载工具。

---

## 9.2 SKILL.md 文件格式

```markdown
---
name: code-review
description: 代码审查专家——发现代码中的 Bug、安全漏洞和性能问题。当需要审查代码、检查安全漏洞、评估代码质量时加载。
---

## 你的角色

你是一名资深代码审查工程师（10 年以上经验），专注于发现以下类型问题：
1. **安全漏洞**：SQL 注入、XSS、命令注入、路径穿越、硬编码密码
2. **逻辑错误**：边界条件处理、空指针、并发问题
3. **性能问题**：N+1 查询、不必要的同步阻塞、内存泄漏

## 审查流程

1. 先用 read_file 读取要审查的文件，理解整体结构
2. 用 search_code 查找函数的调用方，了解使用上下文
3. 逐行分析，按严重程度分级：
   - **Critical**：必须修复（安全漏洞、数据丢失风险）
   - **Warning**：建议修复（逻辑错误、性能问题）
   - **Info**：可选改进（代码风格、可读性）

## 输出格式

每个问题用以下格式：
[级别] 行号（如有）
问题描述
建议修复方案

## 特别注意

- SQL 操作必须使用参数化查询，绝不拼接字符串
- 用户输入必须验证和清洁，不能直接用于系统调用
```

> **frontmatter 只需要 `name` 和 `description` 两个字段。**
> 旧版本还有一个 `triggers`（关键词列表），是给 TF-IDF/关键词匹配用的——现在触发判断交给
> LLM 的语义理解，**不再需要 `triggers`**。`description` 要写清两件事：**这个 Skill 是干什么的**、
> **什么时候该加载它**（触发场景）。因为 `description` 会进入索引表和 `get_skill_guide` 的工具描述，
> 是 LLM 判断"要不要加载"的唯一依据，务必写得具体、可区分。

---

## 9.3 Skill 加载器 `skills/loader.py`

```python
# skills/loader.py
"""
Skills 加载器：读取 SKILL.md 文件，解析 frontmatter 和内容。
"""
import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str      # 一句话说明"是什么 + 何时加载"——进索引表和工具描述
    content: str          # Skill 的主体内容（被 get_skill_guide 按需读取的全文）
    path: str


def load_skills(skills_dir: str = "skills/") -> list[Skill]:
    """扫描目录，加载所有 SKILL.md 文件。"""
    skills = []
    skills_path = Path(skills_dir)

    if not skills_path.exists():
        return []

    for path in skills_path.glob("*.md"):
        try:
            skill = _parse_skill_file(path)
            if skill:
                skills.append(skill)
        except Exception as e:
            print(f"[Skills] 加载 {path.name} 失败：{e}")

    print(f"[Skills] 已加载 {len(skills)} 个 Skill")
    return skills


def _parse_skill_file(path: Path) -> Skill | None:
    """解析单个 SKILL.md 文件（frontmatter + 正文）。"""
    content = path.read_text(encoding="utf-8")

    # 解析 YAML frontmatter（--- 之间的部分）
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not frontmatter_match:
        return None

    frontmatter_text = frontmatter_match.group(1)
    body = content[frontmatter_match.end():]

    # 简单解析 YAML（不用 PyYAML 避免额外依赖）。
    # 现在 frontmatter 只有 name / description 两个标量键，不再有 triggers 列表，
    # 所以解析逻辑简化为纯键值对。
    meta = {}
    for line in frontmatter_text.split("\n"):
        if ": " in line:
            key, _, value = line.partition(": ")
            meta[key.strip()] = value.strip()

    return Skill(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        content=body.strip(),
        path=str(path),
    )


def build_skill_index(skills: list[Skill]) -> str:
    """把所有 Skill 渲染成一张「技能索引表」（只含 name + description），供常驻 system prompt。

    这是"按需加载"机制的第一层：LLM 靠这张表知道"有哪些技能、各自何时用"，
    但**不加载任何全文**——全文由 LLM 自主调用 get_skill_guide(name) 时才读取（第二层）。
    索引表只有几行，token 开销极小，和把 Skill 全文预注入 system prompt 是本质区别。
    """
    if not skills:
        return ""

    rows = "\n".join(f"| `{s.name}` | {s.description} |" for s in skills)
    return (
        "## 可用技能（Skills）\n\n"
        "以下技能提供各领域的团队规范和操作指南。"
        "**当任务匹配某个技能的适用场景时，先调用 `get_skill_guide(skill_name)` 工具"
        "获取它的完整指南，再据此执行**；任务用不到任何技能时，不必调用。\n\n"
        "| skill_name | 适用场景 |\n"
        "|------------|----------|\n"
        f"{rows}"
    )
```

> **索引表长什么样**：`build_skill_index()` 输出的就是一张 Markdown 表格，例如
>
> ```
> | skill_name | 适用场景 |
> |------------|----------|
> | `code-review` | 代码审查专家……当需要审查代码、检查安全漏洞时加载。 |
> | `async-patterns` | 本项目的 asyncio 规范……审查/修改异步代码时加载。 |
> ```
>
> LLM 读到这张表，就知道"有 code-review 这个技能、什么时候该用它"，但此刻还没加载任何全文。

---

## 9.4 按需加载工具 `skills/skill_tool.py`

这一节把旧版本的"TF-IDF 搜索 + 预注入"整个替换掉。不再有 `searcher.py`（连同它依赖的 `jieba`），
取而代之的是一个标准工具 `GetSkillGuideTool`——它继承第 5 章的 `BaseTool`，注册进 `ToolRegistry`，
由 LLM 在 Agentic Loop 里**自主调用**。

```python
# skills/skill_tool.py
"""
get_skill_guide 工具：LLM 按需加载某个 Skill 的完整指南。

工作方式（对齐 DDH-main-agent 的 get_skill_guide）：
  1. system prompt 里常驻 build_skill_index() 生成的技能索引表（只有 name + description）；
  2. LLM 判断任务需要某技能 → 发起工具调用 get_skill_guide(skill_name="code-review")；
  3. 本工具读取 skills/code-review.md 全文，作为 tool_result 返回；
  4. LLM 带着完整指南继续工作。

这样每个 Skill 的全文只在"真正需要时"进入上下文，且由模型自己决定，而不是每次无条件预注入。
"""
from tools.base import BaseTool
from skills.loader import Skill, load_skills


class GetSkillGuideTool(BaseTool):

    def __init__(self, skills: list[Skill] | None = None, skills_dir: str = "skills/"):
        # 进程内缓存已加载的 Skill（name → Skill），避免每次调用都重扫磁盘。
        self._skills = skills if skills is not None else load_skills(skills_dir)
        self._by_name = {s.name: s for s in self._skills}

    @property
    def name(self) -> str:
        return "get_skill_guide"

    @property
    def description(self) -> str:
        # 把可用技能内嵌进工具描述——这样即使不看 system prompt 的索引表，
        # LLM 在决定调用本工具时也能从参数说明里看到有哪些合法的 skill_name。
        available = "、".join(f"{s.name}（{s.description}）" for s in self._skills)
        return (
            "按需获取某个子技能的完整操作指南。当任务匹配某技能的适用场景时，"
            "先调用本工具拿到详细规范，再据此执行。\n"
            f"可用技能：{available}"
        )

    @property
    def input_schema(self) -> dict:
        valid_names = [s.name for s in self._skills]
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": f"要加载的技能名称，必须是以下之一：{', '.join(valid_names)}",
                }
            },
            "required": ["skill_name"],
        }

    async def execute(self, inputs: dict) -> str:
        skill_name = (inputs.get("skill_name") or "").strip()
        skill = self._by_name.get(skill_name)

        if skill is None:
            # 找不到时不报错，而是返回可用列表，帮 LLM 纠正到合法的 skill_name。
            available = ", ".join(self._by_name.keys())
            return f"未找到技能 '{skill_name}'。可用技能：{available}"

        # 返回 SKILL.md 全文，供 LLM 遵循其中的流程、规范和输出格式。
        return f"【技能指南 - {skill.name}】\n\n{skill.content}"
```

> **和 DDH 的差异**：DDH 的 skill 是 `skill/<name>/SKILL.md` 的目录结构，还支持"大技能下的子技能"；
> 本项目的 skill 是扁平的 `skills/<name>.md`（见 9.6），所以 `execute` 只需按 `name` 直接查缓存即可，
> 不用像 DDH 那样做多级路径查找。机制内核是一样的：**索引常驻 + 全文按需加载**。

---

## 9.5 在两套架构的子 Agent 中集成按需加载

本项目有两套并行的架构，各自都有子 Agent 需要集成 Skill：

| 架构 | 入口 | 子 Agent 位置 | 基类 |
|------|------|---------------|------|
| Coordinator | `POST /ask` | `sub_agents/*.py` | `sub_agents/base.py → SubAgent` |
| Swarm | `POST /swarm/ask` | `swarm/*_agent.py` | `swarm/agent_base.py → SwarmAgent` |

两套子 Agent 有相同的痛点：固定的 system prompt 无法覆盖所有领域。例如：

- Reviewer 审查的代码可能涉及数据库、异步、API 等不同领域
- Debugger 修复的 Bug 可能需要参照不同的错误处理规范
- TestWriter 针对不同模块需要遵循不同的测试策略

### 集成方案：索引表常驻 + get_skill_guide 工具

不再有"搜索并拼接全文"的 `enhancer.py`。集成只做两件事，对两套架构完全一致：

1. **把技能索引表拼到 system prompt 末尾**：用 9.3 的 `build_skill_index()` 生成，进程启动时构建一次。
2. **把 `GetSkillGuideTool` 加进子 Agent 的工具列表**：这样 LLM 在 Agentic Loop 里能自主调用它。

先做一个模块级的公共入口，避免每个子 Agent 各自重复加载 Skill：

```python
# skills/__init__.py（或新建 skills/registry.py，二选一，这里用 __init__.py）
"""
Skill 系统的公共入口：
  - SKILL_INDEX：技能索引表文本，拼到各子 Agent 的 system prompt 末尾（常驻，极短）
  - get_skill_guide_tool()：返回共享的 GetSkillGuideTool 实例，加进子 Agent 的 tools
两套架构（SubAgent / SwarmAgent）都从这里取，保证索引和工具口径一致。
"""
from skills.loader import load_skills, build_skill_index
from skills.skill_tool import GetSkillGuideTool

# 进程内只加载一次：所有子 Agent 共享同一份 Skill 列表、索引表和工具实例
_SKILLS = load_skills("skills/")
SKILL_INDEX = build_skill_index(_SKILLS)
_SKILL_TOOL = GetSkillGuideTool(skills=_SKILLS)


def get_skill_guide_tool() -> GetSkillGuideTool:
    return _SKILL_TOOL
```

---

### Coordinator 架构集成：修改 `sub_agents/base.py`

`SubAgent.run()` 本来就跑 `run_agent_loop`（第 6 章），改动很小：**system 末尾拼索引表**，
**tools 里恒定加入 `get_skill_guide`**。

```python
# sub_agents/base.py 的 run() 方法中

from skills import SKILL_INDEX, get_skill_guide_tool

class SubAgent(ABC):
    # ...name、system_prompt、tools 属性不变...

    async def run(self, task: str, context: dict | None = None,
                  session_id: str | None = None) -> tuple[str, Usage]:
        full_task = task
        if context:
            context_str = "\n".join(f"【{k}的结果】\n{v}" for k, v in context.items())
            full_task = f"{task}\n\n参考信息（来自前置任务）：\n{context_str}"

        # ★ 改动 1：system prompt 末尾拼上常驻的技能索引表（只有 name + description，很短）。
        # 不再做 TF-IDF 搜索、也不预注入任何 Skill 全文——加载与否由 LLM 自己在循环里决定。
        system = f"{self.system_prompt}\n\n{SKILL_INDEX}" if SKILL_INDEX else self.system_prompt

        provider = get_provider()

        # ★ 改动 2：把 get_skill_guide 工具恒定加入注册表（和子类自己的 tools 合并）。
        # 这样每个子 Agent 都能按需加载 Skill；registry 恒非空，executor 也恒有效。
        registry = ToolRegistry()
        registry.register(get_skill_guide_tool())
        for tool in self.tools:
            registry.register(tool)
        executor = ToolExecutor(registry)

        result = await run_agent_loop(
            prompt=full_task,
            provider=provider,
            system=system,                              # ← 带索引表的 system
            tools=registry.get_all_definitions(),       # ← 恒含 get_skill_guide
            executor=executor,
            max_turns=10,
            session_id=session_id,
        )
        return result.text, result.total_usage
```

**效果**：当用户通过 `/ask` 问"帮我写一个 Redis 缓存的 API 接口"，Coordinator
规划为 `code_writer` 任务。`code_writer` 在循环里看到索引表里有 `api-design`、`env-config`，
自主调用 `get_skill_guide("api-design")`、`get_skill_guide("env-config")` 加载全文，
再遵循项目的路由命名规则和配置管理流程写代码。**用不到 Skill 的任务，它就不会去调这个工具。**

---

### Swarm 架构集成：让 `handle()` 也走 Agentic Loop

Swarm 子 Agent 原来是**单次 `provider.chat()`**——单次调用里 LLM 没有"先调工具再继续"的机会，
按需加载无从谈起。所以这里把 `handle()` 改成走 `run_agent_loop`，和 Coordinator 用同一套循环。

`swarm/agent_base.py` 删掉 `_enhance_system`，改提供一个组装 system + tools 的便捷入口：

```python
# swarm/agent_base.py

from skills import SKILL_INDEX, get_skill_guide_tool
from agent_core.loop import run_agent_loop
from agent_core.executor import ToolExecutor
from tools.registry import ToolRegistry
from providers.router import get_provider

class SwarmAgent(ABC):
    # ...原有 __init__、task_types、handle 等抽象接口不变...

    async def _run_with_skills(self, base_system: str, prompt: str,
                               extra_tools: list | None = None) -> str:
        """公共执行入口：system 拼索引表 + tools 含 get_skill_guide，走 Agentic Loop。

        取代旧的 _enhance_system（TF-IDF 预注入）。Swarm 子 Agent 由此获得
        和 Coordinator 一致的"按需加载 Skill"能力。
        """
        system = f"{base_system}\n\n{SKILL_INDEX}" if SKILL_INDEX else base_system

        registry = ToolRegistry()
        registry.register(get_skill_guide_tool())
        for tool in (extra_tools or []):
            registry.register(tool)

        result = await run_agent_loop(
            prompt=prompt,
            provider=get_provider(),
            system=system,
            tools=registry.get_all_definitions(),
            executor=ToolExecutor(registry),
            max_turns=10,
        )
        return result.text
```

### Swarm 子 Agent 调用示例（以 ReviewerAgent 为例）

```python
# swarm/reviewer_agent.py 的 handle() 方法中

async def handle(self, task) -> str:
    payload = task.payload
    code = payload.get("code", "")
    filename = payload.get("file", "unknown.py")
    focus = payload.get("focus", "全面审查")

    base_system = """
你是一名资深代码审查工程师。
审查维度：SQL 注入、命令注入、硬编码密码、逻辑错误、边界条件、性能问题。
每个问题输出：[Critical/Warning/Suggestion] 行号 - 问题描述 - 建议修复。
发现 Critical 级别问题时，最后一行输出 NEEDS_FIX:true，否则输出 NEEDS_FIX:false。
"""
    prompt = f"文件：{filename}\n重点关注：{focus}\n\n```python\n{code}\n```"

    # ★ 改动：从单次 provider.chat() 改为走 Agentic Loop。
    # LLM 审查时若发现代码涉及 asyncio、异常处理等，会自主 get_skill_guide 加载对应规范再审。
    result = await self._run_with_skills(base_system, prompt)

    # 后续解析逻辑不变：从返回文本里判断 NEEDS_FIX，决定是否派生 debug 任务。
    if "NEEDS_FIX:true" in result:
        await self.blackboard.post_derived(...)
    return result
```

> **注意返回值来源变了**：旧代码从 `response.content` 逐块拼 `result`；现在 `run_agent_loop`
> 直接返回 `LoopResult.text`，`result` 就是最终文本，`NEEDS_FIX:true` 的判断照样成立。

---

### 整体流程图

```
用户请求
    │
    ├─ POST /ask（Coordinator 架构）
    │       ↓
    │   planner.py → 规划子任务 → dispatcher.py → 分发给 sub_agents
    │       ↓
    │   SubAgent.run()
    │       ├── system = self.system_prompt + SKILL_INDEX     # 常驻索引表（极短）
    │       ├── tools  = [get_skill_guide] + self.tools       # 按需加载工具
    │       └── run_agent_loop(...)
    │              └─（循环内）LLM 判断需要 → get_skill_guide("api-design") → 拿到全文继续
    │
    └─ POST /swarm/ask（Swarm 架构）
            ↓
        Blackboard.post() → SwarmAgent.start() → 认领任务 → handle()
            └── _run_with_skills(base_system, prompt)
                   ├── system = base_system + SKILL_INDEX
                   ├── tools  = [get_skill_guide] + extra_tools
                   └── run_agent_loop(...)
                          └─（循环内）LLM 按需 get_skill_guide(...) 加载规范
```

**两套架构共用 `skills/` 目录下的同一组 Skill 文件、同一张索引表、同一个 `get_skill_guide` 工具**，
加载机制完全统一：**system 常驻索引表，LLM 在 Agentic Loop 里自主决定何时加载哪个 Skill 的全文。**

---

## 9.6 团队规范 Skill 文件

本节的 Skill 不是定义"Agent 是什么"（那是 system prompt 的事），而是定义
**"团队怎么做事"**——编码规范、流程约定、架构决策。它们通过 9.5 的机制被子 Agent
在工作时按需加载。

每个 Skill 文件放在 `skills/` 目录下，被 `load_skills()` 扫描加载，其 `name + description`
进入常驻的技能索引表。当子 Agent 处理任务时，LLM 参照索引表**自主决定**是否调用
`get_skill_guide(skill_name)` 加载对应全文。

**加载时机举例（由 LLM 判断）**：
- Reviewer 审查含 `asyncio` 的代码 → 自主 `get_skill_guide("async-patterns")`，按规范检查 Event 竞态
- Debugger 修复 Redis 连接问题 → 自主 `get_skill_guide("error-handling")`，按规范输出降级策略
- TestWriter 为 API 端点写测试 → 自主 `get_skill_guide("api-design")`，知道该测哪些状态码

> **每个 Skill 的 frontmatter 只有 `name` 和 `description`（无 `triggers`）**。下面各文件的
> `description` 都同时说清"是什么 + 何时加载"，因为它是 LLM 判断要不要加载的唯一依据。

```bash
mkdir -p skills
```

---

**`skills/error-handling.md`**：

> **加载时机**：审查/修改含 try/except 的代码、修复异常处理相关 Bug 时。
> 本 Skill 内容直接对应项目中 `periodic_save()` 的降级设计和 `sandbox.py` 的自定义异常。

````markdown
---
name: error-handling
description: 本项目的异常处理规范——降级策略、日志格式、自定义异常。审查或编写涉及 try/except、错误处理、降级逻辑的代码时加载。
---

## 分层捕获原则

本项目的分层结构决定了异常在哪里捕获：

| 层 | 文件 | 策略 |
|----|------|------|
| HTTP 接口层 | `main.py` | 捕获 → 转为 HTTPException（404/409/422） |
| Swarm 层 | `swarm/*.py` | 不捕获，让 `agent_base.py` 的 `start()` 统一 `fail()` |
| 工具层 | `sandbox.py` | 抛出明确异常（`PathTraversalError`） |
| 外部依赖 | Redis/LLM API | 捕获 → 降级 + 单次日志 |

## 降级模式（已验证有效）

参考 `main.py` 中 `periodic_save()` 的实现：

```python
fail_count = 0
while True:
    try:
        await blackboard.save_to_redis(redis)
        fail_count = 0
    except Exception as e:
        fail_count += 1
        if fail_count == 1:
            print(f"[Save] Redis 保存失败：{e}")  # 只打一次
        if fail_count >= 3:
            await asyncio.sleep(60)  # 连续失败则降速
            continue
    await asyncio.sleep(5)
```

核心：**不刷屏、不崩溃、自动降速**。

## 日志格式

```python
# 正确：模块名 + 操作 + 原因 + 上下文 ID
print(f"[Blackboard] 保存到 Redis 失败（降级为纯内存模式）：{e}")
print(f"[{self.agent_id}] 任务 {task.id} 处理失败：{e}")

# 错误：
print("出错了")              # 没有上下文
print(f"Error: {e}")         # 不知道是哪个模块
```

## 自定义异常

- 继承 `ValueError`：用户输入不合法 → `PathTraversalError`
- 继承 `RuntimeError`：运行时状态异常 → `TaskTypeUnknownError`
- 类名必须以 `Error` 结尾
````

---

**`skills/async-patterns.md`**：

> **加载时机**：审查或修改含 `asyncio`/`async`/`await` 的代码时。
> 本 Skill 内容直接对应项目中 Blackboard 的 Event 竞态修复、`periodic_save()` 的后台任务模式。

````markdown
---
name: async-patterns
description: 本项目的 asyncio 规范——白板并发、后台任务、已踩过的坑。审查或编写涉及 async/await/asyncio、并发协程、Lock/Event/Condition 的代码时加载。
---

## 本项目的异步架构

```
main.py (FastAPI + uvicorn 事件循环)
  ├── lifespan()
  │     ├── asyncio.create_task(periodic_save())   ← 后台常驻
  │     └── asyncio.create_task(agent.start())     ← SwarmAgent 常驻循环
  └── HTTP 处理函数（与 Agent 共用同一事件循环）
```

## 后台常驻任务模板

```python
# 参考 main.py 的 periodic_save()
async def background_loop():
    while True:
        try:
            await do_work()
        except Exception as e:
            print(f"[Loop] 错误：{e}")
        await asyncio.sleep(interval)

# 启动
_task = asyncio.create_task(background_loop())
# 关闭
_task.cancel()
await asyncio.gather(_task, return_exceptions=True)
```

## 白板并发的关键约束

1. **claim() 必须在 Lock 内完成读+写**：从"找到 pending 任务"到"标记为 claimed"
   是原子操作，否则两个 Agent 会同时认领同一个任务。

2. **通知新任务用 Condition，不用 Event**：
   ```python
   # ✗ 有竞态（本项目踩过的坑）
   self._event.set()
   self._event.clear()   # 如果 Agent 在 set 和 clear 之间还没醒来，通知丢失

   # ✓ 正确做法
   async with self._condition:
       self._condition.notify_all()
   ```

3. **不要在 Lock 内做 I/O**：
   ```python
   # ✗ 持锁时间过长
   async with self._lock:
       data = await redis.get(...)    # 网络 I/O 阻塞其他协程获取锁

   # ✓ 先读再锁
   data = await redis.get(...)
   async with self._lock:
       self._tasks[tid] = Task(**data)
   ```

## asyncio.sleep vs time.sleep

- `await asyncio.sleep(5)` → 让出控制权，其他协程可以运行
- `time.sleep(5)` → 阻塞整个事件循环，所有 Agent 都卡住 5 秒

本项目全部使用 `asyncio.sleep`，任何出现 `time.sleep` 的代码都是 Bug。
````

---

**`skills/api-design.md`**：

> **加载时机**：审查或生成 FastAPI 端点代码时。
> 本 Skill 内容直接对应 `main.py` 中已实现的 `/swarm/tasks` 系列端点。

````markdown
---
name: api-design
description: 本项目 FastAPI 端点设计规范——路由、状态码、响应模型。审查或编写 REST 接口、HTTP 端点、路由时加载。
---

## 已有端点参考（main.py）

| 方法 | 路由 | 用途 |
|------|------|------|
| POST | `/ask` | 通用对话入口 |
| POST | `/swarm/ask` | 发布 Swarm 任务 |
| GET | `/swarm/tasks/{task_id}` | 按 ID 查任务状态 |
| GET | `/swarm/tasks` | 任务列表摘要 |
| POST | `/swarm/tasks/{task_id}/apply` | 将已完成任务的代码写入文件 |

## 路由命名规则

- 资源名词复数：`/tasks`、`/sessions`
- 层级嵌套表示从属：`/swarm/tasks/{id}/apply`
- 操作用 HTTP 方法，不用动词路由（`/getTask` ✗）

## 状态码使用（本项目已有的模式）

```python
# 404 - 资源不存在
if not task:
    raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

# 409 - 状态冲突
if task.status != "done":
    raise HTTPException(status_code=409, detail=f"任务状态为 {task.status}，只有 done 状态可以 apply")
```

## 响应模型（Pydantic）

每个端点必须有明确的 Response 模型：

```python
class SwarmAskResponse(BaseModel):
    task_id: str
    status: str
    result: str | None = None
    derived_task_ids: list[str] = []
```

禁止直接返回 `dict`——Pydantic 模型提供自动校验和 OpenAPI 文档。

## 新增端点的清单

1. 在 `main.py` 定义 Request/Response 模型
2. 实现端点函数
3. 在 `static/index.html` 加对应的前端调用
4. 手动用 curl 或浏览器测试一遍
````

---

**`skills/env-config.md`**：

> **加载时机**：涉及 `.env`、`Settings`、新增配置项时。
> 本 Skill 直接对应项目中 `core/config.py` 的 Pydantic Settings 机制和之前踩过的"extra fields forbidden"坑。

````markdown
---
name: env-config
description: 本项目配置管理规范——.env + Pydantic Settings 联动流程。涉及环境变量、配置项、Settings 类、新增 .env 字段时加载。
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
````

---

**`skills/prompt-engineering.md`**：

> **加载时机**：修改子 Agent 的 system prompt 或新增 Agent 时。
> 本 Skill 直接对应 `reviewer_agent.py` 和 `debugger_agent.py` 中 system prompt 的结构。

````markdown
---
name: prompt-engineering
description: 本项目 Agent 提示词规范——结构模板和输出解析约定。修改子 Agent 的 system prompt、新增 Agent、设计 NEEDS_FIX 之类输出标记时加载。
---

## 本项目 system prompt 结构

每个子 Agent 的 system prompt 必须包含：

```
1. 角色定位（一句话）
2. 工作流程（编号步骤）
3. 输出格式（精确模板，含解析标记）
4. 约束（禁止项）
```

示例（reviewer_agent.py 的实际 system prompt）：
```
你是一名资深代码审查工程师。
审查维度：SQL 注入、命令注入、硬编码密码、逻辑错误、边界条件、性能问题。
每个问题输出：[Critical/Warning/Suggestion] 行号 - 问题描述 - 建议修复。
发现 Critical 级别问题时，最后一行输出 NEEDS_FIX:true，否则输出 NEEDS_FIX:false。
```

## 输出解析约定

本项目通过**约定格式标记**来让代码提取 LLM 的结构化输出：

| 标记 | 用途 | 解析方 |
|------|------|--------|
| `NEEDS_FIX:true/false` | Reviewer 决定是否派生 debug 任务 | `reviewer_agent.py` 用 `in` 判断 |
| ` ```python ... ``` ` | 代码块 | `code_extractor.py` 用正则提取 |
| `Bug 根因：xxx` | Debugger 的诊断结论 | 人读 / 前端展示 |

## 关键原则

1. **用"必须"/"禁止"**，不用"建议"/"尽量"——模糊措辞让 LLM 自行发挥
2. **输出格式写死**——方便下游代码用字符串匹配 / 正则提取
3. **一个 Agent 只干一件事**——不要让 reviewer 又审查又修复

## 新增 Agent 时的检查清单

- [ ] system prompt 有没有明确输出格式？
- [ ] 下游代码能不能稳定解析这个输出？
- [ ] 异常情况（LLM 不遵守格式）有没有兜底？
````

---

**`skills/security.md`**：

> **加载时机**：审查涉及文件操作、用户输入、路径拼接的代码时。
> 本 Skill 直接对应 `sandbox.py` 的路径遍历防护设计。

````markdown
---
name: security
description: 本项目安全规范——路径遍历防护、输入校验、沙箱写入。审查涉及文件操作、用户/LLM 输入、路径拼接、注入风险的代码时加载。
---

## 路径遍历防护（已实现）

本项目通过 `sandbox.py` 的 `resolve_safe_path()` 防止 LLM 输出的文件路径逃出
工作目录：

```python
def resolve_safe_path(workspace: Path, relative: str) -> Path:
    target = (workspace / relative).resolve()
    if not str(target).startswith(str(workspace.resolve())):
        raise PathTraversalError(f"路径 {relative} 逃出了工作区")
    return target
```

**核心逻辑**：`.resolve()` 会把 `../../../etc/passwd` 解析为绝对路径，然后
`startswith` 检查是否还在 workspace 下。

## 必须使用沙箱的场景

| 场景 | 正确做法 | 错误做法 |
|------|----------|----------|
| apply 端点写入代码 | `write_text_sandboxed(workspace, path, code)` | `open(path, 'w').write(code)` |
| 任何用户/LLM 提供的路径 | 先过 `resolve_safe_path()` | 直接拼接使用 |

## 输入校验原则

- LLM 的输出视为**不可信输入**（它可能输出 `../../../../etc/crontab`）
- 用户通过 API 传入的 `file_path` 也是不可信的
- 只有代码里硬编码的路径（如 `skills/` 目录）才可信

## 禁止事项

- 禁止 `os.system()`、`subprocess.run(shell=True)` + 用户输入
- 禁止 `eval()`、`exec()` 执行 LLM 输出
- 禁止把密钥放在代码里或 Git 历史里
````

---

## 9.7 本章检查清单

```
□ skills/ 目录有 5+ 个 .md 文件，格式正确（frontmatter 只含 name、description，无 triggers）

□ 不再依赖 jieba（TF-IDF 已移除，0.2 依赖清单里不应再有 jieba）

□ load_skills() 能正确解析所有 Skill 文件，打印加载数量

□ build_skill_index() 生成的技能索引表只含 name + description，被拼进各子 Agent 的 system prompt

□ 子 Agent 的工具列表里恒定包含 get_skill_guide（用 registry.list_names() 或日志验证）

□ get_skill_guide("async-patterns") 返回该 Skill 全文；传入不存在的 skill_name 返回可用列表而不崩溃

□ SubAgent 与 SwarmAgent 都走 run_agent_loop（Swarm 不再是单次 provider.chat）

□ 提一个涉及 asyncio 的审查任务，日志显示 LLM 自主调用了 get_skill_guide("async-patterns")

□ 提一个用不到任何规范的简单任务，LLM 不调用 get_skill_guide（验证"按需"而非"每次预注入"）
```

**全部打勾之后，进入第 10 章。**

---

# 第 10 章：阶段 10 —— 会话持久化

> **本章目标**：让 Agent 记住历史对话，重启后能从上次中断的地方继续。
> 用 JSONL 文件作为主存储，Redis 缓存最近消息内容加速加载，支持断点续传。

---

## 10.1 持久化架构

JSONL 文件是唯一的"事实来源"（source of truth），永远全量、永久保存。Redis 只缓存**最近一段窗口内的消息内容本身**（用 List 类型），是可丢弃、可重建的加速层——丢了大不了退回读文件，不影响正确性。

写入路径：

```
用户发送消息
    ↓
Agent 处理（Agentic Loop）
    ↓
把这次对话追加写入 JSONL 文件（顺序写，高效，永久保存）
    ↓ 同时（尽力而为，失败不影响主流程）
把这条 message 记录 RPUSH 进 Redis List（session_msgs:{session_id}）
并 LTRIM 只保留最近 N 轮，设置过期时间
```

读取路径（下次用户发消息，需要加载历史时）：

```
优先查 Redis List（session_msgs:{session_id}）
    ├─ 命中（List 非空 且 请求轮数在缓存窗口内）
    │     → 直接从 Redis 内存里取，无需碰磁盘，速度快
    │
    └─ 未命中（Redis 未启用 / key 已过期 / 请求轮数超出缓存窗口）
          → 退回读取 JSONL 文件（慢但一定拿得到完整历史）
          → 顺手把读到的最近 N 轮回填进 Redis List（下次命中）
```

> 注意：之前版本里 Redis 只存了 `session_id → 文件路径 + 最后一条预览`（Hash），这其实**加速不了任何东西**——因为文件路径本来就能由 `session_id` 直接算出来（见 10.3 的 `_session_path`），查 Redis 反而多了一次网络往返。真正想让 Redis 起到加速作用，缓存的必须是**消息内容本身**，这样加载历史时才能跳过磁盘 I/O 和 JSONL 逐行解析。

---

## 10.2 JSONL 会话格式

JSONL（JSON Lines）格式：每行是一个独立的 JSON 对象，追加写入，无需读取整个文件。

```jsonl
{"type": "message", "ts": 1719619200, "session_id": "abc", "role": "user",      "content": "帮我审查 auth.py"}
{"type": "message", "ts": 1719619205, "session_id": "abc", "role": "assistant",  "content": "发现以下问题：..."}
{"type": "tool_call",   "ts": 1719619203, "session_id": "abc", "tool": "read_file", "input": {"path": "auth.py"}}
{"type": "tool_result", "ts": 1719619204, "session_id": "abc", "tool": "read_file", "output": "def login..."}
{"type": "checkpoint",  "ts": 1719619206, "session_id": "abc", "turn": 2}
```

每种记录类型：
- `message`：用户消息或 Agent 回复（可直接重建 messages 列表）
- `tool_call` / `tool_result`：工具调用记录（用于审计，不用于恢复）
- `checkpoint`：检查点（记录到这里时已完成几轮，用于断点续传）

---

## 10.3 会话存储 `persistence/session_store.py`

```python
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

        # 3. 修补"孤儿 user 消息"：如果上一次调用在写完 user、还没写 assistant
        #    时中途崩溃（比如 LLM 调用抛异常），文件/缓存会以一条没人回答的
        #    user 记录结尾。留着它的话，本次会在它后面再拼一条新 user 消息，
        #    形成连续两条 role="user"，违反 Anthropic API 的角色交替要求，
        #    下一次请求会直接报错。这条记录反正没被回答过，丢弃它不影响历史完整性。
        if raw_messages and raw_messages[-1].get("role") == "user":
            raw_messages = raw_messages[:-1]

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
```

> **为什么 `load_messages` 要专门丢弃末尾的孤儿 user 消息？** `append_message("user", ...)`
> 和 `append_message("assistant", ...)` 是两次独立的写入（中间隔着一次完整的 Agentic
> Loop 调用）。如果 Loop 在这中间抛异常（比如 LLM 调用超时/报错），JSONL 文件就会停在
> 一条没人回答的 user 记录上。如果不处理，下次 `ask()` 会在它后面再拼一条新的 user
> 消息，历史里出现连续两条 `role="user"`，直接违反 Anthropic API"必须 user/assistant
> 交替"的硬性要求，那次请求会报错而不是"记忆稍微少了一点"。丢弃这条未完成的记录，
> 是让历史"要么完整一轮、要么不存在"，代价是这条消息的内容确实会丢——但反正它也没被
> 回答过，保留半条记录没有意义。

---

## 10.4 在 Agent 中使用会话持久化

> 本章的 `SessionStore` 要接到项目实际的 `agent/agent.py` 里（第 4 章之后 `agent.py` 已经拆成了
> `agent/` 包，对外接口实现在 `agent/agent.py`，`agent/__init__.py` 只做导出），
> 而不是新建一个独立的 `agent.py`。这里复用 `ask()` 已有的 `_registry`（工具注册）、
> `_executor`（工具执行器）、`SYSTEM_PROMPT`，只是多了"加载历史 / 存历史"两步。

先给 `run_agent_loop` 补上 `initial_messages` 参数（对应第 4 章遗留的 TODO：调用方需要能传入
包含历史的完整消息列表，而不是永远从 `prompt` 现造一条消息）：

```python
# agent/loop.py（run_agent_loop 签名变化）

async def run_agent_loop(
    prompt: str,
    provider: BaseProvider,
    system: str = "",
    tools: list[ToolDefinition] | None = None,
    executor: ToolExecutor | None = None,
    max_turns: int = 10,
    max_tokens: int = 4096,
    on_text_delta: Callable[[str], None] | None = None,
    initial_messages: list[Message] | None = None,   # 新增
) -> LoopResult:
    # 优先用调用方传入的完整历史（含本轮新问题）；没传则退回单条用户消息
    starting_messages = initial_messages if initial_messages is not None else [
        Message(role="user", content=[TextBlock(text=prompt)])
    ]
    state = LoopState(messages=tuple(starting_messages))
    ...
```

> **为什么不单独写一个 `ask_with_memory()`？** 它跟 `ask()` 除了"要不要读写历史"之外，
> 拿 provider、传 tools/executor、跑 loop、组装 `AskResult` 全部一样——两个函数并存只会
> 变成重复代码，以后改一处很容易忘了改另一处。更好的做法是给 `ask()` 加一个可选的
> `session_id`：不传（默认 `None`）就是原来的无记忆行为，完全兼容现有调用方
> （`cli.py`、测试）；传了就自动读历史、拼历史、存历史。

给 `agent/agent.py` 里已有的 `ask()` 加上 `session_id` 参数：

```python
# agent/agent.py（在文件顶部追加 import，并替换 ask()）

from providers.types import Message, TextBlock
from persistence.session_store import SessionStore
from persistence.redis_client import create_redis_client

# 会话存储：JSONL 落盘 + Redis 缓存最近历史。
# redis_client 复用 persistence/redis_client.py 里的同一套连接配置（Blackboard 也是这么接的），
# 不要各自硬编码 host/port。
_store = SessionStore(base_dir="sessions/", redis_client=create_redis_client())


async def ask(question: str, session_id: str | None = None) -> AskResult:
    """
    调用 Agent 回答问题。

    session_id 为 None（默认）时无记忆，每次调用互不影响；
    传入 session_id 时会从 SessionStore 加载历史（最近 10 轮）、
    拼到本次问题前面一起跑，并把这轮问答追加写回 SessionStore。
    """
    provider = get_provider()

    initial_messages = None
    if session_id is not None:
        history = await _store.load_messages(session_id, max_turns=10)
        new_user_message = Message(role="user", content=[TextBlock(text=question)])
        initial_messages = history + [new_user_message]
        await _store.append_message(session_id, "user", question)

    result = await run_agent_loop(
        prompt=question,
        provider=provider,
        system=SYSTEM_PROMPT,
        tools=_registry.get_all_definitions(),
        executor=_executor,
        max_turns=10,
        initial_messages=initial_messages,
    )

    if session_id is not None:
        await _store.append_message(session_id, "assistant", result.text)

    return AskResult(
        text=result.text,
        input_tokens=result.total_usage.input_tokens,
        output_tokens=result.total_usage.output_tokens,
        turn_count=result.turn_count,
    )
```

> **`ask_stream()` / `clear_session()` 不在这里**：早期版本曾经把这两个函数也放在
> `agent/agent.py`，跟 `ask()` 抄同一套"加载历史 → 拼新问题 → 跑 Loop → 存回历史"逻辑。
> 后来发现这样会导致 `/ask`（走 Coordinator）和 `/ask/stream`/`/session/clear`
> （走单 Agent 直接对话）用的是两套不同的会话读写实现，行为容易走偏（比如流式接口
> 里"审查+修复+写测试"这类复合请求不会被拆成子任务）。所以 `ask_stream()` 和
> `clear_session()` 挪到了 10.4b 节，跟 `/ask` 用的是同一个 `CoordinatorAgent`。
> `agent/agent.py` 里只保留 `ask()`（无 Coordinator 规划能力的单 Agent 直接对话，
> 供 `cli.py` 和基础测试用）。

---

## 10.4b 在 Coordinator 中实现流式与清除会话 `coordinator/agent.py`

`/ask` 走的是 `CoordinatorAgent`（第 6 章），`/ask/stream` 和 `/session/clear` 也应该
落在同一套架构上，而不是像早期版本那样绕回 `agent/agent.py` 的单 Agent 实现——否则
"帮我审查这段代码顺便把测试也写一下"这种应该拆成 `code_reviewer → debugger →
test_writer` 三个任务的请求，走流式接口时会退化成单 Agent 自己硬扛，跟 `/ask` 的
行为不一致。

**流式的粒度**：Coordinator 内部是"规划一次 LLM 调用 + N 个子 Agent 各一次 LLM 调用"，
不是像 `agent/agent.py` 那样单次 `run_agent_loop` 天然支持逐 token 回调。规划阶段
必须拿到完整 JSON 才能解析出任务列表，没法边生成边流式；所以粒度选在**子任务完成
即推送**，而不是逐 token：
- 不需要子 Agent（`direct_reply`）：只有一次完整回复，作为一个整块推送
- 需要拆解成多个子任务：每个子 Agent 跑完就立刻推送它的结果，不用等其他并行任务
  也跑完（比 `/ask` 等全部任务聚合完才返回更快看到进展）

先给 `dispatch()`（`coordinator/dispatcher.py`）加一个可选的 `on_task_done` 回调，
在 `_run_one` 里子任务算完就立刻调用——因为多个任务在同一波次里用
`asyncio.gather` 并发执行，`_run_one` 内部调用回调的时机是"这一个任务完成"，不是
"整个波次/整个 gather 完成"，所以先跑完的子 Agent 能先被推送出去：

```python
# coordinator/dispatcher.py

async def dispatch(
    tasks: list[TaskSpec],
    session_id: str | None = None,
    on_task_done: Callable[[TaskSpec, str], None] | None = None,
) -> tuple[dict[str, str], Usage]:
    ...
    if runnable:
        coros = [_run_one(spec, results, log, on_task_done) for spec in runnable]
        done = await asyncio.gather(*coros, return_exceptions=True)
    ...


async def _run_one(spec, prior_results, log=None, on_task_done=None) -> tuple[str, Usage]:
    ...
    text, usage = await agent.run(task=spec.input, context=context or None)
    if on_task_done:
        on_task_done(spec, text)
    return text, usage
```

`CoordinatorAgent` 新增 `ask_stream()`，用跟 `agent/agent.py` 原来一样的
"Queue + 回调 + 后台 Task"模式桥接同步回调和异步生成器；`clear_session()` 也搬过来，
直接复用 `CoordinatorAgent.run()` 已经用的 `_store`：

```python
# coordinator/agent.py

async def clear_session(session_id: str):
    """清除一个会话的历史（文件 + Redis 缓存），供 FastAPI /session/clear 调用。"""
    await _store.clear(session_id)


class CoordinatorAgent:
    ...

    async def ask_stream(self, user_request: str, session_id: str | None = None) -> AsyncGenerator[str, None]:
        """
        流式版本的 run()：不需要子 Agent 时整块推送回复；需要子 Agent 时按任务完成
        顺序逐个推送，不等全部任务聚合完。session_id 用法跟 run() 一致。
        """
        history = None
        if session_id is not None:
            history = await self._store.load_messages(session_id, max_turns=10)
            await self._store.append_message(session_id, "user", user_request)

        tasks, direct_reply, _ = await make_plan(user_request, session_id=session_id, history=history)

        queue: Queue[str | None] = Queue()
        collected: list[str] = []

        def emit(text: str):
            collected.append(text)
            queue.put_nowait(text)

        async def run_loop():
            if direct_reply is not None:
                emit(direct_reply)
            elif tasks:
                def on_task_done(spec, text):
                    emit(f"**[{spec.agent}]**\n{text}\n\n---\n\n")
                await dispatch(tasks, session_id=session_id, on_task_done=on_task_done)
            else:
                emit("无法理解请求，请提供更多信息。")

            if session_id is not None:
                await self._store.append_message(session_id, "assistant", "".join(collected))
            queue.put_nowait(None)

        loop_task = asyncio.create_task(run_loop())

        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            yield chunk

        await loop_task
```

> **为什么用 `Queue` 桥接，不直接 `async for` 子 Agent 的结果？** `dispatch()` 内部
> 是并发跑多个任务（`asyncio.gather`），跑完的顺序和 `tasks` 列表顺序不一定一致，
> 也没有现成的"边跑边产出"的异步迭代器接口。用 `Queue` 是第 2 章 `ask_stream()` 就用
> 过的模式——启动一个后台 `Task` 去跑真正的逻辑，通过回调把结果 `put` 进队列，
> 主协程只管 `await queue.get()` 消费，两边通过队列解耦，不用改 `dispatch()` 的
> 并发结构。

---

## 10.5 在 FastAPI 接口中传入 session_id

`main.py` 里的 `/ask` 按 7.1.1 节定下的分工继续走 `CoordinatorAgent`（规划 → 分发 →
聚合），不切回单 Agent 直接调用——`/swarm/ask` 才是"提交任务、内部另一套编排"的入口，
两者的分界是"编排方式不同"，不是"要不要记忆"。要让 `/ask` 也有记忆，正确做法是给
`CoordinatorAgent` 本身加上 `session_id`，而不是绕开它退回没有规划/分发能力的
`ask()`。

> **踩过的坑**：早期一版实现图省事，直接把 `/ask` 改成调用 `agent/agent.py` 的
> `ask()`（第 4/5 章留下的单 Agent 直接对话实现），因为 `SessionStore` 先接在了那里、
> 改起来最快。这样虽然让 `/ask` 有了记忆，但代价是丢掉了规划/分发能力——原本一句
> "审查这段代码顺便把测试也写一下"会被 Coordinator 拆成
> `code_reviewer → debugger → test_writer` 三个任务串行执行，改完之后就变成单个
> Agent 自己硬扛，行为和文档描述的不一致。教训是：**给一条链路加新能力时，加到它
> 该在的那一层（这里是 Coordinator），不要因为另一层实现起来更省事就把调用方悄悄
> 切过去**。

`CoordinatorAgent.run()` 加一个可选的 `session_id` 参数，内部直接复用 10.3 节的
`SessionStore`——调用前加载历史（喂给 `make_plan`，让规划器理解"这个""那个文件"这类
指代），调用后把最终聚合结果写回历史：

```python
# coordinator/agent.py

from persistence.session_store import SessionStore
from persistence.redis_client import create_redis_client

_store = SessionStore(base_dir="sessions/", redis_client=create_redis_client())


class CoordinatorAgent:
    async def run(self, user_request: str, session_id: str | None = None) -> AskResult:
        history = None
        if session_id is not None:
            history = await _store.load_messages(session_id, max_turns=10)
            await _store.append_message(session_id, "user", user_request)

        # 把历史传给规划器，理解上下文里的指代
        tasks, direct_reply, plan_usage = await make_plan(
            user_request, session_id=session_id, history=history,
        )
        ...
        # 聚合出 text 之后
        if session_id is not None:
            await _store.append_message(session_id, "assistant", text)

        return AskResult(text=text, input_tokens=..., output_tokens=...)
```

`make_plan()` 也要能接收历史（`coordinator/planner.py`），把历史消息拼进发给 LLM 的
`messages` 列表里：

```python
# coordinator/planner.py

async def make_plan(user_request, session_id=None, history=None):
    messages = list(history) if history else []
    messages.append(Message(role="user", content=[TextBlock(text=user_request)]))
    response = await provider.chat(messages=messages, system=_COORDINATOR_SYSTEM, max_tokens=2048)
    ...
    return tasks, direct_reply, response.usage   # usage 一起带出来，给 /ask 算 Token 用量
```

> **usage 从哪来？** Coordinator 一次 `/ask` 背后可能是多次 LLM 调用（规划一次 + 每个
> 子 Agent 各一次），跟 `agent/agent.py` 里单 Agent 一次调用就有 `total_usage` 不一样，
> 需要 `coordinator/dispatcher.py` 的 `dispatch()` 把各子 Agent（`sub_agents/base.py`
> 的 `run()` 现在返回 `(文字, usage)` 而不是纯文字）的用量也汇总一遍，连同规划阶段的
> 用量一起加总，才能给 `/ask` 返回完整的 `usage.input_tokens` / `output_tokens`。

`main.py` 里 `/ask` 的写法不变，还是调用一个全局单例，只是这个单例现在是
`CoordinatorAgent`：

```python
# main.py

from coordinator.agent import CoordinatorAgent

_coordinator = CoordinatorAgent()   # 全局单例，启动时初始化一次


class AskRequest(BaseModel):
    """POST /ask 的请求体格式"""
    question: str = Field(..., min_length=1, description="用户的问题")
    session_id: str = Field(
        default="web:default",
        description="会话 ID，同一 session_id 的多次请求会共享对话历史",
    )


class AskResponse(BaseModel):
    """POST /ask 的响应体格式"""
    text: str = Field(default="", description="Agent 的回答")
    session_id: str = Field(default="", description="本次请求使用的会话 ID，回传给前端便于下一轮携带")
    usage: dict = Field(default_factory=dict, description="Token 用量")
    error: str | None = Field(default=None, description="错误信息")


@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    try:
        result = await _coordinator.run(req.question, session_id=req.session_id)
        return AskResponse(
            text=result.text,
            session_id=req.session_id,
            usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
    except Exception as e:
        return AskResponse(session_id=req.session_id, error=str(e))
```

> **更新**：`agent/agent.py` 后来被彻底删除了。最初的想法是留着它给 `cli.py` 和
> `/ask/stream` 用，`/ask` 单独走 Coordinator——但这样一来"要不要走 Coordinator
> 规划"就取决于用户走了哪个入口，而不是任务本身的复杂度：同样一句"帮我审查这段代码
> 顺便把测试也写一下"，走 `/ask` 会被拆成 `code_reviewer` + `test_writer` 两个子任务，
> 走 `/ask/stream` 或 `cli.py` 却只有单 Agent 硬扛，行为不一致，用户体验也不统一。
>
> 所以后来把 `cli.py`、`/ask/stream`、`/session/clear` 全部改成调用
> `coordinator/agent.py` 里的 `CoordinatorAgent`（`run()`/`ask_stream()`），
> `agent/agent.py` 整个文件删掉，只留 `agent/loop.py`、`agent/executor.py`
> 这些底层组件继续被 `sub_agents/base.py` 复用（这个包后来又改名为
> `agent_core/`，见 4.6 更新说明）。现在无论从哪个入口进来，
> 都是同一套"规划 → 分发 → 聚合"逻辑，行为完全一致。

客户端调用时保持相同的 `session_id`，Agent 就会记住上下文：

```bash
# 第一轮
curl -X POST http://localhost:8002/ask \
  -d '{"question": "我叫小明，我在用 Python 写一个数据分析项目", "session_id": "user_001"}'

# 第二轮（Agent 应该记得"小明"和"数据分析项目"）
curl -X POST http://localhost:8002/ask \
  -d '{"question": "帮我想想这个项目应该用什么数据库？", "session_id": "user_001"}'
```

`/ask/stream`（SSE 流式接口）同样加上 `session_id`（URL 查询参数，可选）：

```python
# main.py
from coordinator.agent import CoordinatorAgent, clear_session

_coordinator = CoordinatorAgent()

@app.get("/ask/stream")
async def ask_stream_endpoint(question: str, session_id: str | None = None):
    async def event_generator():
        try:
            async for chunk in _coordinator.ask_stream(question, session_id=session_id):
                data = json.dumps({"type": "text_delta", "text": chunk}, ensure_ascii=False)
                yield f"data: {data}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            error_data = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={...})
```

`CoordinatorAgent.ask_stream()` 的实现和推送粒度见 10.4b：不需要子 Agent 时整块推送
回复，需要多个子 Agent 时哪个先跑完就先推送哪个，不是逐 token。

再补一个清除会话的接口——10.3 的 `SessionStore.clear()` 之前只是个方法，没有任何路由
接到它，前端的"清空"按钮点了也只是清空页面上的气泡，服务端历史其实还在：

```python
# main.py

class ClearSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="要清除的会话 ID")


@app.post("/session/clear")
async def clear_session_endpoint(req: ClearSessionRequest) -> dict:
    await clear_session(req.session_id)
    return {"session_id": req.session_id, "cleared": True}
```

**前端怎么拿到 session_id？** 没有登录系统，`session_id` 就不能像飞书那样直接用平台
自带的 `sender_id`/`chat_id`，得让浏览器自己造一个、并持久化下来——同一浏览器下次
打开页面还是这个 ID，能接着聊；换个浏览器/清了缓存就是新会话。用 `localStorage` 存
一个 `crypto.randomUUID()` 即可：

```js
// static/index.html

function getSessionId() {
    let id = localStorage.getItem('session_id');
    if (!id) {
        id = crypto.randomUUID();
        localStorage.setItem('session_id', id);
    }
    return id;
}
const SESSION_ID = getSessionId();

// POST /ask 时带上
fetch(BASE + '/ask', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question, session_id: SESSION_ID }),
});

// GET /ask/stream 时带上
const url = BASE + '/ask/stream?question=' + encodeURIComponent(question) +
    '&session_id=' + encodeURIComponent(SESSION_ID);

// "清空"按钮：先清服务端历史，再清页面
async function clearChat() {
    document.getElementById('chat').innerHTML = '';
    await fetch(BASE + '/session/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: SESSION_ID }),
    });
}
```

> `AskRequest.session_id` 上的默认值 `"web:default"` 只是兜底（比如直接用 curl 测试、
> 没传这个字段的场景），走真正前端页面时请求体里一定会带上浏览器自己持久化的
> `SESSION_ID`，不会落到这个默认值上，也就不会出现"所有访客共用一个会话、历史互相
> 串"的问题。

---

## 10.6 本章检查清单

```
□ sessions/ 目录在 .gitignore 里（不要把用户对话提交到 Git）

□ append_message() 能正常写入 JSONL 文件
  验证：问一个问题，然后 cat sessions/de/default.jsonl 查看内容

□ load_messages() 能正确重建 Message 对象列表
  验证：先对话 3 次，然后 python -c "import asyncio; from persistence.session_store import SessionStore; ..."

□ 调用 POST /session/clear 清除会话后再对话，Agent 不再记得之前的内容
  （不是只清前端页面——之前 SessionStore.clear() 没有接任何路由，这一条测不了）

□ 重启服务后，再次发同一 session_id 的请求，Agent 还记得之前的对话

□ /ask/stream 传入 session_id 时也有记忆效果，跟 /ask 表现一致

□ 前端刷新页面后 localStorage 里的 session_id 不变，Agent 还记得刷新前的对话；
  换一个浏览器 / 清掉 localStorage 后是全新会话，不会看到别人的历史

□ 人为让一次 ask() 调用在写完 user、还没写 assistant 时抛异常（比如临时改 provider
  抛错），验证下一次同 session_id 的请求不会因为"连续两条 user 消息"报错
  （load_messages() 应该已经丢弃了那条孤儿 user 记录）

□ （可选，装了 Redis 才测）Redis List 缓存写入成功
  验证：redis-cli lrange session_msgs:default 0 -1，应该能看到完整的消息 JSON（不只是预览）

□ （可选）验证缓存确实被读到、而不是每次都在读文件
  验证：先对话几轮预热缓存，然后临时把 sessions/ 目录改名（模拟文件不可读），
  再发一条 max_turns 在缓存窗口内的请求，Agent 应该仍然记得历史（说明走的是 Redis 缓存）

□ （可选）验证缓存未命中时能正确回填
  验证：redis-cli del session_msgs:default 清空缓存后再发一条请求，
  Agent 依然记得历史（退回读文件），且之后 lrange session_msgs:default 0 -1 能看到缓存已重新写入
```

---

# 第 11 章：阶段 11 —— 可观测性

> **本章目标**：让每个 Agent 调用都可查可追——结构化日志、Prometheus 指标、OpenTelemetry 链路追踪。

---

## 11.1 三个维度的可观测性

| 维度 | 工具 | 解答的问题 |
|------|------|----------|
| 结构化日志 | structlog | "这次 Agent 调用发生了什么？" |
| 指标监控 | Prometheus + Grafana | "整个系统的健康状态如何？" |
| 链路追踪 | OpenTelemetry | "这个请求经过了哪些 Agent 和工具？" |

---

## 11.2 结构化日志 `observability/logging.py`

```python
# observability/logging.py
"""
结构化日志配置。

为什么用 structlog 而不是普通 print？
- print：人读的，机器处理难（日志聚合系统无法解析）
- structlog：输出 JSON 格式，每个字段有固定 key，
  可以用 Grafana Loki、ELK、腾讯云日志服务等工具
  精确过滤：session_id=xxx 的所有日志、provider=openai 且 status=error 的日志
"""
import structlog
import logging


def setup_logging(log_level: str = "INFO"):
    """初始化结构化日志，在服务启动时调用一次。"""

    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(message)s",
    )

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(ensure_ascii=False),   # 输出 JSON
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


# 全局 logger（各模块 from observability.logging import logger）
logger = structlog.get_logger()
```

> **⚠️ 踩坑记录：`structlog.stdlib.filter_by_level` / `add_logger_name` 不能和 `PrintLoggerFactory` 一起用**
>
> `structlog.stdlib.filter_by_level` 和 `structlog.stdlib.add_logger_name` 这两个processor，设计前提是底层 `logger_factory` 产出的是**真正的 `logging.Logger`**（比如用 `structlog.stdlib.LoggerFactory()`）——前者要读 `logger.disabled`，后者要读 `logger.name`。
>
> 但这里为了直接输出 JSON、不依赖 Python 标准 logging 的 handler 机制，用的是 `structlog.PrintLoggerFactory()`，它产出的 `PrintLogger` 根本没有 `.disabled` / `.name` 这两个属性。只要调用 `logger.info(...)`，就会在这两个 processor 里炸出：
> ```
> AttributeError: 'PrintLogger' object has no attribute 'disabled'
> AttributeError: 'PrintLogger' object has no attribute 'name'
> ```
> 这个 bug 在代码写好之后不会立刻暴露——因为只要没人调用 `setup_logging()`，`structlog.configure()` 就不会生效，用的是 structlog 默认配置，不会报错。**只有在服务启动时真正调用了 `setup_logging()`，第一条 `logger.info()` 才会崩掉。** 所以本章的代码必须按上面的最终版本抄（不含这两行），或者干脆把 `logger_factory` 换成 `structlog.stdlib.LoggerFactory()`（那样就需要这两个 processor 配合工作）。
>
> 这里选择保留 `PrintLoggerFactory()`，直接删掉这两个 processor：日志级别过滤已经由 `wrapper_class=structlog.make_filtering_bound_logger(logging.INFO)` 处理，`add_logger_name` 只是加个 `logger_name` 字段，不影响功能，删掉不影响可观测性目标。

**使用示例（在 Agentic Loop 里）：**

```python
from observability.logging import logger

# 在 run_agent_loop 里记录每轮的状态
logger.info(
    "agent_loop_turn",
    session_id=session_id,
    turn=state.turn_count,
    provider=provider.model_name,
    input_tokens=turn_usage.input_tokens,
    output_tokens=turn_usage.output_tokens,
    tool_calls=[tc.name for tc in tool_calls],
    stop_reason="tool_use" if tool_calls else "end_turn",
)
```

**输出的 JSON 日志（机器可解析）：**

```json
{"event": "agent_loop_turn", "session_id": "user_001", "turn": 2, "provider": "claude-sonnet-4-6", "input_tokens": 1234, "output_tokens": 567, "tool_calls": ["calculator"], "stop_reason": "tool_use", "timestamp": "2025-01-01T12:00:00Z", "level": "info"}
```

> **为什么 JSON 就叫"机器可解析"？** 每条日志是一个字段固定的 JSON 对象，机器不用写正则去猜，直接按 key 过滤和聚合：`session_id == "user_001"` 拉出一次请求的全部日志、`level == "error"` 筛所有错误、对 `elapsed_ms` 字段直接算平均/分位数。临时排查时命令行用 `jq` 就能拆（`... | jq 'select(.level=="warning")'`）；生产环境则交给日志聚合系统自动建索引。
>
> **本项目的日志聚合方案：Grafana Loki。** 服务只管把 JSON 日志打到标准输出（stdout），由采集器 **Promtail** 读取容器 stdout 推送给 **Loki** 存储，最后在 **Grafana** 里用 LogQL 查询——这样日志（Loki）、指标（Prometheus）、链路（Tempo）三样都在同一个 Grafana 界面，还能靠共同的 `session_id` 互相跳转。具体部署脚本见第 13 章，本章 11.3.1 先预拉镜像。

---

## 11.3 Prometheus 指标 `observability/metrics.py`

```python
# observability/metrics.py
"""
Prometheus 指标定义。

Prometheus 是业界标准的监控系统，Grafana 可以展示 Prometheus 数据。
指标数据通过 /metrics 端点暴露，Prometheus 服务器定期来"拉取"。
"""
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST


# ── 计数器（Counter）：只增不减，记录总量 ─────────────────────────────────────

# 总请求数（按 provider、model、status 分类）
agent_requests_total = Counter(
    "agent_requests_total",
    "Agent 接收并处理的请求总数",
    ["provider", "model", "status"],   # label 维度，可以分类过滤
)

# 工具调用总数
tool_calls_total = Counter(
    "tool_calls_total",
    "工具被调用的总次数",
    ["tool_name", "status"],   # status: success / error
)


# ── 直方图（Histogram）：记录延迟分布 ─────────────────────────────────────────

# Agent 请求延迟（从收到请求到返回结果）
agent_latency_seconds = Histogram(
    "agent_latency_seconds",
    "Agent 完成一次请求的端到端延迟（秒）",
    ["provider"],
    # 桶的边界：0.5s、1s、2s、5s、10s、30s、60s
    # 意思是：有多少请求在 0.5s 内完成，有多少在 1s 内……
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)


# ── 计量器（Gauge）：可增可减，记录当前状态 ───────────────────────────────────

# 当前活跃中的请求数
active_requests = Gauge(
    "active_requests",
    "当前正在处理中的请求数",
)


# ── Token 用量（用 Counter 累计）─────────────────────────────────────────────

token_usage_total = Counter(
    "token_usage_total",
    "累计 Token 用量",
    ["type"],   # type: input / output / cache_read
)


# ── 辅助函数 ──────────────────────────────────────────────────────────────────

def record_request(provider: str, model: str, status: str, latency: float, usage=None):
    """一次 Agent 请求完成时记录所有相关指标。"""
    agent_requests_total.labels(provider=provider, model=model, status=status).inc()
    agent_latency_seconds.labels(provider=provider).observe(latency)

    if usage:
        token_usage_total.labels(type="input").inc(usage.input_tokens)
        token_usage_total.labels(type="output").inc(usage.output_tokens)
        if usage.cache_read_tokens:
            token_usage_total.labels(type="cache_read").inc(usage.cache_read_tokens)
```

**在 FastAPI 中暴露 /metrics 端点：**

```python
# main.py

from fastapi import FastAPI, HTTPException, Response
from observability.metrics import generate_latest, CONTENT_TYPE_LATEST, record_request
from providers.types import Usage

@app.get("/metrics")
async def metrics():
    """Prometheus 指标端点（Prometheus 服务器来拉取数据）。"""
    return Response(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
```

放在 `/health` 之后、`/ask` 之前即可，路由顺序不影响功能。

**在 `/ask` 里调用 `record_request()`——成功和失败都要记：**

只暴露端点还不够，指标数字要有人在业务代码里"打点"才会变化。`/ask` 是完整响应接口（第 6 章的 `CoordinatorAgent.run()`），在它的成功分支和异常分支都调用一次 `record_request()`，这样无论请求成不成功，`agent_requests_total{status=...}` 都会增加：

```python
# main.py
@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(req: AskRequest) -> AskResponse:
    start = time.time()
    log = logger.bind(session_id=req.session_id)
    log.info("ask_request_received", question=req.question[:60])

    try:
        result = await _coordinator.run(req.question, session_id=req.session_id)
        elapsed_ms = round((time.time() - start) * 1000)
        log.info("ask_request_done", elapsed_ms=elapsed_ms)

        # 成功：记 provider/model/status=success 三个维度 + 延迟 + token 用量
        record_request(
            provider=settings.llm_provider,
            model=settings.llm_model,
            status="success",
            latency=time.time() - start,
            usage=Usage(input_tokens=result.input_tokens, output_tokens=result.output_tokens),
        )
        return AskResponse(
            text=result.text,
            session_id=req.session_id,
            usage={
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
    except Exception as e:
        elapsed_ms = round((time.time() - start) * 1000)
        log.error("ask_request_error", elapsed_ms=elapsed_ms, error=str(e))

        # 失败：status=error，不传 usage（这次请求没有真实 token 用量数据）
        record_request(
            provider=settings.llm_provider,
            model=settings.llm_model,
            status="error",
            latency=time.time() - start,
        )
        return AskResponse(session_id=req.session_id, error=str(e))
```

> **注意 1**：`AskResult`（`coordinator/agent.py` 里 `CoordinatorAgent.run()` 的返回类型）没有 `provider`/`model` 字段，所以这里的 label 直接取 `settings.llm_provider` / `settings.llm_model`（全局配置），而不是从 result 里取——单 provider 部署场景下这是等价的，如果以后要支持每次请求动态切换 provider，需要把 provider/model 一起放进 `AskResult`。
>
> **注意 2**：`GET /ask/stream`（SSE 流式接口）**没有**接入 `record_request()`——它的生成器只管边生成边往外推文本增量，函数返回前拿不到聚合后的最终 usage 和总耗时，要打点得先改造流式生成器在结束时收集 usage，本次没有做这一步，如果需要可以补。

**指标一览表（中英对照）**

| 指标名 Metric Name | 类型 Type | 中文说明 | English Description | Label 维度 | 状态 |
|---|---|---|---|---|---|
| `agent_requests_total` | Counter | Agent 接收并处理的请求总数 | Total number of requests received and processed by the agent | `provider`, `model`, `status` | ✅ 已接入（`/ask` 成功/失败都会 +1） |
| `agent_latency_seconds` | Histogram | Agent 完成一次请求的端到端延迟（秒），桶边界 0.5/1/2/5/10/30/60s | End-to-end request latency distribution (seconds), buckets: 0.5/1/2/5/10/30/60s | `provider` | ✅ 已接入 |
| `token_usage_total` | Counter | 累计 Token 用量 | Cumulative token usage | `type`（input / output / cache_read） | ✅ 已接入（仅成功请求记录，失败时不传 usage） |
| `tool_calls_total` | Counter | 工具被调用的总次数 | Total number of tool invocations | `tool_name`, `status`（success/error） | ⚠️ 已定义但代码里未调用，当前恒为空 |
| `active_requests` | Gauge | 当前正在处理中的请求数 | Number of requests currently being processed | 无 | ⚠️ 已定义但代码里未 `.set()`/`.inc()`，恒为 0 |

> **prometheus_client 的一个坑**：`agent_requests_total`/`agent_latency_seconds`/`token_usage_total` 这类带 label 的 Counter/Histogram，只有代码里真正调用过 `.labels(xxx=...).inc()`/`.observe()` 之后，那个具体 label 组合的时间序列才会出现在 `/metrics` 输出里——进程刚启动、还没打过一次 `/ask` 请求时，`/metrics` 端点只会输出不带 label 的 `active_requests`（Gauge 无论如何都会输出一行，哪怕值是 0），Grafana 的 Metric 下拉框也就搜不到那几个带 label 的指标名。这不是 bug，打一次 `/ask` 请求后指标就会出现。

> **采集范围的局限**：`record_request()` 只挂在 `/ask`（非流式）这一个端点上，`GET /ask/stream`（SSE）和 `/swarm/ask`（Swarm 架构）的请求**不会**计入这几个指标。Prometheus 侧除了这 5 个自定义指标，还会附带 `prometheus_client` 库自带的进程级指标（`process_cpu_seconds_total`、`process_resident_memory_bytes`、`python_gc_collections_total` 等）以及 Prometheus 自身的抓取元指标（如 `scrape_duration_seconds`）。

---

### 11.3.1 预拉取可观测性栈镜像

可观测性三件套需要独立部署，本章先拉取镜像（确保网络通畅、镜像可下载），具体 Docker 部署脚本统一放到第 13 章。各镜像职责：

| 镜像 | 维度 | 职责 |
|------|------|------|
| `prom/prometheus:latest` | 指标 | 定期拉取 `/metrics` 并存储 |
| `grafana/tempo:latest` | 链路 | 接收 OTLP 链路并存储 |
| `grafana/loki:latest` | 日志 | 存储 JSON 日志，供 LogQL 查询 |
| `grafana/promtail:latest` | 日志 | 采集器：读容器 stdout 推给 Loki |
| `grafana/grafana:latest` | 可视化 | 统一界面，同时连上以上三者 |

```bash
# 预拉取镜像（约 800MB，首次下载需要几分钟）
docker pull prom/prometheus:latest
docker pull grafana/grafana:latest
docker pull grafana/tempo:latest
docker pull grafana/loki:latest
docker pull grafana/promtail:latest
```

> **为什么用 Tempo 而不是 Jaeger？** 两者都能接收 OpenTelemetry（OTLP）链路数据，但 Tempo 是 Grafana 官方的链路存储后端，**不需要单独的 UI**——它直接作为 Grafana 的一个数据源，链路和指标（Prometheus）在同一个 Grafana 界面里查看，运维一套即可。Jaeger 则自带独立 UI（16686 端口），需要额外开一个页面。本指南统一到 Grafana，所以选 Tempo。
>
> **Loki 和 Promtail 为什么是两个镜像？** Loki 只负责存储和查询日志，本身不会主动去读日志文件；Promtail 是配套的**采集器（agent）**，它读取容器的 stdout 日志再推送给 Loki。这和 Prometheus「主动拉指标」相反——日志是「被推送」进来的，所以需要 Promtail 这个推送方。
>
> 这些镜像在第 13 章的 `docker-compose.yml` 里会和 PolyCoder 服务一起编排启动，这里先不写部署脚本，避免分散注意力。

---

## 11.4 OpenTelemetry 链路追踪 `observability/tracing.py`

```python
# observability/tracing.py
"""
OpenTelemetry 链路追踪。

链路追踪回答的问题：
  "用户的这个请求，经过了哪些 Agent？每个 Agent 耗时多少？哪里最慢？"

每个操作（一次 Agent 调用、一次工具调用）创建一个 Span（跨度）。
多个 Span 串成一条链路（Trace），推送到 Tempo 存储，在 Grafana 的 Explore 页面里可视化展示。
"""
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource

from observability.logging import logger   # setup_logging 先于 setup_tracing 调用，此时 logger 已配置


def setup_tracing(service_name: str = "my-agent", otlp_endpoint: str | None = None):
    """
    初始化链路追踪，在服务启动时调用一次。

    otlp_endpoint：OTLP 导出地址（Tempo、Jaeger 等），例如 http://tempo:4317
    如果不传，链路数据不导出（但代码里的 tracer 调用不报错）。
    """
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            logger.info("tracing_enabled", otlp_endpoint=otlp_endpoint)
        except ImportError:
            logger.warning("tracing_exporter_missing", hint="未安装 opentelemetry-exporter-otlp，链路数据不导出")

    trace.set_tracer_provider(provider)


# 全局 tracer（各模块使用）
tracer = trace.get_tracer("my-agent")
```

**在 Agentic Loop 里添加追踪：**

`run_agent_loop`（`agent_core/loop.py`）里，一次调用是一个 Trace：外层一个 `agent_loop` Span 贯穿整个 while 循环，每一轮 LLM 调用是一个 `turn_N` 子 Span。

```python
from observability.tracing import tracer

# agent_core/loop.py — run_agent_loop 函数体
with tracer.start_as_current_span("agent_loop") as loop_span:
    loop_span.set_attribute("session_id", session_id or "")
    loop_span.set_attribute("provider", provider.model_name)

    while True:
        # ...（压缩检查 / 轮次限制检查）

        with tracer.start_as_current_span(f"turn_{state.turn_count}") as turn_span:
            # ... LLM 调用（流式/非流式）
            turn_span.set_attribute("input_tokens", turn_usage.input_tokens)
            turn_span.set_attribute("output_tokens", turn_usage.output_tokens)
            turn_span.set_attribute("tool_calls", [tc.name for tc in tool_calls])

        # ...
        tool_results = await executor.execute_all(tool_calls)   # 见下方
```

**工具调用的 Span 要放在 `ToolExecutor` 内部，不要放在 loop 里的 `for` 循环：**

`ToolExecutor.execute_all()`（`agent_core/executor.py`）用 `asyncio.gather()` **并行**执行所有工具调用（见 6.x 章）。如果照搬"每个工具一个 Span"的直觉写法，在 `run_agent_loop` 里用 `for tc in tool_calls:` 顺序创建 Span，会把并行执行改成串行，白白拖慢速度。正确做法是把 Span 下沉到实际执行单个工具的 `_execute_one` 里，`asyncio.gather` 调度多个协程时各自的 Span 仍然独立记录，互不影响并发：

```python
# agent_core/executor.py
from observability.tracing import tracer

class ToolExecutor:
    async def execute_all(self, tool_calls):
        # 保持并行：Span 不在这里逐个创建
        tasks = [self._execute_one(tc) for tc in tool_calls]
        return list(await asyncio.gather(*tasks))

    async def _execute_one(self, tool_call):
        with tracer.start_as_current_span(f"tool_{tool_call.name}") as tool_span:
            tool_span.set_attribute("tool.name", tool_call.name)
            # ... 执行工具，异常时 tool_span.set_attribute("tool.error", ...)
```

**在服务启动时初始化：`setup_logging()` + `setup_tracing()`**

两者都必须在服务启动时各调用一次，且顺序是先日志、后追踪（这样 `setup_tracing()` 内部如果打日志，走的已经是配置好的结构化 logger）：

- `setup_logging()` 不调用 → 用 structlog 默认配置，日志不是 JSON 格式，`session_id` 等字段也不会自动带上。
- `setup_tracing()` 不调用 → `tracer` 用的是 OpenTelemetry 默认 `TracerProvider`（No-op），Span 正常创建但不会导出到任何地方，`agent_loop`/`turn_N`/`tool_xxx` 全部是空转。

`otlp_endpoint` 从配置读取，不填就只在本地"空转"（不报错，也不导出）：

```python
# core/config.py
class Settings(BaseSettings):
    ...
    # 可观测性：OpenTelemetry 链路追踪导出地址（Jaeger/Tempo 等），不填则不导出
    otel_exporter_otlp_endpoint: str = ""
```

```python
# main.py
from observability.logging import logger, setup_logging
from observability.tracing import setup_tracing
from core.config import settings
from core.workspace import get_workspace

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _swarm_save_task

    setup_logging()
    setup_tracing(service_name="my-agent", otlp_endpoint=settings.otel_exporter_otlp_endpoint or None)

    # 初始化统一工作目录：所有工具和子 Agent 的文件操作都限定在此目录内。
    # 用 WORKSPACE=/path uvicorn main:app 指定，不设则用当前目录。
    workspace = get_workspace()
    workspace.mkdir(parents=True, exist_ok=True)

    logger.info("server_starting")
    logger.info("workspace_ready", workspace=str(workspace))   # 启动即打印实际工作目录，方便确认
    logger.info("api_docs_url", url="http://localhost:8002/docs")
    logger.info("stream_test_hint", hint="curl -N 'http://localhost:8002/ask/stream?question=你好'")

    # ... 原有的 Redis 恢复 / 常驻 Swarm Agent 启动 / 后台备份任务逻辑不变

    yield

    # ... 原有的收尾逻辑不变
    logger.info("server_shutdown")
```

> **启动时确认工作目录**：服务起来后第一时间会打印一条 `{"event": "workspace_ready", "workspace": "/data/workspace", ...}` 日志。通过飞书或 HTTP 触发的任何文件操作，都落在这个目录里——排查"文件写到哪去了"时先看这条日志。

**验证方式**：用 `TestClient` 触发一次完整的 `lifespan`（比直接跑 `uvicorn` 更快看到启动阶段的报错），确认没有异常且能拿到 `/metrics` 数据：

```python
import main
from fastapi.testclient import TestClient

with TestClient(main.app) as c:
    r = c.get("/metrics")
    print(r.status_code)   # 200
    print([l for l in r.text.splitlines() if "agent_requests_total" in l])
```

如果这里报 `AttributeError: 'PrintLogger' object has no attribute 'disabled'`（或 `'name'`），说明 `observability/logging.py` 还是旧版本，回到 11.2 节把 `filter_by_level` / `add_logger_name` 从 processors 列表删掉。

---

## 11.5 本章检查清单

```
□ observability/logging.py 的 processors 里不含 structlog.stdlib.filter_by_level /
  add_logger_name（跟 PrintLoggerFactory 不兼容，会在第一条日志时报 AttributeError）

□ setup_logging() 在 lifespan 里调用，日志输出为 JSON 格式
  验证：用 TestClient 触发 lifespan，看到 {"event": "server_starting", ...} 而不报错

□ /metrics 端点可访问
  验证：curl http://localhost:8002/metrics | grep agent_requests

□ /ask 的成功分支和异常分支都调用了 record_request()
  验证：发送几次 /ask 请求后，agent_requests_total{status="success"} 计数器增加；
        故意触发一次异常，agent_requests_total{status="error"} 也增加

□ （已知缺口）/ask/stream 暂未接入 record_request()——流式生成器结束时拿不到聚合 usage，
  如需要，要先改造生成器在收尾时收集 usage 再打点

□ Grafana 能连接到 Prometheus 并展示 agent_latency_seconds 的图表

□ 日志输出到 stdout（不写死到文件），这样 Promtail 才能采集到并推给 Loki（部署见第 13 章）
  验证：docker compose logs agent 能看到一行行 JSON 日志

□ setup_tracing() 在 lifespan 里调用，且在 setup_logging() 之后（否则 Span 只在本地创建，不会导出）

□ 工具 Span（tool_{name}）建在 ToolExecutor._execute_one 里，不要改成串行 for 循环

□ （可选）配置 OTEL_EXPORTER_OTLP_ENDPOINT 后，链路推送到 Tempo，在 Grafana → Explore →
  选择 Tempo 数据源里能看到一条完整的链路（agent_loop → turn_N → tool_xxx）
```

---

## 11.6 可观测性整体架构（三大支柱：Metrics / Traces / Logs）

```
                        ┌────────────────────────┐
                        │       Grafana :3000     │ ← 唯一的浏览器入口
                        │  (数据源自动配置好了)     │
                        └───┬─────────┬───────┬───┘
                            │         │       │
                  Prometheus│    Tempo│  Loki │
                     :9090  │   :3200 │ :3100 │
                            │         │       │
```

三条数据各自独立采集、存储，最后都汇总到 Grafana 展示。

### 11.6.1 流程图 1：Metrics（指标）—— Prometheus 拉取模式

```
agent 服务(:8002)
  └─ observability/metrics.py 定义 Counter/Histogram/Gauge
       (agent_requests_total, tool_calls_total,
        agent_latency_seconds, active_requests, token_usage_total)
  └─ main.py 暴露 GET /metrics 端点 (generate_latest)
             │
             │ 每 15s 主动 pull (scrape_interval: 15s)
             ▼
        Prometheus (prometheus.yml → targets: agent:8002)
             │ 存储时序数据到 prometheus_data volume
             ▼
        Grafana 数据源 "Prometheus" (isDefault) 查询展示
```

特点：**拉模式**，Prometheus 主动去 agent 的 `/metrics` 抓取，指标值是代码里手动 `.inc()` / `.observe()` 累加的。

### 11.6.2 流程图 2：Traces（链路追踪）—— OTLP 推送模式

```
agent 服务
  └─ observability/tracing.py setup_tracing()
       创建 TracerProvider，若配置了 otel_exporter_otlp_endpoint
       → OTLPSpanExporter + BatchSpanProcessor
             │
             │ gRPC 推送 span (批量)
             ▼
        Tempo :4317 (OTLP 接收端口)
             │ 落盘存储 (tempo_data volume, local backend)
             ▼
        Tempo :3200 HTTP API
             │
             ▼
        Grafana 数据源 "Tempo" 查询 TraceID
        (jsonData.tracesToMetrics / serviceMap → 可跳转到 Prometheus 指标)
```

特点：**推模式**，agent 主动把 span 推给 Tempo；一次请求会产生一串 span（一条 Trace），用来看这次请求慢在哪一步。

这里用的是 **OpenTelemetry（OTel）**：

- **OpenTelemetry**：标准 + SDK 层。负责在代码里定义 `TracerProvider`、生成 `tracer`、创建 `Span`，并用 **OTLP（OpenTelemetry Protocol）** 协议把 span 批量导出（`BatchSpanProcessor` + `OTLPSpanExporter`，走 gRPC）。厂商中立，换 Jaeger/Zipkin/Tempo 只需要换 exporter 的 endpoint。
- **Tempo**：只是接收端 + 存储后端。`tempo.yaml` 里 `receivers.otlp.protocols.grpc: 0.0.0.0:4317` 就是专门接收 OTel SDK 推来的 OTLP 数据的端口，对应 `docker-compose.yml` 里 tempo 服务暴露的 `4317` 端口。
- **Grafana**：只是查询/展示层，通过 Tempo 数据源的 HTTP API（`3200` 端口）读 Tempo 存的 trace 数据，本身不涉及 OTel 协议。

**注意**：`main.py` 的 `lifespan` 里只有传了 `otlp_endpoint`（即环境变量 `OTEL_EXPORTER_OTLP_ENDPOINT`，一般设为 `http://tempo:4317`），span 才会真正导出到 Tempo；不配置的话，`tracer.start_as_current_span()` 仍然能正常创建 Span、代码不报错，但链路数据不会离开进程，Grafana 里也就看不到任何 trace。

### 11.6.3 流程图 3：Logs（日志）—— 容器日志采集模式

```
agent 服务（以及其他容器）
  └─ observability/logging.py setup_logging()
       structlog → JSONRenderer → 输出到 stdout
             │
             │ Docker 把每个容器 stdout 写成 json-file
             ▼
        /var/lib/docker/containers/*/*.log (宿主机文件)
             │
             │ Promtail 通过 docker_sd_configs 自动发现容器
             │ 挂载 docker.sock + containers 目录（只读）
             ▼
        Promtail 推送 (relabel: 容器名 → container 标签)
             │
             ▼
        Loki :3100 (/loki/api/v1/push 接收)
             │ 存储到 loki_data volume
             ▼
        Grafana 数据源 "Loki" 用 LogQL 查询
```

特点：日志不用改代码埋点，只要容器往 stdout 打 JSON 日志，Promtail 自动采集，靠 `container` 标签区分是哪个容器的日志。

### 11.6.4 三者关联

- 日志里可以带 `trace_id` 字段（如果代码里把当前 span 的 trace_id 写进 structlog 上下文），这样能从 Loki 日志跳到 Tempo 对应链路。
- Tempo 数据源配置了 `tracesToMetrics`，可以从一条 trace 跳转到 Prometheus 对应指标（比如该 service 的延迟直方图）。
- 三者共同点：**Grafana 是唯一 UI**，Prometheus/Tempo/Loki 只是存储+查询后端，不直接暴露给用户看。

---

## 11.7 在 Grafana UI 里怎么查这些数据

访问 `http://localhost:3000`，用 `docker-compose.yml` 里配的 `admin` / `admin` 登录。三条数据都走左侧导航栏的 **Explore**（指南针图标），顶部下拉框切换数据源即可，不需要为了临时查数据专门建 Dashboard。

### 11.7.1 查 Metrics（选数据源 "Prometheus"）

Explore → 数据源选 `Prometheus` → 在查询框写 PromQL：

```promql
# 每个 provider 的请求总量
agent_requests_total

# 按 status 拆分的成功/失败请求速率（最近 5 分钟）
sum by (status) (rate(agent_requests_total[5m]))

# 请求延迟的 P95（分位数），衡量"慢请求"
histogram_quantile(0.95, rate(agent_latency_seconds_bucket[5m]))

# 当前活跃请求数
active_requests

# 累计 token 用量，按 type 拆分
sum by (type) (token_usage_total)
```
查完点右上角 **Add to dashboard** 可以固化成图表面板，长期盯着看就建一个 Dashboard；临时排查用 Explore 就够。

> **为什么 Metric 下拉框里 `agent_latency_seconds` 会展开出好几个指标？** 这是 Histogram 类型的固定行为，不是配置多了——一个 `Histogram(...)` 定义在 `/metrics` 里会拆成多条时间序列：
>
> | 后缀 Suffix | 含义 |
> |---|---|
> | `_bucket{le="X"}` | 累积分桶计数：延迟 ≤X 秒的请求数（`le` = less than or equal），`buckets=[0.5,1,2,5,10,30,60]` 生成 7 条 + 隐含的 `le="+Inf"` |
> | `_sum` | 所有观测值（延迟秒数）总和，配合 `_count` 算平均延迟 |
> | `_count` | 观测次数总数，等价于 `_bucket{le="+Inf"}` |
> | `_created` | 指标首次创建的 Unix 时间戳，`prometheus_client` 自动加的元数据，日常查询用不上 |
>
> Counter（如 `agent_requests_total`）同样会自动带一条 `_created`，但没有 `_bucket`/`_sum`——那两个是 Histogram 独有的。

### 11.7.2 查 Traces（选数据源 "Tempo"）

Explore → 数据源选 `Tempo`，有两种查法：

- **按 TraceID 查**：如果日志里打印过 trace_id（或者从别处拿到了），直接粘贴进查询框，点查询就能看到那一条完整链路的瀑布图（`agent_loop` → `turn_N` → `tool_xxx` 逐层展开，每个 Span 的耗时一目了然）。
- **按服务名搜索（TraceQL）**：不知道 TraceID 时，用查询构建器（Search 标签页）按 `service.name = "my-agent"` 搜最近的链路列表，再点进去看瀑布图；也可以直接写 TraceQL，比如：

```traceql
{ .service.name = "my-agent" && duration > 2s }
```
这条查最近 service 名为 `my-agent` 且总耗时超过 2 秒的慢请求链路，点进某条结果就能看到具体是哪个 `turn_N` 或 `tool_xxx` Span 拖慢的。

> 前提：`.env.docker` 里配了 `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317` 且 agent 至少处理过一次 `/ask` 请求，否则 Tempo 里没有数据可查。

### 11.7.3 查 Logs（选数据源 "Loki"）

Explore → 数据源选 `Loki` → 用 LogQL：

```logql
# 看 agent 容器的全部日志
{container="agent"}

# 只看错误级别日志
{container="agent"} | json | level="error"

# 按 session_id 串起一次会话的所有日志（对应 11.2 节的打点习惯）
{container="agent"} | json | session_id="user_001"

# 统计最近 5 分钟错误日志的速率（配合面板画图）
sum(rate({container="agent"} | json | level="error" [5m]))
```
因为日志是 `structlog.processors.JSONRenderer` 输出的 JSON，`| json` 之后就能按字段（`session_id`、`level`、`event` 等）精确过滤，不用写正则猜格式。

### 11.7.4 三者联动查一次完整请求

排查一次慢请求/报错的典型顺序：

1. 先在 **Loki** 里按 `session_id` 或 `level="error"` 定位到出问题的那条日志，拿到 `session_id`（或日志里带的 `trace_id`，如果代码里加了的话）。
2. 如果日志里有 `trace_id`，直接去 **Tempo** 粘贴该 ID 查瀑布图，看请求卡在哪个 Span；没有的话就按时间窗口 + `service.name` 在 Tempo 里缩小范围找。
3. 确认是个别请求慢还是整体退化，去 **Prometheus** 看 `agent_latency_seconds` 的 P95/P99 曲线和 `agent_requests_total{status="error"}` 的速率，判断影响面。

三个数据源都在同一个 Grafana 里，来回切换不用跳出页面。

> 📌 **进阶备注**（不影响本章已实现内容）：现有三支柱可观测性的**数据**已经很充分，但缺一层
> **失败的结构化归类**——把失败归到"规划缺陷/Agent 间失配/验证失败"三类再跨会话统计。这层
> 归类（`failure_category` 字段）在 **第 16 章 16.13 节** 定义、在 **第 18 章 18.2 节** 给出
> Loki 聚合查询写法。此外，LLM 输出层安全护栏的已知空白说明见 **18.3 节**。

---

# 第 12 章：阶段 12 —— 飞书机器人

> **本章目标**：在飞书里 @ 机器人就能使用 Agent，支持私聊和群聊，显示"处理中"状态。

---

## 12.1 架构说明

```
飞书服务器
    ↓ WebSocket 长连接（事件推送）
ws_manager.py（统一连接管理）
    ↓
handler.py（事件解析 + 消息处理）
    ↓
Agent 处理
    ↓
client.py（调用飞书 API 发送回复）
    ↓
飞书用户
```

**为什么用 WebSocket 而不是 HTTP Webhook？**

| 对比项 | Webhook（HTTP） | WebSocket |
|-------|--------------|----------|
| 需要公网 IP | 是 | 否（主动连接飞书服务器） |
| 延迟 | 毫秒级 | 毫秒级 |
| 开发难度 | 需要 ngrok 或公网服务器 | 本地开发直接用 |
| 稳定性 | 依赖你的服务器可达性 | 飞书服务器主动重连 |

---

## 12.2 在飞书开放平台创建应用（图文步骤）

1. 打开 [https://open.feishu.cn/app](https://open.feishu.cn/app)，登录后点击「创建自建应用」
2. 填写应用名称（如"我的 AI 助手"）和描述
3. 左侧菜单 → **「功能」** → 「机器人」→ 点击「启用」
4. 左侧菜单 → **「权限管理」** → 搜索 `im:message` → 申请读写权限（`im:message:receive_v1` 和 `im:message`）
5. 左侧菜单 → **「事件订阅」**：
   - 订阅方式选「**长连接**」（不需要填 URL）
   - 添加事件：`im.message.receive_v1`（接收消息）
6. 左侧菜单 → **「凭证与基础信息」** → 复制 **App ID** 和 **App Secret**
7. 在 `.env` 里填写：
   ```dotenv
   FEISHU_APP_ID=cli_aadee18ccc7c9cd8
   FEISHU_APP_SECRET=RM0oosaFQBdtEHJzyYzC5bFqAaZLAAsf
   ```
8. 回到应用首页 → 点击「发布版本」→ 「创建版本」→ 填写版本号 → 申请发布

---

## 12.3 飞书客户端 `feishu/client.py`

```python
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
            # 暴露飞书的真实错误（reaction type 非法、权限不足等），不要静默吞掉
            raise RuntimeError(f"add_reaction 失败：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    async def remove_reaction(self, message_id: str, reaction_id: str) -> dict:
        """移除 Emoji 反应（处理完成后移除"处理中"）。"""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}",
                headers=await self._headers(),
            )
        data = resp.json()
        if data.get("code") != 0:
            raise RuntimeError(f"remove_reaction 失败：code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})
```

> **⚠️ emoji_type 是大小写敏感的固定枚举，别自己编。** 直觉上会写 `"Thinking"` 表示"思考中"，但它**不是**飞书的合法值，会返回 `code=231001 reaction type is invalid`——而且如果 `add_reaction` 不检查 `code`、不 `return data`（早期写法就是发完请求什么都不返回），这个错误会被上层的 `except` 静默吞掉，表现成「能回答但没有表情」，极难排查。本项目用 `OnIt`（👌 处理中），实测有效。

> **⚠️ 为什么 Agent 回复要用 `send_markdown` 而不是 `send_text`？** 飞书的 `msg_type: "text"` 是**纯文本**，`**加粗**`、代码块、`# 标题`、列表会原样显示成字面量，不渲染。Agent 的回复几乎都含 Markdown，所以要用 `interactive` 交互式卡片 + `markdown` 元素发送（`send_markdown`）才能正确渲染。注意飞书支持的是 Markdown 的**子集**（加粗/斜体/代码/链接/列表可用，复杂表格、多级嵌套可能不支持）。纯文本的命令回复（`/help`、`/clear`）继续用 `send_text` 即可，没必要包卡片。

---

## 12.4 消息处理器 `feishu/handler.py`

> **并发策略：latest-wins 防抖（debounce）。** 用户连发多条消息时，如果每条都并发处理，会出现两个 Bug：① 多条的「加表情 → 调 LLM → 回复 → 去表情」四步交错执行，**时序错乱**；② 配合飞书重推，还会**一条消息被回复多次**。本项目的策略是**只处理最新一条、丢弃中间的**——收到消息先进一个防抖窗口（0.8s），窗口内又来新消息就取消上一条的处理任务。

> **去重放到哪里？** event_id 去重**不在这里做**，而在上游 `ws_manager` 的**同步回调**里做（见 12.5）——那里是 SDK 单线程串行调用，不会有并发穿透；如果放在 async 的 `handle_event` 里，原始推送和飞书重推的两个协程会并发穿过「检查-添加」，去重失效。

```python
# feishu/handler.py
"""
飞书消息事件处理器。

并发策略：latest-wins 防抖（debounce）。同一会话连发多条消息时，只处理最新一条、
丢弃中间的——收到消息先进入一个防抖窗口，窗口内若又来新消息，就取消上一条的处理任务。
这样避免了「表情/回复时序错乱」和「一条消息被回复多次」的问题。
（event_id 去重在上游 ws_manager 的同步回调里做，那里是单线程，不会有并发穿透。）
"""
import asyncio
import json
from .client import FeishuClient
from coordinator.agent import CoordinatorAgent, clear_session   # 传 session_id 就是带记忆的调用
from observability.logging import logger


class MessageHandler:

    # 防抖窗口：窗口内同一会话的新消息会取代旧消息。正常单条对话几乎无感。
    _DEBOUNCE_SEC = 0.8

    def __init__(self, client: FeishuClient):
        self.client = client
        self._coordinator = CoordinatorAgent()
        # 每个会话当前正在等待/处理的任务，新消息到来时取消它（latest-wins）
        self._pending: dict[str, asyncio.Task] = {}

    async def handle_event(self, event_data: dict):
        """处理飞书推送的事件：解析消息，按会话做 latest-wins 防抖调度。"""
        # 只处理消息接收事件
        if event_data.get("header", {}).get("event_type") != "im.message.receive_v1":
            return

        event = event_data.get("event", {})
        message = event.get("message", {})
        sender = event.get("sender", {})

        # 提取关键信息
        message_id = message.get("message_id", "")
        chat_type = message.get("chat_type", "")    # "p2p"（私聊）或 "group"（群聊）
        sender_id = sender.get("sender_id", {}).get("open_id", "")
        chat_id = message.get("chat_id", message.get("open_id", ""))

        # 确定回复目标（私聊 → 发给用户，群聊 → 发给群）
        if chat_type == "p2p":
            receive_id = sender_id
            receive_id_type = "open_id"
            session_key = f"feishu_p2p:{sender_id}"
        else:
            receive_id = chat_id
            receive_id_type = "chat_id"
            session_key = f"feishu_group:{chat_id}:{sender_id}"

        # 提取文本内容
        msg_type = message.get("message_type", "")
        text = ""
        try:
            content = json.loads(message.get("content", "{}"))
            if msg_type == "text":
                text = content.get("text", "").strip()
            elif msg_type == "post":
                # 富文本格式，提取所有文本
                for para in content.get("content", []):
                    for elem in para:
                        if elem.get("tag") == "text":
                            text += elem.get("text", "")
        except Exception:
            pass

        if not text:
            return

        # latest-wins 防抖：同一会话已有待处理任务，先取消（丢弃旧消息，只回最新一条）
        old = self._pending.get(session_key)
        if old and not old.done():
            old.cancel()

        self._pending[session_key] = asyncio.create_task(
            self._debounced_process(session_key, receive_id, receive_id_type, message_id, text)
        )

    async def _debounced_process(self, session_key, receive_id, receive_id_type, message_id, text):
        """先等一个防抖窗口，窗口内若被新消息取消就直接退出，否则真正处理。"""
        try:
            await asyncio.sleep(self._DEBOUNCE_SEC)
        except asyncio.CancelledError:
            return   # 窗口内被更新的消息取代，直接放弃这条
        try:
            await self._process(session_key, receive_id, receive_id_type, message_id, text)
        finally:
            # 处理完清理自己（避免 _pending 无限增长）
            if self._pending.get(session_key) is asyncio.current_task():
                self._pending.pop(session_key, None)

    async def _process(self, session_key, receive_id, receive_id_type, message_id, text):
        """真正处理一条消息：命令 / 加表情 → 调 Agent → 回复 → 去表情。"""
        # 内置命令处理
        if text.strip() in ("/help", "帮助"):
            await self.client.send_text(
                receive_id,
                "你好！我是 AI 助手。直接发消息给我就能对话。\n/clear 清除对话历史",
                id_type=receive_id_type,
            )
            return

        if text.strip() in ("/clear", "清除历史"):
            await clear_session(session_key)
            await self.client.send_text(receive_id, "对话历史已清除。", id_type=receive_id_type)
            return

        # 添加"处理中"Emoji 反应（OnIt 是飞书表示"正在处理"的合法枚举值，
        # 大小写敏感；"Thinking" 不是合法值，会返回 231001 reaction type is invalid）
        reaction_id = None
        try:
            reaction_resp = await self.client.add_reaction(message_id, "OnIt")
            reaction_id = reaction_resp.get("reaction_id")
        except Exception as e:
            # Emoji 反应失败不影响主流程，但要打日志（别静默吞掉，否则「没表情」极难排查）
            logger.warning("feishu_add_reaction_failed", session_id=session_key, error=str(e))

        # 调用 Agent
        try:
            result = await self._coordinator.run(text, session_id=session_key)
            # Agent 回复常含 Markdown（代码块、加粗、列表），用卡片发送才能正确渲染
            await self.client.send_markdown(receive_id, result.text, id_type=receive_id_type)
        except Exception as e:
            await self.client.send_text(receive_id, f"处理时遇到错误，请稍后重试。", id_type=receive_id_type)
            logger.error("feishu_handle_message_failed", session_id=session_key, error=str(e))
        finally:
            # 移除"处理中"Emoji
            if reaction_id:
                try:
                    await self.client.remove_reaction(message_id, reaction_id)
                except Exception:
                    pass
```

---

## 12.5 WebSocket 管理器 `feishu/ws_manager.py`

> **⚠️ 这一节有三个非踩不可的坑，踩中任意一个都会导致「飞书能发消息，但后端完全收不到」。** 坑 1、坑 3 不报错、极难排查，坑 2 报一句 `This event loop is already running` 也很误导。直接抄下面的最终版本，别按直觉写。

**坑 1：`ws.Client.start()` 是同步阻塞方法，不能 `await`。**
lark SDK 的 `ws.Client.start()` 内部自己 `loop.run_until_complete(...)`，它是一个**同步函数**（返回 `None`）。如果写成 `await ws_client.start()`，等于 `await None` → `TypeError`，**WebSocket 连接根本没建立**。正确做法是放到独立线程里跑，不阻塞 FastAPI 主 loop。

**坑 2：lark 用模块级全局 loop，会和 FastAPI 主 loop 撞车。**
lark 在 `import` 时就执行了 `loop = asyncio.get_event_loop()`（`lark_oapi/ws/client.py` 模块级），在主线程 import 就**抓住了 FastAPI 的主 loop**。即使把 `start()` 丢到线程里，它内部 `loop.run_until_complete(...)` 用的还是这个主 loop，于是报 `This event loop is already running`。正确做法是在工作线程里**新建一个线程私有 loop**，并**覆盖 lark 的模块级 loop 变量**，让 SDK 跑在这个独立 loop 上。

**坑 3：SDK 用同步方式调用事件回调，`async def` 回调会被丢弃。**
SDK 内部是 `processor.do(data)` **同步**调用你的回调，拿到协程对象后**从不 `await`**。所以如果 `_on_message` 写成 `async def`，它的函数体（`handle_event`）**永远不会执行**。正确做法是把 `_on_message` 写成**同步函数**，内部用 `asyncio.run_coroutine_threadsafe(...)` 把异步的 `handle_event` 投递回**主 event loop**执行（handler 用的 Redis/httpx 等资源都绑在主 loop）。

> **两个 loop 各司其职**：lark 的 WebSocket 连接跑在**工作线程的私有 loop**（坑 2），业务处理 `handle_event` 跑在 **FastAPI 主 loop**（坑 3），靠 `run_coroutine_threadsafe` 在两者之间投递。

```python
# feishu/ws_manager.py
"""
飞书 WebSocket 长连接管理器。

飞书的长连接（Long Connection）模式：你的服务主动连接飞书的 WebSocket 服务器，
飞书有新消息时通过这条连接推送过来。

三个必须注意的坑（否则后端收不到消息）：
1. lark SDK 的 ws.Client.start() 是**同步阻塞**方法（内部自己 run_until_complete），
   不能 await，要放到独立线程里跑，否则会阻塞/破坏 FastAPI 主 loop。
2. lark 在 import 时用**模块级全局 loop**（lark_oapi.ws.client.loop = get_event_loop()），
   在主线程 import 就抓住了 FastAPI 的主 loop。直接在线程里跑 start() 会报
   "This event loop is already running"。必须在工作线程里新建一个**线程私有 loop**，
   并覆盖 lark 模块的全局 loop 变量，让 SDK 的 run_until_complete 跑在这个独立 loop 上。
3. SDK 用**同步方式**调用事件回调（processor.do(data)，不会 await 协程）。而我们的
   handle_event 是 async 的，所以回调里要用 run_coroutine_threadsafe 把协程投递回
   主 event loop 执行——直接 async def 回调会被 SDK 丢弃，body 永不执行。
"""
import asyncio
import json

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .client import FeishuClient
from .handler import MessageHandler
from core.config import settings
from observability.logging import logger


class FeishuWSManager:

    def __init__(self):
        self._client_http = FeishuClient()
        self._handler = MessageHandler(self._client_http)
        self._loop: asyncio.AbstractEventLoop | None = None
        # event_id 去重：在同步回调里做（单线程，无并发穿透）。飞书会重推同一条消息，
        # 尤其当回调 ACK 慢的时候——所以既要秒 ACK，也要在这里挡住重推。
        self._seen_events: set[str] = set()

    async def start(self):
        """启动飞书 WebSocket 长连接，开始接收事件。"""
        # 记住主 event loop：SDK 的回调跑在别的线程，要靠它把协程调度回来
        self._loop = asyncio.get_running_loop()

        # 注册事件处理器（无加密：encrypt_key 和 verification_token 传空字符串）
        event_dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .build()
        )

        ws_client = lark.ws.Client(
            settings.feishu_app_id,
            settings.feishu_app_secret,
            event_handler=event_dispatcher,
        )

        logger.info("feishu_ws_connecting")
        # 在独立线程里跑：线程内建一个私有 loop 并覆盖 lark 的模块级 loop，
        # 避免和 FastAPI 主 loop 冲突（This event loop is already running）
        await asyncio.to_thread(self._run_ws_blocking, ws_client)

    def _run_ws_blocking(self, ws_client) -> None:
        """在工作线程里以线程私有 loop 运行 lark 的同步阻塞 start()。"""
        thread_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(thread_loop)
        # 关键：覆盖 lark 模块级全局 loop，让 SDK 内部 run_until_complete 用这个私有 loop
        lark_ws_client.loop = thread_loop
        try:
            ws_client.start()   # 同步阻塞，持续监听
        finally:
            thread_loop.close()

    def _on_message(self, data: P2ImMessageReceiveV1) -> None:
        """
        收到飞书消息事件时的回调（SDK 在独立线程里同步调用）。

        这里是同步函数——SDK 不会 await 协程。关键点：
        - 必须**立刻返回**（不等 handle_event 跑完），否则 lark 迟迟不向飞书 ACK，
          飞书判定推送失败会重推同一条消息 → 一条消息被处理多次。
        - event_id 去重在这里做：本回调是 SDK 单线程串行调用，不会有并发穿透。
        - 用 run_coroutine_threadsafe 把 handle_event 投递回主 loop，但**不 result()**（fire-and-forget）。
        """
        try:
            # P2ImMessageReceiveV1 是对象，先 marshal 成 JSON 再转回 dict，
            # 这样 handler 里 event_data.get("header", {}).get("event_type") 才拿得到值
            payload = json.loads(lark.JSON.marshal(data))

            # event_id 去重：挡住飞书的重推（同步单线程，安全）
            event_id = payload.get("header", {}).get("event_id", "")
            if event_id:
                if event_id in self._seen_events:
                    return
                self._seen_events.add(event_id)
                if len(self._seen_events) > 10000:
                    self._seen_events.clear()

            # fire-and-forget：投递到主 loop 后立刻返回，让 lark 秒 ACK
            asyncio.run_coroutine_threadsafe(
                self._handler.handle_event(payload),
                self._loop,
            )
        except Exception as e:
            logger.error("feishu_event_dispatch_failed", error=str(e))
```

> **为什么 `_on_message` 用 `lark.JSON.marshal(data)` 而不是 `data.to_dict()`？** 回调收到的是 `P2ImMessageReceiveV1` 对象，它的 `header` / `event` 是嵌套对象而非 dict。用 SDK 的 `lark.JSON.marshal()` 序列化成 JSON 字符串、再 `json.loads` 转回 dict，才能得到 `handler.py` 里 `event_data.get("header", {}).get("event_type")` 所依赖的标准结构。

> **⚠️ 为什么回调不能 `future.result()`？** 早期写法在回调里 `future.result()` **阻塞等** `handle_event` 全跑完（调 LLM 可能好几秒）才返回。但 lark 要等回调返回才向飞书 ACK，而**飞书的 ACK 超时只有几秒**——等不到就判定推送失败、**重推同一条消息**，于是「发 1 条被回复 2 次」。所以回调必须 fire-and-forget 秒 ACK，重推则靠上面的 event_id 去重挡住。

---

## 12.6 在 FastAPI 启动时连接飞书

飞书 WebSocket 不是单独的 lifespan，而是**融进第 11 章那个已有的 `lifespan`**——
在 Redis 恢复、常驻 Swarm Agent、后台备份任务都启动之后再拉起，关闭时最先取消。
只有配置了 `feishu_app_id` + `feishu_app_secret` 才启动，没配就整段跳过（不影响
纯 HTTP / CLI 部署）：

```python
# main.py 的 lifespan（在第 11 章版本上补充飞书部分，其余逻辑不变）

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _swarm_save_task

    setup_logging()
    setup_tracing(service_name="my-agent", otlp_endpoint=settings.otel_exporter_otlp_endpoint or None)

    workspace = get_workspace()
    workspace.mkdir(parents=True, exist_ok=True)
    logger.info("server_starting")
    logger.info("workspace_ready", workspace=str(workspace))

    # 1~3. Redis 恢复 / 常驻 Swarm Agent / 后台备份（第 11 章逻辑，此处省略）
    ...

    # 4. 启动飞书 WebSocket（配了 App ID/Secret 才启动），后台任务运行，不阻塞主服务
    feishu_task = None
    if settings.feishu_app_id and settings.feishu_app_secret:
        from feishu.ws_manager import FeishuWSManager
        ws_manager = FeishuWSManager()
        feishu_task = asyncio.create_task(ws_manager.start())
        logger.info("feishu_ws_started")

    yield

    # 5. 收尾：先取消飞书任务，再停 Swarm Agent / 备份任务 / 关闭连接（第 11 章逻辑）
    if feishu_task:
        feishu_task.cancel()
        try:
            await feishu_task
        except asyncio.CancelledError:
            pass

    # ... 停 Swarm Agent、停备份、save_to_redis、close（第 11 章逻辑，此处省略）
    logger.info("server_shutdown")
```

> **两个提醒**：① `asyncio` 已在 `main.py` 顶部 import，不要在 lifespan 里再局部
> `import asyncio`；② 日志用项目统一的 `logger.info("feishu_ws_started")`（结构化 JSON，
> 能被 Loki 采集），不要用 `print`。

---

## 12.7 本章检查清单

```
□ 飞书开放平台已创建应用，App ID 和 App Secret 已填入 .env

□ 飞书应用已启用机器人功能，已申请 im:message 读写权限

□ 服务启动时看到 {"event": "feishu_ws_started", ...} 和 {"event": "feishu_ws_connecting", ...} 的日志

□ 私聊机器人能收到回复

□ （排查「发消息后端完全收不到」）先自查 12.5 的三个坑：
  - ws_client.start() 是否误写成 await（应放到独立线程跑，不 await）
  - 启动是否报 "This event loop is already running"（应在工作线程建私有 loop
    并覆盖 lark_ws_client.loop）
  - _on_message 是否误写成 async def（应为同步函数 + run_coroutine_threadsafe）

□ 群聊 @机器人能收到回复

□ 发消息时出现"处理中"👌 Emoji（OnIt），回复后消失
  （如果没出现：看日志有没有 231001 reaction type is invalid——emoji_type 用错了；
   emoji_type 是大小写敏感的固定枚举，见 12.3 的合法值说明）

□ Agent 回复的 Markdown 正确渲染（加粗、代码块、列表显示为格式，而非字面量）
  （用 send_markdown 走 interactive 卡片；若显示成 **加粗** 字面量，说明还在用 send_text）

□ /help 命令返回帮助文字

□ /clear 命令清除会话历史，下次对话 Agent 不再记得之前内容

□ event_id 去重生效（飞书重推同一条消息，只处理一次）——去重在 12.5 的同步回调里做

□ 连发多条消息不乱序、不重复回复：
  - 快速连发 3 条不同消息，只回复最新那一条（latest-wins 防抖，见 12.4 的 _DEBOUNCE_SEC）
  - 不会出现「发 3 条回 4 条」（说明回调已 fire-and-forget、飞书不再因 ACK 超时重推）

□ 回调不阻塞：_on_message 里没有 future.result()（否则 ACK 慢会触发飞书重推）
```

---

# 第 13 章：阶段 13 —— 容器化部署

> **本章目标**：打包成 Docker 镜像，一条命令启动整个服务（包括 Redis），服务器上稳定运行。

---

## 13.1 本章新增文件

```
my-agent/
├── Dockerfile
├── .dockerignore        ← 构建时排除 .venv / 缓存等，避免污染镜像、加快构建
├── docker-compose.yml
├── prometheus.yml       ← Prometheus 抓取配置
├── tempo.yaml           ← Tempo 链路存储配置
├── promtail-config.yaml ← Promtail 日志采集配置（推给 Loki）
├── grafana/
│   └── provisioning/
│       └── datasources/
│           └── datasources.yaml   ← Grafana 数据源自动配置（Prometheus + Tempo + Loki）
├── entrypoint.sh
└── .env.docker          ← Docker 部署用的唯一环境配置（本地开发用 .env）
```

---

## 13.2 核心概念讲解

### Docker 和 Docker Compose 是什么？

**Docker**：把你的应用和它所需的所有依赖打包成一个"集装箱"（镜像）。
这个集装箱在任何装了 Docker 的机器上运行，不受操作系统差异影响。
就像真实的集装箱：不管运到哪个港口，箱子里的货物不变。

**Docker Compose**：用一个 YAML 文件定义多个容器（服务）之间的关系，一条命令启动所有服务。
我们需要两个服务：
1. `agent`：你的 Python 应用
2. `redis`：Redis 数据库

**v2 和 v1 的区别**：v2 不再依赖 Node.js，所以 Dockerfile 比 v1 简洁很多，镜像体积也更小。

---

## 13.3 `Dockerfile`

本项目用 **uv** 管理依赖（`pyproject.toml` + `uv.lock`），所以 Dockerfile 基于 uv 官方镜像、用 `uv sync` 按锁文件精确安装，而不是 `pip install requirements.txt`。这样容器里装的依赖版本和本地 `uv.lock` 完全一致，也不用再单独维护 `requirements.txt`。

```dockerfile
# Dockerfile

# 使用 uv 官方镜像（自带 uv + Python 3.14，和 .python-version / pyproject 的 requires-python 一致）
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

# 设置工作目录
WORKDIR /app

# 设置 Python 不生成 .pyc 文件（容器里不需要）；输出不缓冲（日志实时可见）
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# 用国内镜像加速依赖下载
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 先只复制依赖清单，利用 Docker 层缓存（依赖没变时不重装）
COPY pyproject.toml uv.lock ./

# 按 uv.lock 精确安装依赖到 .venv（--frozen 不改锁文件；--no-install-project 此时还没复制项目代码；
# --no-dev 不装 dev 依赖组，如 pytest）
RUN uv sync --frozen --no-install-project --no-dev

# 再复制应用代码（代码改动时只需重新构建这一层，依赖层缓存有效）
COPY . .

# 把项目本身也装进环境（此时代码已就位）
RUN uv sync --frozen --no-dev

# 暴露服务端口（声明意图，实际映射在 docker-compose.yml 里）
EXPOSE 8002

# 给启动脚本加执行权限
RUN chmod +x entrypoint.sh

# 启动命令
ENTRYPOINT ["./entrypoint.sh"]
```

> **两次 `uv sync` 是为了层缓存**：第一次只装第三方依赖（代码没复制，改代码不会让这层失效）；第二次在复制代码后把项目本身装进环境。依赖没变时，第一次那层直接命中缓存，重建镜像很快。
>
> **`uv run` 前缀**：依赖装在 `.venv` 里，所以 `entrypoint.sh` 启动命令要用 `uv run uvicorn ...`（见 13.4），让 uv 用虚拟环境里的 uvicorn；直接 `uvicorn ...` 会找不到命令。

### 13.3.1 `.dockerignore`

`COPY . .` 会把整个项目目录复制进镜像。本地的 `.venv/`（可能几百 MB，且是为本机 Python 构建的，进容器无用甚至冲突）、`__pycache__`、`.git` 等都不该进镜像——用 `.dockerignore` 排除，既加快构建又减小镜像：

```gitignore
# .dockerignore
.venv/
__pycache__/
*.pyc
.git/
.pytest_cache/
sessions/
workspace/
logs/
.env
.env.*
*.md
.vscode/
```

> **关键是排除 `.venv/`**：uv 在容器内会用 `uv sync` 重新创建 `.venv`，本地那份必须挡在镜像外，否则可能覆盖容器内正确的依赖。`.env*` 也排除——密钥通过 docker-compose 的 `env_file` 注入，不打进镜像。

---

## 13.4 `entrypoint.sh`

```bash
#!/bin/bash
# entrypoint.sh — 容器启动脚本

set -e   # 任何命令失败就退出

echo "=== My Agent 服务启动 ==="
echo "Provider：${LLM_PROVIDER:-anthropic}"

# 等待 Redis 就绪（最多等 30 秒）
# 用 uv run python + redis 库探活，不依赖 redis-cli（uv slim 镜像没装它）
if [ -n "${REDIS_HOST}" ]; then
    echo "等待 Redis 就绪..."
    for i in $(seq 1 30); do
        if uv run python -c "import redis, sys; sys.exit(0 if redis.Redis(host='${REDIS_HOST:-redis}', port=${REDIS_PORT:-6379}).ping() else 1)" > /dev/null 2>&1; then
            echo "Redis 已就绪"
            break
        fi
        sleep 1
    done
fi

# 单 Worker + 热重载启动（uv run 会用 .venv 里的 uvicorn）
# 单 worker 也避免了飞书 WebSocket 在多 worker 下各连一次导致的重复处理
echo "启动：单 Worker，热重载开启"
exec uv run uvicorn main:app \
    --host 0.0.0.0 \
    --port 8002 \
    --reload
```

> **为什么 Redis 探活不用 `redis-cli`？** uv 的 slim 镜像里没有 `redis-cli` 这个命令行工具，而项目依赖里本来就有 `redis` 库，所以直接 `uv run python -c "...redis.Redis(...).ping()"` 探活，不用额外 `apt install redis-tools`。

---

## 13.5 `docker-compose.yml`

```yaml
# docker-compose.yml

version: "3.9"

services:

  # ── 你的 Agent 服务 ──────────────────────────────────────────────
  agent:
    build:
      context: .
      dockerfile: Dockerfile
    image: my-agent:latest
    ports:
      - "${APP_PORT:-8002}:8002"    # 主机端口:容器端口
    env_file:
      - .env.docker                 # 唯一的容器环境配置（不再分 dev/prod）
    depends_on:
      redis:
        condition: service_healthy  # 等 Redis 健康检查通过后才启动 agent
    restart: unless-stopped         # 除非手动停止，否则崩溃后自动重启
    volumes:
      - ./sessions:/app/sessions    # 把会话文件映射到主机，重建容器不丢失
      - ./workspace:/app/workspace  # 工作目录（WORKSPACE）映射到主机，工具产出不丢失

  # ── Redis 服务 ────────────────────────────────────────────────────
  redis:
    image: redis:7-alpine           # 使用 Alpine 版本，体积小
    command: redis-server --save 60 1 --loglevel warning  # 60 秒内有 1 次写入就持久化
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5
    restart: unless-stopped
    volumes:
      - redis_data:/data            # Redis 数据持久化（容器重建不丢失）

  # ── Prometheus 监控 ──────────────────────────────────────────────
  prometheus:
    image: prom/prometheus:latest
    container_name: prometheus
    ports:
      - "9090:9090"
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
    restart: unless-stopped

  # ── Grafana 可视化（一处看齐：指标 Prometheus + 链路 Tempo + 日志 Loki）─
  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_USER=admin
      - GF_SECURITY_ADMIN_PASSWORD=admin
      # 开启 Tempo 相关特性（trace 到 metrics 的跳转）
      - GF_FEATURE_TOGGLES_ENABLE=traceToMetrics
    volumes:
      - grafana_data:/var/lib/grafana
      # 数据源自动配置：容器启动时自动连好 Prometheus / Tempo / Loki，无需手动点界面
      - ./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources
    restart: unless-stopped
    depends_on:
      - prometheus
      - tempo
      - loki

  # ── Tempo 链路追踪（存储后端，UI 走 Grafana）─────────────────────
  tempo:
    image: grafana/tempo:latest
    container_name: tempo
    command: ["-config.file=/etc/tempo.yaml"]
    ports:
      - "4317:4317"                  # OTLP gRPC 接收端口（你的服务 push span 到这里）
      - "3200:3200"                  # Tempo HTTP API（Grafana 数据源查询用）
    volumes:
      - ./tempo.yaml:/etc/tempo.yaml
      - tempo_data:/var/tempo
    restart: unless-stopped

  # ── Loki 日志存储（存储后端，UI 走 Grafana）─────────────────────
  loki:
    image: grafana/loki:latest
    container_name: loki
    command: ["-config.file=/etc/loki/local-config.yaml"]  # 用镜像自带默认配置即可
    ports:
      - "3100:3100"                  # Loki HTTP API（Promtail 推日志 + Grafana 查询都走这里）
    volumes:
      - loki_data:/loki
    restart: unless-stopped

  # ── Promtail 日志采集器（读容器 stdout，推给 Loki）──────────────
  promtail:
    image: grafana/promtail:latest
    container_name: promtail
    command: ["-config.file=/etc/promtail/config.yaml"]
    volumes:
      - ./promtail-config.yaml:/etc/promtail/config.yaml
      # 读取宿主机上的容器日志（Docker 把每个容器 stdout 写成 json-file）
      - /var/lib/docker/containers:/var/lib/docker/containers:ro
      - /var/run/docker.sock:/var/run/docker.sock:ro
    restart: unless-stopped
    depends_on:
      - loki

volumes:
  redis_data:
  prometheus_data:
  grafana_data:
  tempo_data:
  loki_data:
```

> **镜像清单**（共 7 个）：`ghcr.io/astral-sh/uv:python3.14-bookworm-slim`（Dockerfile FROM，uv + Python 3.14） + `redis:7-alpine`（会话缓存） + `prom/prometheus:latest`（指标抓取） + `grafana/grafana:latest`（统一可视化） + `grafana/tempo:latest`（链路存储） + `grafana/loki:latest`（日志存储） + `grafana/promtail:latest`（日志采集）。其中 `my-agent:latest` 是通过 Dockerfile `build` 生成的，不是外部镜像。
>
> **Tempo / Loki 都没有独立 UI**：它们只是存储后端，查看统一走 Grafana。Tempo 收链路（4317）、供查询（3200）；Loki 收日志 + 供查询都走同一个端口（3100）。
>
> **Promtail 为什么要挂载 docker.sock 和 containers 目录？** Docker 默认把每个容器的 stdout 写成宿主机上的 json-file 日志文件，Promtail 需要读到这些文件、并通过 `docker.sock` 拿到容器名/标签，才能把日志打上 `container` 标签推给 Loki。两个挂载都是**只读（`:ro`）**，Promtail 不会改动它们。

**项目根目录下创建 `prometheus.yml`**（与 `docker-compose.yml` 同级）：

```yaml
# prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "polycoder"
    scrape_interval: 15s
    static_configs:
      - targets: ["agent:8002"]       # Docker 网络内用服务名访问
        labels:
          service: "polycoder"
```

> **targets 说明**：docker-compose 内所有服务共享同一个 Docker 网络，所以这里直接填服务名 `agent:8002`，不需要 `host.docker.internal`。

---

### 13.5.1 `tempo.yaml`（Tempo 配置，与 `docker-compose.yml` 同级）

Tempo 需要一个配置文件告诉它「从哪个端口收链路」「存到哪里」。下面是一份最小可用的单机配置（本地文件存储，不接对象存储）：

```yaml
# tempo.yaml
server:
  http_listen_port: 3200          # Grafana 数据源查询走这个端口

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: "0.0.0.0:4317"   # 接收你的服务 push 过来的 OTLP gRPC 链路

ingester:
  max_block_duration: 5m

compactor:
  compaction:
    block_retention: 24h          # 链路数据保留 24 小时（本地开发够用）

storage:
  trace:
    backend: local                # 本地磁盘存储（生产可换成 s3/gcs）
    local:
      path: /var/tempo/blocks
    wal:
      path: /var/tempo/wal
```

> **端口对应关系**：你的服务把 Span 推到 `tempo:4317`（OTLP gRPC，见 `.env.docker` 里的 `OTEL_EXPORTER_OTLP_ENDPOINT`），Grafana 查询链路时连的是 `tempo:3200`（HTTP API）。两个端口各司其职，别搞混。

---

### 13.5.2 `promtail-config.yaml`（日志采集配置，与 `docker-compose.yml` 同级）

Loki 用镜像自带的默认配置就能跑，不需要额外文件。真正要配的是采集器 **Promtail**——告诉它「读哪里的日志」「推给谁」。下面这份配置用 Docker 服务发现，自动采集所有容器的 stdout：

```yaml
# promtail-config.yaml
server:
  http_listen_port: 9080

positions:
  filename: /tmp/positions.yaml   # 记录读到哪了，重启不重复采集

clients:
  - url: http://loki:3100/loki/api/v1/push   # 推给 Loki（Docker 网络内用服务名）

scrape_configs:
  - job_name: docker
    docker_sd_configs:              # 通过 docker.sock 自动发现所有容器
      - host: unix:///var/run/docker.sock
        refresh_interval: 5s
    relabel_configs:
      # 把容器名提取成 container 标签，查询时可按容器过滤
      - source_labels: ["__meta_docker_container_name"]
        regex: "/(.*)"
        target_label: container
```

> **验证日志被 JSON 解析**：本项目日志本身就是 JSON（第 11 章），进入 Grafana 后在 Loki 查询里加一个 `| json` 管道（见 13.7.1），就能把 `session_id`、`event`、`level` 等字段拆出来单独过滤。Promtail 这里不需要预先解析 JSON，交给查询时按需展开更灵活。

---

### 13.5.3 Grafana 数据源自动配置（免手动点界面）

第 11 章检查清单里「打开 Grafana 手动添加 Prometheus 数据源」这一步，可以用 Grafana 的 **provisioning** 机制自动完成——把数据源写进一个 YAML 文件，Grafana 启动时自动加载。这样 `docker compose up` 之后，Prometheus、Tempo、Loki 三个数据源就已经连好了。

**创建 `grafana/provisioning/datasources/datasources.yaml`**（注意目录层级，`docker-compose.yml` 里挂载的就是这个路径）：

```yaml
# grafana/provisioning/datasources/datasources.yaml
apiVersion: 1

datasources:
  # ── 指标：Prometheus ──────────────────────────────────────────
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090      # Docker 网络内用服务名
    isDefault: true

  # ── 链路：Tempo ───────────────────────────────────────────────
  - name: Tempo
    type: tempo
    access: proxy
    url: http://tempo:3200           # 连 Tempo 的 HTTP 查询端口
    jsonData:
      # 从链路 Span 跳到对应指标（可选，增强联动）
      tracesToMetrics:
        datasourceUid: prometheus
      # 服务依赖图需要的节点/边指标
      serviceMap:
        datasourceUid: prometheus

  # ── 日志：Loki ────────────────────────────────────────────────
  - name: Loki
    type: loki
    access: proxy
    url: http://loki:3100            # 连 Loki 的 HTTP 查询端口
```

> **目录结构**：完成后项目根目录多出这些配置文件：
> ```
> my-agent/
> ├── docker-compose.yml
> ├── prometheus.yml
> ├── tempo.yaml
> ├── promtail-config.yaml
> └── grafana/
>     └── provisioning/
>         └── datasources/
>             └── datasources.yaml
> ```
> Grafana 官方约定 `provisioning/datasources/` 下的所有 YAML 都会在启动时被读取，无需在文件名上做特殊约定。

---

## 13.6 环境变量文件

Docker 部署只用**一个** `.env.docker`（不再分 dev/prod）。本地开发仍用 `.env`，两者互不干扰。

```dotenv
# .env.docker — Docker 部署用的唯一环境配置（本地开发用 .env）

# ── LLM Provider（示例用 DeepSeek，走 openai 兼容接口；用 Claude 就填 anthropic）─
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-v4-pro
LLM_MODEL=claude-sonnet-4-6   # 仅 anthropic provider 时生效

APP_PORT=8002

# ── 飞书机器人（第 12 章）──────────────────────────────────────────
FEISHU_APP_ID=cli_...
FEISHU_APP_SECRET=...

# ── Redis（Docker 网络内用服务名 redis 访问）──────────────────────
REDIS_HOST=redis
REDIS_PORT=6379
# 如需密码，取消注释并给 docker-compose.yml 的 redis 服务加 --requirepass
# REDIS_PASSWORD=your_strong_password_here

# ── 工作目录（第 0 章）——容器内所有工具/子 Agent 的文件操作都限定在此目录内 ─
WORKSPACE=/app/workspace

# ── OpenTelemetry 链路追踪导出地址（第 11 章）——推送到 Tempo 的 OTLP gRPC 端口 ─
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
```

> **Docker 里的工作目录**：容器 `WORKDIR` 是 `/app`（第 13.3 节 Dockerfile），这里把工作区设为 `/app/workspace` 与代码目录分开。13.5 的 agent 服务已加卷映射 `./workspace:/app/workspace`（和 `./sessions:/app/sessions` 同理），工具产出的文件会落到主机的 `./workspace`，容器重建也不丢失。
>
> **`REDIS_HOST=redis`**：容器间用 compose 里的服务名 `redis` 互访，不是 `localhost`（那是本地 `.env` 的写法）。

**在 `.gitignore` 里排除这个文件：**

```
.env.docker
```

---

## 13.7 构建和启动命令

```bash
# 启动（前台，看日志）
docker compose up --build

# 后台启动
docker compose up -d --build

# 查看实时日志
docker compose logs -f agent

# 查看服务状态（确认 redis 显示 healthy）
docker compose ps

# 停止所有服务
docker compose down

# 停止并删除数据卷（完全清除，慎用）
docker compose down -v
```

**验证部署成功：**

```bash
# 健康检查
curl http://localhost:8002/health

# 发送测试请求
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "你好"}'
```

---

## 13.7.1 在 Grafana 里查看指标、链路和日志

启动后所有可观测性数据都在一个 Grafana 界面查看，登录信息：`http://localhost:3000`，账号密码都是 `admin`（首次登录会要求改密码，本地可跳过）。

**① 确认数据源已自动连好**

`Connections → Data Sources`，应该看到 provisioning 自动创建的三个数据源：
- **Prometheus**（`http://prometheus:9090`）——指标
- **Tempo**（`http://tempo:3200`）——链路
- **Loki**（`http://loki:3100`）——日志

各自点进去拉到底 `Save & test`，看到绿色 "successfully" 即连接正常。如果这里是空的，说明 `grafana/provisioning/datasources/datasources.yaml` 没挂载进去，检查 `docker-compose.yml` 的 volumes 路径和文件是否存在，然后 `docker compose restart grafana`。

**② 查看链路（Tempo）**

先发几次 `/ask` 请求产生链路数据，然后：

1. 左侧菜单 `Explore`
2. 顶部数据源下拉选 **Tempo**
3. 查询模式选 `Search`，Service Name 选 `my-agent`（对应 `tracing.py` 里的 `service.name`），点 `Run query`
4. 结果列表里点任意一条 trace，右侧展开火焰图，能看到完整链路：`agent_loop → turn_0 → tool_xxx → turn_1 → ...`，每段的耗时一目了然

> 如果 Search 标签页是灰的/不可用，用 `TraceQL` 模式输入 `{}` 也能列出最近的全部链路。

**③ 查看指标（Prometheus）**

同样在 `Explore`，数据源切到 **Prometheus**，输入指标名验证打点是否生效，例如：
- `agent_requests_total` —— 请求总数（按 provider/model/status 分类）
- `rate(agent_latency_seconds_sum[5m]) / rate(agent_latency_seconds_count[5m])` —— 平均延迟
- `active_requests` —— 当前活跃请求数

需要长期看板可以在 `Dashboards → New` 里把这些指标做成图表面板。

**④ 查看日志（Loki）**

同样在 `Explore`，数据源切到 **Loki**，用 LogQL 查询。因为本项目日志是 JSON，加 `| json` 就能把字段拆出来精确过滤：

```logql
# 只看 agent 容器的日志
{container="agent"}

# 拆出 JSON 字段后，只看 warning 及以上级别
{container="agent"} | json | level=~"warning|error"

# 追踪某一次请求的完整日志（就是上一个问题里排查时间线的做法）
{container="agent"} | json | session_id="49da725d-176f-4e35-8f6c-e2cb3bfa379a"

# 只看 Redis 连接失败
{container="agent"} | json | event=~"session_store_redis.*"
```

> **三件套联动**：因为日志、链路、指标都在同一个 Grafana，排查问题时可以从一条指标异常（Prometheus 看到延迟飙高）→ 跳到对应时间段的日志（Loki 按 `session_id` 过滤）→ 再跳到那次请求的链路火焰图（Tempo 看哪个 Span 最慢），一条龙定位根因。

需要长期看板可以在 `Dashboards → New` 里把指标图表和日志面板放到同一个 dashboard。

---

## 13.8 常见部署问题

**Q：docker compose up 时报 "Permission denied" 关于 entrypoint.sh？**

A：执行权限没有设置。在容器构建时已经 `chmod +x entrypoint.sh`，但如果你在 Windows 上编辑了这个文件，可能丢失执行权限。解决：
```bash
git update-index --chmod=+x entrypoint.sh
```

**Q：agent 服务启动但立刻退出，查看日志显示 "Can't connect to Redis"？**

A：Redis 还没有就绪。检查：
1. `docker compose ps` 里 redis 服务是否显示 healthy
2. entrypoint.sh 里的等待 Redis 逻辑是否正常执行

**Q：API Key 配置了但服务返回认证错误？**

A：检查 `.env.docker` 文件是否在 docker compose 的 `env_file` 里，以及 Key 格式是否正确（没有额外空格）。

**Q：Grafana 里 Tempo 数据源没有自动出现 / 测试连接失败？**

A：分两种情况：
1. **数据源根本没出现**：provisioning 文件没挂载进去。确认 `grafana/provisioning/datasources/datasources.yaml` 存在，且 `docker-compose.yml` 里 grafana 的 volumes 有 `./grafana/provisioning/datasources:/etc/grafana/provisioning/datasources`，然后 `docker compose restart grafana`。
2. **出现了但测试失败**：URL 要填 `http://tempo:3200`（HTTP 查询端口），不是 `4317`（那是 OTLP 接收端口，Grafana 连它会失败）。

**Q：发了请求但 Grafana Explore 里搜不到链路？**

A：按顺序排查：
1. `.env.docker` 里 `OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317` 是否配置（不配就不导出，见第 11 章）。
2. 依赖 `opentelemetry-exporter-otlp-proto-grpc` 是否装了（没装的话 `tracing.py` 会打印 "未安装...链路数据不导出"）。
3. `docker compose logs tempo` 看 Tempo 是否正常启动、有没有报 `tempo.yaml` 配置错误。
4. Tempo 有几秒的 ingester 缓冲，发完请求稍等几秒再刷新查询。

**Q：Grafana Loki 里查不到任何日志？**

A：日志链路是 `agent 打到 stdout → Promtail 采集 → 推给 Loki`，逐段排查：
1. `docker compose logs agent` 本身能看到 JSON 日志吗？看不到说明 agent 没往 stdout 打日志（检查 `setup_logging()` 是否调用）。
2. `docker compose logs promtail` 有没有报错？常见是挂载路径不对——确认 `/var/lib/docker/containers` 和 `/var/run/docker.sock` 都挂进去了。
3. Loki 数据源 URL 要填 `http://loki:3100`；LogQL 至少要有一个标签选择器，直接输 `| json` 会报错，正确写法是 `{container="agent"} | json`。
4. 容器名标签可能不是 `agent` 而是 `my-agent-agent-1` 之类，先用 `{container=~".+"}` 列出全部，确认实际标签值再精确过滤。

**Q：Promtail 挂载 docker.sock 报权限错误？**

A：宿主机上 `/var/run/docker.sock` 属于 root/docker 组。本指南容器以默认用户跑、且用户是 root，通常能读；如果宿主机做了严格权限隔离，可给 promtail 服务加 `user: root` 或把当前用户加入 docker 组。

---

## 13.9 本章检查清单

```
□ Dockerfile 构建成功（docker build -t my-agent . 无报错）

□ docker compose up 能正常启动（7 个服务都变成 running：
  agent + redis + prometheus + grafana + tempo + loki + promtail）

□ redis 服务健康检查通过（docker compose ps 显示 healthy）

□ curl http://localhost:8002/health 返回 200

□ curl http://localhost:8002/metrics 能拉到指标数据（如 agent_requests_total）

□ 打开 http://localhost:9090 → Status → Targets，polycoder 状态为 UP

□ 打开 http://localhost:3000 → Connections → Data Sources，Prometheus / Tempo / Loki 三个数据源
  已自动出现（provisioning 生效），各自点 "Test" 均通过

□ 打开 http://localhost:3000 → Explore → 选择 Tempo 数据源 → Search，能搜到链路数据
  （需先发几次 /ask 请求；Tempo 没有独立 UI，链路只在 Grafana 里看）

□ 打开 http://localhost:3000 → Explore → 选择 Loki 数据源 → 输入 {container="agent"} | json，
  能看到结构化日志，且可按 session_id / level / event 字段过滤

□ 发送问题能得到回答（end-to-end 验证）

□ docker compose down 然后再 up，会话历史仍然存在（volumes 持久化有效）

□ 启动日志出现「启动：单 Worker，热重载开启」（单一环境，单 worker + reload）
```

---

# 第 14 章：定制化指南

> **本章目标**：把这个框架改造成你自己的业务 Agent。清单式指导，每步只改一个地方。

---

## 14.1 六处核心替换点

| 序号 | 文件 | 替换内容 | 说明 |
|------|------|---------|------|
| 1 | `core/config.py` | Provider 和模型名 | 选择你的 LLM 和模型 |
| 2 | `agent.py` 的 `SYSTEM_PROMPT` | 系统提示词 | 定义 Agent 的角色和行为准则 |
| 3 | `tools/builtin/` 下新建工具文件 | 业务工具实现 | 调用你的 API、数据库、内部服务 |
| 4 | `skills/` 目录 | SKILL.md 文件 | 按业务领域拆分专业知识 |
| 5 | `coordinator/planner.py` 的系统提示词 | 可用子 Agent 描述 | 定义你的业务流程和 Agent 团队 |
| 6 | `feishu/handler.py` 内置命令 | 添加业务快捷命令 | 如 /status、/report |

---

## 14.2 系统提示词写作指南

好的系统提示词包含四个部分：

```markdown
## 角色定义（你是谁）

你是 [公司名] 的 [角色名]，专注于 [核心职责]。

## 能力边界（能做什么 / 不能做什么）

你能：
- [能力 1]
- [能力 2]

遇到以下情况，请直接告知用户你无法处理，不要尝试：
- [边界 1]
- [边界 2]

## 工具使用规范（什么时候用什么工具）

- 用户询问 [场景 A] 时，调用 [工具 A]
- 用户询问 [场景 B] 时，调用 [工具 B]
- 如果不确定该用哪个工具，先询问用户确认

## 输出格式（结果应该是什么样）

回答时遵循以下格式：
[具体格式规范，最好有示例]

## 特殊规则（业务特定要求）

- [规则 1]：[原因和做法]
- [规则 2]
```

---

## 14.3 如何添加新 Provider

实现 `BaseProvider` 接口（`providers/base.py`），然后在 `providers/router.py` 的 `get_provider()` 里添加分支：

```python
# providers/router.py — 添加新 Provider
elif name == "my_llm":
    from providers.my_llm import MyLLMProvider
    return MyLLMProvider(
        api_key=settings.my_llm_api_key,
        model=settings.my_llm_model,
    )
```

同时在 `core/config.py` 里加对应的配置字段：

```python
class Settings(BaseSettings):
    # 新 Provider 的配置
    my_llm_api_key: str = ""
    my_llm_model: str = "my-model-v1"
```

---

## 14.4 不同数据源的工具实现参考

### 调用 REST API

```python
class MyRestApiTool(BaseTool):
    @property
    def name(self) -> str:
        return "query_my_api"

    @property
    def description(self) -> str:
        return "查询公司内部系统的数据，当用户询问 [具体业务] 时使用。"

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "resource_id": {
                    "type": "string",
                    "description": "资源 ID，格式：PROJ-XXXX"
                }
            },
            "required": ["resource_id"],
        }

    async def execute(self, inputs: dict) -> str:
        resource_id = inputs.get("resource_id", "").strip()
        if not resource_id:
            return "错误：resource_id 不能为空"

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    f"https://your-internal-api.company.com/resources/{resource_id}",
                    headers={"Authorization": f"Bearer {settings.internal_api_token}"},
                )
                resp.raise_for_status()
                data = resp.json()

                # 只返回 LLM 需要的字段
                return json.dumps({
                    "id": data["id"],
                    "name": data["name"],
                    "status": data["status"],
                    "owner": data["owner"]["name"],
                }, ensure_ascii=False)

        except httpx.TimeoutException:
            return "错误：内部 API 请求超时（15秒），服务可能繁忙"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return f"错误：资源 {resource_id} 不存在"
            return f"错误：内部 API 返回 {e.response.status_code}"
        except Exception as e:
            return f"查询失败：{e}"
```

### 查询 MySQL 数据库

```python
import aiomysql

class MySQLQueryTool(BaseTool):
    @property
    def name(self) -> str:
        return "query_database"

    async def execute(self, inputs: dict) -> str:
        table = inputs.get("table", "")
        filters = inputs.get("filters", {})

        # 重要：使用参数化查询，防止 SQL 注入
        # 不要：f"SELECT * FROM {table} WHERE name='{name}'"
        # 要：使用参数化查询

        where_clause = " AND ".join(f"{k} = %s" for k in filters.keys())
        values = list(filters.values())
        sql = f"SELECT * FROM {table}"
        if where_clause:
            sql += f" WHERE {where_clause}"
        sql += " LIMIT 10"

        try:
            conn = await aiomysql.connect(
                host=settings.db_host,
                user=settings.db_user,
                password=settings.db_password,
                db=settings.db_name,
            )
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(sql, values)
                rows = await cursor.fetchall()
            conn.close()

            if not rows:
                return f"查询 {table} 表无结果"
            return json.dumps(rows, ensure_ascii=False, default=str)

        except Exception as e:
            return f"数据库查询失败：{e}"
```

### 读取本地文件

```python
import aiofiles

class FileReaderTool(BaseTool):
    @property
    def name(self) -> str:
        return "read_document"

    async def execute(self, inputs: dict) -> str:
        file_path = inputs.get("file_path", "").strip()
        if not file_path:
            return "错误：file_path 不能为空"

        # 安全检查：只允许访问指定目录
        allowed_base = Path("/app/documents")
        full_path = (allowed_base / file_path).resolve()
        if not str(full_path).startswith(str(allowed_base)):
            return "错误：不允许访问此路径（安全限制）"

        if not full_path.exists():
            return f"错误：文件不存在：{file_path}"

        try:
            async with aiofiles.open(full_path, "r", encoding="utf-8") as f:
                content = await f.read()
            if len(content) > 10000:
                content = content[:10000] + f"\n...(文件过长，已截断，完整大小：{len(content)} 字符)"
            return content
        except UnicodeDecodeError:
            return "错误：文件编码不是 UTF-8，无法读取"
        except Exception as e:
            return f"读取失败：{e}"
```

---

## 14.5 如何增加第三个专家 Agent

以"数据查询专家"为例：

**第一步**：创建子 Agent 文件：

```python
# sub_agents/data_query.py
from .base import SubAgent

class DataQueryAgent(SubAgent):
    @property
    def name(self) -> str:
        return "data_query"

    @property
    def system_prompt(self) -> str:
        return "你是数据查询专家，负责查询数据库并以清晰的格式展示数据..."
```

**第二步**：在 `coordinator/dispatcher.py` 的 `_get_sub_agents()` 里注册：

```python
def _get_sub_agents() -> dict:
    from sub_agents.code_review import CodeReviewAgent
    from sub_agents.data_query import DataQueryAgent   # 新增

    return {
        "code_review": CodeReviewAgent(),
        "data_query": DataQueryAgent(),   # 新增
    }
```

**第三步**：更新 Coordinator 的系统提示词（在 `coordinator/planner.py` 的 `_COORDINATOR_SYSTEM` 里）：

```
可用的子 Agent：
- code_review：代码审查...
- data_query：数据查询，当用户需要查询数据库或生成报表时使用  ← 新增
```

只需这三步，Coordinator 就能把数据查询任务路由到新 Agent。

---

## 14.6 常见定制化场景

### 场景 1：企业内部知识库问答

1. 用 `read_document` 工具读取内部文档（Confluence、飞书文档）
2. 用 `search_knowledge_base` 工具搜索向量数据库（Qdrant、Pinecone）
3. 系统提示词：只回答和公司业务相关的问题，不解答无关问题

### 场景 2：智能客服

1. 用 `query_order` 工具查询订单状态
2. 用 `get_faqs` 工具搜索常见问题
3. 用 `create_ticket` 工具创建客服工单
4. 系统提示词：始终保持专业友好，遇到投诉优先安抚

### 场景 3：代码助手

1. 用 `read_file` / `write_file` / `run_command` 工具
2. Skills：code-review.md、python-best-practices.md、security-guidelines.md
3. 系统提示词：遵循团队的代码规范，修改前先读取现有代码

---

## 14.7 本章检查清单

```
□ 系统提示词已替换为你的业务场景，包含角色、能力边界、工具使用规范

□ 至少实现了一个业务工具（继承 BaseTool，execute() 调用真实 API 或数据库）

□ 工具注册到了 ToolRegistry.default() 里

□ 发送业务相关问题，Agent 能调用工具并给出正确回答

□ SKILL.md 文件已按业务领域拆分，name+description 进技能索引表，LLM 能用 get_skill_guide 按需加载

□ （如果用多 Agent）新的子 Agent 已注册到 dispatcher，Coordinator 能正确路由

□ 飞书机器人内置命令已更新（帮助文本显示你的业务 Agent 的能力）
```

---

# 附录 A：最终目录结构

```
my-agent/
├── .env（本地开发） / .env.docker（Docker 部署）
├── .gitignore
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── entrypoint.sh
├── main.py                 ← FastAPI 入口
├── cli.py                  ← 命令行入口
│
├── core/                   ← 阶段 0：全局配置
│   ├── __init__.py
│   └── config.py
│
├── providers/              ← 阶段 3：Provider 抽象层
│   ├── __init__.py
│   ├── base.py
│   ├── types.py
│   ├── router.py
│   ├── anthropic.py
│   ├── openai.py
│   └── gemini.py
│
├── agent_core/              ← 阶段 4：Agentic Loop（后期从 agent/ 改名而来，见 4.6 更新说明）
│   ├── __init__.py
│   ├── loop.py
│   ├── state.py
│   ├── executor.py
│   └── context.py          ← 阶段 8：上下文压缩
│
├── tools/                  ← 阶段 5：工具系统
│   ├── __init__.py
│   ├── base.py
│   ├── registry.py
│   └── builtin/
│       ├── __init__.py
│       └── calculator.py
│
├── coordinator/            ← 阶段 6：Coordinator 模式
│   ├── __init__.py
│   ├── agent.py
│   ├── planner.py
│   └── dispatcher.py
│
├── sub_agents/             ← 阶段 6：子 Agent
│   ├── __init__.py
│   ├── base.py
│   └── code_review.py
│
├── swarm/                  ← 阶段 7：Swarm 模式
│   ├── __init__.py
│   ├── blackboard.py
│   └── agent_base.py
│
├── skills/                 ← 阶段 9：Skills 系统
│   ├── loader.py           ← 解析 SKILL.md + build_skill_index()
│   ├── skill_tool.py       ← get_skill_guide 工具（LLM 按需加载）
│   └── code-review.md
│
├── persistence/            ← 阶段 10：会话持久化
│   └── session_store.py
│
├── observability/          ← 阶段 11：可观测性
│   ├── logging.py
│   ├── metrics.py
│   └── tracing.py
│
├── feishu/                 ← 阶段 12：飞书集成
│   ├── client.py
│   ├── handler.py
│   └── ws_manager.py
│
├── sessions/               ← 会话 JSONL 文件（运行时生成，不提交 Git）
│
└── tests/
    ├── test_stage1.py
    ├── test_stage2.py
    ├── test_provider.py
    └── test_loop.py
```

---

# 附录 B：所有配置项说明

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|-------|------|
| `LLM_PROVIDER` | str | `anthropic` | 选择 Provider：`anthropic` / `openai` / `gemini` |
| `ANTHROPIC_API_KEY` | str | — | Anthropic API Key（以 `sk-ant-` 开头） |
| `OPENAI_API_KEY` | str | — | OpenAI / 兼容 Provider 的 API Key |
| `OPENAI_BASE_URL` | str | OpenAI 官方 | Ollama 改为 `http://localhost:11434/v1` |
| `OPENAI_MODEL` | str | `gpt-4o` | OpenAI 模型名 |
| `GEMINI_API_KEY` | str | — | Gemini API Key |
| `GEMINI_MODEL` | str | `gemini-2.0-flash` | Gemini 模型名 |
| `LLM_MODEL` | str | `claude-sonnet-4-6` | Anthropic Provider 的默认模型 |
| `APP_PORT` | int | `8002` | 服务监听端口 |
| `REDIS_HOST` | str | `localhost` | Redis 主机地址 |
| `REDIS_PORT` | int | `6379` | Redis 端口 |
| `REDIS_PASSWORD` | str | — | Redis 密码（生产环境设置） |
| `FEISHU_APP_ID` | str | — | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | str | — | 飞书应用 App Secret |

---

# 附录 C：常见报错速查

| 报错信息 | 原因 | 解决方法 |
|---------|------|---------|
| `AuthenticationError: 401` | API Key 不正确或未设置 | 检查 `.env` 中对应 Provider 的 Key |
| `ModuleNotFoundError: anthropic` | 依赖未安装 | `uv sync`（本地开发）或重建镜像（容器） |
| `context_length_exceeded` | 对话历史超出 Token 限制 | 启用第 8 章的历史压缩 |
| `Connection refused (6379)` | Redis 未启动 | Windows/Docker：`docker start redis-dev`；Mac：`brew services start redis`（见 7.6 节） |
| `Connection refused (11434)` | Ollama 未启动 | `ollama serve` |
| `ValueError: 不支持的 Provider` | `LLM_PROVIDER` 拼写错误 | 检查 `.env`，可选值见附录 B |
| `AssertionError: 有工具调用但未提供 ToolExecutor` | `run_agent_loop` 忘传 `executor` | 初始化 `ToolExecutor` 并传入 |
| `422 Unprocessable Entity` | Pydantic 数据验证失败 | 检查请求 JSON 格式，确认必填字段存在 |
| `lark_oapi` 导入错误 | 飞书 SDK 未安装 | `pip install lark-oapi` |

---

# 附录 D：关键术语词典

| 术语 | 解释 |
|------|------|
| **Agentic Loop** | Agent 的主循环：调用 LLM → 检测工具调用 → 执行工具 → 结果回填 → 继续，直到 LLM 给出最终答案 |
| **Provider** | LLM 服务提供方（Anthropic、OpenAI、Gemini 等）的统一抽象，换 Provider 只改 .env |
| **ToolUseBlock** | LLM 决定调用某工具时产生的消息块，包含工具名和调用参数 |
| **ToolResultBlock** | 工具执行完成后，把结果包装成此类型发回给 LLM |
| **Stop Reason** | LLM 停止生成的原因：`end_turn`（完成）/ `tool_use`（需调工具）/ `max_tokens`（截断） |
| **SSE** | Server-Sent Events，服务器向客户端单向推送流式数据的协议（AI 打字机效果）|
| **SKILL.md** | 描述某个专业领域规范/操作的 Markdown 文件，其 name+description 进技能索引表，全文由 LLM 调 `get_skill_guide` 按需加载 |
| **get_skill_guide** | 一个工具：LLM 判断任务需要某 Skill 时调用它，读取对应 SKILL.md 全文注入对话（取代旧的 TF-IDF 预注入） |
| **Blackboard（黑板模式）** | Swarm 模式中 Agent 共享信息的机制：任务发布到白板，Agent 认领并处理 |
| **Prompt Cache** | Anthropic 的 system prompt 缓存机制，5 分钟内重复调用可降低 90% 费用 |
| **JSONL** | JSON Lines 格式，每行一个 JSON 对象，适合顺序追加写入（日志、会话历史）|

---

# 附录 E：每阶段 Git Commit 检查清单

```bash
# 每完成一个阶段，按此流程提交

# 1. 检查状态
git status

# 2. 运行测试（有的话）
python -m pytest tests/ -v

# 3. 只添加本阶段相关的文件（不要 git add .）
git add core/config.py agent.py cli.py   # 精确指定文件

# 4. 提交（遵循 Conventional Commits 规范）
git commit -m "feat: 阶段1 - 最小 Agent 直接调用 Anthropic API"
```

Commit 命名规范：

| 前缀 | 含义 | 示例 |
|------|------|------|
| `feat:` | 新功能 | `feat: 阶段3 - 添加 Provider 抽象层` |
| `fix:` | Bug 修复 | `fix: OpenAI 适配器 tool_calls 解析错误` |
| `refactor:` | 重构 | `refactor: 把 agent.py 拆分为 agent/ 模块` |
| `test:` | 测试 | `test: 补充跨 Provider 对比测试` |
| `docs:` | 文档 | `docs: 更新 README 的 Provider 配置说明` |

---

> **阅读建议**：
> - 第一次建议按章节顺序完整执行每个阶段
> - 只需要单一 Provider：完成第 0-2 章 + 第 4-5 章，跳过第 3 章
> - 不需要多 Agent：第 0-5 章 + 第 8-12 章就够了
> - 不需要飞书：第 0-11 章
> - 不需要 Docker：第 0-12 章
> - **随时切换 Provider**：只改 `.env` 的 `LLM_PROVIDER`，重启服务即可

---

# 附录 F：技术栈总结 · 项目亮点 · 简历写法

---

## F.1 完整技术栈一览

### 核心语言与运行时

| 技术 | 版本 | 用途 |
|------|------|------|
| Python | 3.11+ | 主语言 |
| asyncio | 内置 | 异步并发基础 |

### LLM 接入层

| 技术 | 版本 | 用途 |
|------|------|------|
| Anthropic SDK | ≥0.40 | Claude 系列模型调用（原生流式） |
| OpenAI SDK | ≥1.40 | GPT / Ollama / DeepSeek / vLLM 兼容接入 |
| Google Generative AI | ≥0.8 | Gemini 模型接入 |

### Web 服务层

| 技术 | 版本 | 用途 |
|------|------|------|
| FastAPI | ≥0.111 | HTTP API 框架 |
| Uvicorn | ≥0.30 | ASGI 服务器 |
| SSE（Server-Sent Events） | — | 流式响应（打字机效果） |
| Pydantic v2 | ≥2.0 | 数据验证与序列化 |
| pydantic-settings | ≥2.0 | 多环境配置管理 |
| CORS Middleware | — | 跨域支持 |

### Agent 引擎层（自实现）

| 技术 / 模块 | 用途 |
|-----------|------|
| Agentic Loop（自实现状态机） | 控制 LLM 思考 → 工具调用 → 结果回填 → 循环 |
| Provider 抽象层（BaseProvider） | 屏蔽各家 API 差异，统一接口 |
| ToolRegistry + ToolExecutor | 工具注册与异步执行 |
| BaseTool 基类 | 规范化工具定义（输入 Schema + 执行方法） |
| MCP（Model Context Protocol） | 标准化工具通信协议 |
| ReAct 模式 | 推理（Reasoning）+ 行动（Acting）循环 |

### 多 Agent 编排层

| 技术 / 模式 | 用途 |
|-----------|------|
| Coordinator 模式 | 主 Agent 专注编排，子 Agent 各司其职 |
| Swarm 模式 | 持久化 Agent 团队 + Blackboard 任务白板 |
| Hub-and-Spoke 路由 | LeadAgent 理解意图，路由到专家 Agent |
| 拓扑排序并行调度 | 有依赖关系的子任务自动并行 |
| SKILL.md + get_skill_guide 工具 | 索引表常驻、LLM 自主按需加载能力文件，避免 Token 浪费 |

### 上下文与记忆层

| 技术 | 用途 |
|------|------|
| Token 预算管理 | 防止超出上下文窗口 |
| 历史压缩（摘要策略） | 超长对话自动压缩保留关键信息 |
| Anthropic Prompt Cache | system prompt 缓存，5 分钟内复用降低 90% 费用 |
| JSONL 会话持久化 | 对话历史落盘，支持断点续传 |
| Redis List 缓存 | 缓存最近 N 轮消息内容，加速会话加载；未命中时退回读文件 |
| aioredis | 异步 Redis 客户端 |

### 可观测性层

| 技术 | 用途 |
|------|------|
| structlog | 结构化 JSON 日志（便于日志聚合平台检索） |
| OpenTelemetry SDK | 分布式链路追踪（Trace / Span） |
| Prometheus Client | 指标暴露（请求数、延迟、Token 消耗） |

### 集成与部署层

| 技术 | 用途 |
|------|------|
| lark-oapi | 飞书开放平台 SDK（消息收发、消息卡片、事件订阅） |
| WebSocket（飞书长连接） | 实时接收飞书消息事件 |
| Docker + docker-compose | 容器化打包与多服务编排 |
| Redis（docker-compose 服务） | 会话存储容器化 |
| httpx | 异步 HTTP 客户端（工具层调用外部 API） |
| tenacity | 指数退避重试（处理 LLM 限速、网络抖动） |
| python-dotenv | 本地开发环境变量加载 |
| aiofiles | 异步文件 I/O（JSONL 会话写入） |

---

## F.2 项目亮点（技术深度维度）

### 亮点 1：自实现 Agentic Loop，完全可观测

市面上大多数项目依赖 LangChain、claude-agent-sdk 等框架，Loop 内部是黑箱。本项目从零实现状态机驱动的 Agentic Loop：

- 每轮状态（LLM 输出 → 工具调用检测 → 工具执行 → 结果注入）完全可调试
- stop_reason 驱动流转：`end_turn` 退出循环，`tool_use` 继续执行，`max_tokens` 触发压缩
- 支持并发工具调用（同一轮多工具并行 `asyncio.gather`）

### 亮点 2：多 Provider 统一抽象，改一行 .env 切换模型

设计了 `BaseProvider` 抽象基类，三个适配器（Anthropic / OpenAI-compat / Gemini）各自处理格式转换，上层 Agentic Loop 代码零感知 Provider 差异：

- 同一套代码可接入 Claude、GPT-4o、Gemini、Ollama 本地模型、DeepSeek
- 以 Anthropic 消息格式为内部标准，OpenAI 的 `tool_calls` / Gemini 的 `function_call` 统一转换
- 生产环境可按成本 / 性能动态切换 Provider

### 亮点 3：多 Agent 编排：Coordinator + Swarm 双模式，两个入口而非一个开关

- **Coordinator 模式**（`POST /ask`）：LeadAgent 只做意图理解和任务分发，专家 Agent 无状态、可水平扩展，同步返回最终结果
- **Swarm 模式**（`POST /swarm/ask`）：持久化 Agent 团队共享 Blackboard 白板，任务自动认领，支持并行执行，调用方式跟 `/ask` 一样一次调用等结果，内部靠 claim() 而非直接派发；可选的 `GET /swarm/tasks/{task_id}` 用于超时后按 task_id 补查
- 两种模式不是靠配置项切换，而是各占一个独立 HTTP 入口，同一个 `python main.py` 一次启动即可同时访问，调用方按自己的调用模式直接选接口
- 拓扑排序实现有向无环图（DAG）任务依赖调度，互不依赖的子任务自动并行

### 亮点 4：工程级上下文管理

- Token 预算感知：超出阈值前自动触发历史压缩，不丢失关键信息
- Anthropic Prompt Cache：固定 system prompt 写入缓存，连续对话节省 90% 输入 Token 费用
- JSONL 断点续传：会话历史顺序追加落盘，服务重启后可从任意轮次恢复

### 亮点 5：生产级可观测性三件套

- **结构化日志（structlog）**：JSON 格式，每条日志携带 session_id / turn / token 信息，便于 ELK / Loki 检索
- **链路追踪（OpenTelemetry）**：每次 LLM 调用、工具执行均生成 Span，推送到 Grafana Tempo，在 Grafana Explore 里追踪完整链路
- **Prometheus 指标**：暴露请求数、P99 延迟、Token 消耗速率，接入 Grafana 大盘

### 亮点 6：飞书机器人全链路集成

- WebSocket 长连接接收消息事件，单线程事件循环统一管理多个 Bot，避免线程竞争
- 支持私聊 + 群聊 @ 触发，消息去重防止重复处理
- 流式回复：Agent 思考过程中实时更新飞书消息卡片（Emoji 反应 + 分段更新）
- 内置 `/help`、`/clear` 等指令，用户体验完整

---

## F.3 简历写法参考

### 项目基本信息模板

```
项目名称：Multi-Agent 智能对话系统（个人项目 / 实习项目）
时间：2025.XX — 2026.XX
角色：独立开发 / 主要开发者
```

### 项目描述（一句话版，适合简历项目标题行）

> 基于 Python 自实现 Agentic Loop 的多 Agent 编排框架，支持 Claude / GPT-4o / Gemini / Ollama 多 Provider 统一接入，集成飞书机器人和 Docker 部署，具备生产级可观测性。

### 项目描述（展开版，适合面试中口头介绍）

> 从零实现了一套 Multi-Agent 编排系统，核心是自实现的 Agentic Loop 状态机，避免依赖黑箱框架。设计了统一 Provider 抽象层，通过适配器模式屏蔽 Anthropic / OpenAI / Gemini 等各家 API 差异，支持一行配置切换大模型。在多 Agent 层面实现了 Coordinator 和 Swarm 两种编排模式，用"索引表常驻 + LLM 通过 get_skill_guide 工具按需加载技能文件"的机制（对齐 Claude Code 的 Skills 设计）替代全量预注入，降低无效 Token 消耗。工程侧接入 structlog 结构化日志、OpenTelemetry 链路追踪和 Prometheus 指标，完整对接飞书机器人并支持 Docker 容器化部署。

### 核心成就子弹点（挑选 3-5 条写在简历条目里）

以下按不同侧重点分类，根据目标岗位选取：

**侧重 AI / LLM 工程：**
- 自实现 Agentic Loop 状态机（ReAct 模式），取代黑箱框架，支持工具调用并行执行与断点续传
- 设计统一 Provider 抽象层（Adapter 模式），单一配置项切换 Claude / GPT-4o / Gemini / Ollama，上层代码零修改
- 实现 Anthropic Prompt Cache 接入，在高频对话场景下将输入 Token 费用降低约 90%
- 实现 Token 预算感知的上下文压缩策略，使对话轮次突破单次模型上下文限制

**侧重后端 / 系统设计：**
- 设计 Coordinator + Swarm 双模式多 Agent 编排，基于 DAG 拓扑排序实现子任务并行调度
- 基于 FastAPI + SSE 实现流式响应接口，支持打字机效果实时推送，P99 首字延迟 < 500ms
- 实现 JSONL + Redis 双层会话持久化，支持多用户并发隔离与任意轮次断点续传
- 接入 OpenTelemetry + Prometheus，实现 LLM 调用链路追踪与 Token 消耗指标监控

**侧重集成与部署：**
- 全链路对接飞书机器人（WebSocket 长连接 + 消息卡片流式更新），支持私聊与群聊 @ 触发
- 基于 Docker Compose 容器化部署，含 Redis 服务编排和 dev / prod 双模式启动脚本

### 技术栈关键词（HR 关键词扫描 / 技能标签）

```
Python · asyncio · FastAPI · Pydantic · LLM · Claude / Anthropic · OpenAI API ·
Agentic Loop · Multi-Agent · Coding Agent · Tool Use · MCP · SSE · Redis · Docker ·
代码审查 · 代码生成 · 自动调试 · 单元测试生成 · 沙箱执行 ·
structlog · OpenTelemetry · Prometheus · 飞书开发 · 多 Provider · Prompt Cache
```

### 常见面试追问与参考答案

**Q：为什么不用 LangChain / claude-agent-sdk，要自己实现 Agentic Loop？**

> 框架封装层过深，Loop 内部不透明，出问题时无法定位是 LLM 决策问题还是工具执行问题。自实现可以在每一轮注入日志、修改消息列表、做 Token 预算判断，这些在框架里都很难做到。代价是代码量多一些，但换来了完整的可控性和可调试性。

**Q：多 Provider 抽象层怎么处理工具调用格式不同的问题？**

> 以 Anthropic 的消息格式为内部标准，因为它的 content block 粒度最细。OpenAI 的 `tool_calls` 数组和 Gemini 的 `function_call` 在各自的 Adapter 里做双向转换。上层 Agentic Loop 只认内部格式的 `ToolUseBlock` 和 `ToolResultBlock`，Provider 换了只需要换 Adapter，Loop 代码不动。

**Q：Token 预算压缩是怎么实现的？**

> 每轮 Loop 开始前检查当前 messages 列表的估算 Token 数。超过阈值（比如 80% 上下文窗口）时，触发压缩：保留最新 N 轮对话，把更早的历史用 LLM 生成摘要替换，摘要作为一条 user 消息注入。这样对话可以无限延伸，不会因为超出上下文窗口被截断。

**Q：Swarm 模式和 Coordinator 模式有什么区别，什么场景用哪个？**

> Coordinator 模式适合任务类型确定、Agent 分工明确的场景（比如"代码助手"固定有 CodeAgent、ReviewAgent、DebugAgent）。LeadAgent 理解意图后路由，各 Agent 无状态，容易水平扩展。Swarm 模式适合任务类型动态、需要 Agent 自主协作的场景（比如复杂研究任务），Agent 团队持久存在，通过 Blackboard 白板发布和认领任务，任务之间可以产生新任务，更灵活但也更难调试。

---

# 附录 G：简历含金量提升三件套（完整代码实现）

> 解决三个核心问题：没有真实工具场景 / 缺乏量化数字 / 没有可展示 Demo。
> 每个小节都是可独立运行的完整代码，直接复制到项目里即可。

---

## G.1 补真实工具调用场景

### 为什么必须做这个

面试官最常问："你的 Agent 解决了什么真实问题，调了哪些真实 API？"

如果工具全是 Mock 数据，这个问题回答不上来，项目含金量立刻折半。

本节实现两个真实工具：
- **天气工具**：接入 OpenWeatherMap（免费，注册即用）
- **本地知识库 RAG**：基础版用 TF-IDF，5 分钟搭起来；第 15 章升级为 embedding（bge-small-zh-v1.5）+ Qdrant 向量检索，支持语义匹配

---

### G.1.1 目录结构（新增部分）

```
my-agent/
├── tools/
│   ├── __init__.py
│   ├── base.py              ← 工具基类（所有工具继承此类）
│   ├── weather.py           ← ★ 真实天气 API 工具
│   └── knowledge_base.py   ← ★ 本地知识库 RAG 工具
├── knowledge_base/          ← 放你的知识文档（.txt / .md）
│   ├── company_policy.md
│   └── product_faq.md
└── scripts/
    └── demo.py              ← ★ 演示脚本（G.3 节）
```

---

### G.1.2 工具基类 `tools/base.py`

所有工具继承这个类，统一接口：

```python
# tools/base.py
"""
工具基类。所有工具必须继承 BaseTool 并实现 definition 和 execute。

设计原则：
- definition：告诉 LLM 这个工具是什么、接受什么参数（LLM 靠这个决定要不要调）
- execute：实际执行逻辑，必须是 async，出错时 raise Exception（上层捕获）
- 工具永远不 return None，保证 LLM 总能拿到有意义的结果
"""

from abc import ABC, abstractmethod
from providers.types import ToolDefinition


class BaseTool(ABC):

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """返回工具定义（名称、描述、参数 Schema）。"""
        ...

    @abstractmethod
    async def execute(self, **kwargs) -> dict:
        """
        执行工具，返回结果字典。
        出错时 raise Exception，由 ToolExecutor 统一处理。
        """
        ...

    @property
    def name(self) -> str:
        return self.definition.name
```

---

### G.1.3 天气工具 `tools/weather.py`

**第一步：注册 OpenWeatherMap 免费 API Key**

1. 打开 https://openweathermap.org/api
2. 点击 Sign Up，注册免费账号
3. 登录后进入 My API Keys，复制默认 Key
4. 在 `.env` 文件里添加：`OPENWEATHER_API_KEY=你的Key`
5. 免费套餐：60 次/分钟，每月 100 万次，完全够用

**第二步：在 `core/config.py` 里加字段：**

```python
# core/config.py（在 Settings 类里新增一行）
openweather_api_key: str = ""
```

**第三步：完整工具代码：**

```python
# tools/weather.py
"""
真实天气查询工具，接入 OpenWeatherMap API。

免费套餐限制：60次/分钟，够演示用。
获取 Key：https://openweathermap.org/api → 注册 → My API Keys
"""

import httpx
from tools.base import BaseTool
from providers.types import ToolDefinition
from core.config import settings


class WeatherTool(BaseTool):

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="get_weather",
            description=(
                "查询指定城市的实时天气信息，包括温度、体感温度、湿度、"
                "天气状况描述、风速、能见度等。"
                "当用户询问天气、是否适合出行、穿什么衣服时调用此工具。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": (
                            "城市名称。支持中文（如'北京'、'上海'）"
                            "或英文（如'Beijing'、'Shanghai'）"
                        ),
                    },
                    "units": {
                        "type": "string",
                        "enum": ["metric", "imperial"],
                        "description": "温度单位：metric=摄氏度（默认），imperial=华氏度",
                        "default": "metric",
                    },
                },
                "required": ["city"],
            },
        )

    async def execute(self, city: str, units: str = "metric") -> dict:
        if not settings.openweather_api_key:
            raise ValueError(
                "OPENWEATHER_API_KEY 未配置，"
                "请到 https://openweathermap.org/api 注册并填入 .env"
            )

        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": city,
            "appid": settings.openweather_api_key,
            "units": units,
            "lang": "zh_cn",   # 天气描述返回中文
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params)

            # 城市不存在时 API 返回 404
            if resp.status_code == 404:
                raise ValueError(f"找不到城市：{city}，请检查城市名称是否正确")

            resp.raise_for_status()
            data = resp.json()

        unit_symbol = "°C" if units == "metric" else "°F"
        speed_unit = "m/s" if units == "metric" else "mph"

        return {
            "city": data["name"],
            "country": data["sys"]["country"],
            "temperature": f"{data['main']['temp']}{unit_symbol}",
            "feels_like": f"{data['main']['feels_like']}{unit_symbol}",
            "temp_min": f"{data['main']['temp_min']}{unit_symbol}",
            "temp_max": f"{data['main']['temp_max']}{unit_symbol}",
            "humidity": f"{data['main']['humidity']}%",
            "description": data["weather"][0]["description"],
            "wind_speed": f"{data['wind']['speed']} {speed_unit}",
            "visibility": f"{data.get('visibility', 'N/A')} m",
            "cloudiness": f"{data['clouds']['all']}%",
        }
```

**验证天气工具是否能跑通（单独测试，不依赖 Agent）：**

```python
# 在项目根目录运行：python -c "..."
# 或者新建 test_weather.py 测试

import asyncio
from tools.weather import WeatherTool

async def test():
    tool = WeatherTool()
    result = await tool.execute(city="北京")
    print(result)
    # 应该看到：{'city': 'Beijing', 'temperature': '28°C', ...}

asyncio.run(test())
```

---

### G.1.4 本地知识库 RAG 工具 `tools/knowledge_base.py`

这是一个**不依赖向量数据库**的轻量 RAG 实现，用 TF-IDF 做相关性检索。
优点：零依赖（不需要 FAISS / ChromaDB），搭建 5 分钟，效果足够演示。

> **本节是基础版**：只支持纯文本 + 字面匹配，用于快速跑通 RAG 流程。第 15 章会把它升级为
> **embedding（bge-small-zh-v1.5）+ Qdrant 向量检索**，支持 PDF/图片和语义匹配（见 15.3.4、15.9）。
> 如果你直接以最终形态实现，可跳过本节的 TF-IDF 版本。

```python
# tools/knowledge_base.py
"""
本地知识库检索工具（轻量 RAG，无向量数据库）。

原理：
1. 启动时扫描 knowledge_base/ 目录，加载所有 .txt 和 .md 文件
2. 用户查询时，用 TF-IDF 算法计算查询词和每篇文档的相关性
3. 返回 top-k 最相关的文档片段

为什么不用向量数据库：
- 个人项目文档数量少（<100 篇），TF-IDF 够用
- 零额外依赖，部署简单
- 面试时能清楚解释原理（向量数据库面试官会追问 embedding 模型选型）
"""

import re
import math
from pathlib import Path
from collections import Counter
from tools.base import BaseTool
from providers.types import ToolDefinition


def _tokenize(text: str) -> list[str]:
    """
    简单分词：提取所有中英文词汇（去除标点和空白）。
    中文按字切分（无词典分词），英文按单词切分。
    """
    # 英文单词
    en_tokens = re.findall(r'[a-zA-Z]+', text.lower())
    # 中文按字切分（每个汉字是一个 token）
    zh_tokens = re.findall(r'[一-鿿]', text)
    return en_tokens + zh_tokens


def _tfidf_score(query: str, doc_content: str, all_docs: list[str]) -> float:
    """
    计算查询词对某篇文档的 TF-IDF 相关性分数。

    TF（词频）：查询词在文档里出现的频率
    IDF（逆文档频率）：查询词在所有文档里越少见，IDF 越高，说明越有区分度
    最终分数 = 所有查询词的 TF × IDF 之和
    """
    query_tokens = _tokenize(query)
    doc_tokens = _tokenize(doc_content)
    doc_counter = Counter(doc_tokens)
    doc_len = len(doc_tokens) or 1

    score = 0.0
    for token in query_tokens:
        # TF：该词在本文档中的频率
        tf = doc_counter.get(token, 0) / doc_len
        # DF：有多少文档包含该词
        df = sum(1 for d in all_docs if token in _tokenize(d))
        # IDF：log((文档总数 + 1) / (包含该词的文档数 + 1))，+1 是平滑处理
        idf = math.log((len(all_docs) + 1) / (df + 1))
        score += tf * idf

    return score


def _extract_relevant_chunk(content: str, query: str, chunk_size: int = 400) -> str:
    """
    从文档中提取与查询最相关的段落（滑动窗口）。
    比直接截取前 N 个字符更精准。
    """
    query_tokens = set(_tokenize(query))
    paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

    if not paragraphs:
        return content[:chunk_size]

    best_para = max(
        paragraphs,
        key=lambda p: sum(1 for t in _tokenize(p) if t in query_tokens),
        default=paragraphs[0],
    )

    if len(best_para) <= chunk_size:
        return best_para
    return best_para[:chunk_size] + "..."


class KnowledgeBaseTool(BaseTool):

    def __init__(self, kb_dir: str = "knowledge_base"):
        self.kb_dir = Path(kb_dir)
        self._docs: list[dict] = []
        self._load_docs()

    def _load_docs(self):
        """扫描目录，加载所有 .txt 和 .md 文件。"""
        if not self.kb_dir.exists():
            return
        for path in sorted(self.kb_dir.rglob("*.txt")) + sorted(self.kb_dir.rglob("*.md")):
            try:
                content = path.read_text(encoding="utf-8")
                self._docs.append({
                    "path": str(path),
                    "title": path.stem.replace("_", " ").replace("-", " "),
                    "content": content,
                })
            except Exception:
                pass  # 跳过无法读取的文件

    def reload(self):
        """重新加载知识库（热更新，不重启服务）。"""
        self._docs.clear()
        self._load_docs()

    @property
    def definition(self) -> ToolDefinition:
        doc_count = len(self._docs)
        return ToolDefinition(
            name="search_knowledge_base",
            description=(
                f"搜索本地知识库（当前共 {doc_count} 篇文档），"
                "查找与问题最相关的内容。"
                "适用于：公司政策、产品介绍、FAQ、技术文档等内部知识查询。"
                "当用户询问内部知识、产品功能、规章制度时优先调用此工具。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索问题或关键词，越具体结果越准确",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回最相关的文档数量（1-5），默认 3",
                        "default": 3,
                        "minimum": 1,
                        "maximum": 5,
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, query: str, top_k: int = 3) -> dict:
        if not self._docs:
            return {
                "error": (
                    f"知识库为空。"
                    f"请在 {self.kb_dir}/ 目录下放置 .txt 或 .md 文档。"
                )
            }

        all_contents = [d["content"] for d in self._docs]

        # 计算每篇文档的相关性分数
        scored = [
            (
                _tfidf_score(query, doc["content"], all_contents),
                doc,
            )
            for doc in self._docs
        ]
        scored.sort(key=lambda x: x[0], reverse=True)
        top_docs = scored[:top_k]

        results = []
        for score, doc in top_docs:
            if score < 0.0001:  # 相关性太低，说明知识库里没有相关内容
                break
            results.append({
                "title": doc["title"],
                "relevance_score": round(score, 4),
                "content": _extract_relevant_chunk(doc["content"], query),
                "source": doc["path"],
            })

        if not results:
            return {
                "query": query,
                "found": False,
                "message": "知识库中未找到与该问题相关的内容",
            }

        return {
            "query": query,
            "found": True,
            "total_docs_searched": len(self._docs),
            "results": results,
        }
```

**准备演示用的知识库文档：**

在项目根目录创建 `knowledge_base/` 文件夹，新建几个示例文档：

```bash
mkdir -p knowledge_base
```

新建 `knowledge_base/company_policy.md`，内容（根据你的业务替换）：

```markdown
# 公司退换货政策

## 退货条件
- 购买后 7 天内可无理由退货
- 商品需保持原包装，未使用状态
- 促销活动商品不支持退货

## 换货条件  
- 商品存在质量问题可申请换货
- 换货周期：收到退回商品后 3 个工作日内发出新品

## 退款说明
- 退款将在审核通过后 1-3 个工作日内原路返回
- 运费由买家承担（质量问题除外）

## 联系方式
- 客服电话：400-xxx-xxxx
- 工作时间：周一至周五 9:00-18:00
```

新建 `knowledge_base/product_faq.md`，内容：

```markdown
# 产品常见问题

## Q：产品支持哪些平台？
A：支持 Windows 10+、macOS 12+、iOS 15+、Android 10+。

## Q：免费版和付费版的区别是什么？
A：免费版每月 100 次调用额度；付费版无限调用，额外包含数据导出和 API 访问功能。

## Q：数据安全如何保障？
A：所有数据采用 AES-256 加密存储，服务器位于国内，符合数据安全法要求。

## Q：如何升级到付费版？
A：登录账户后进入「账户设置」→「升级套餐」，支持支付宝和微信支付。
```

---

### G.1.5 把工具注册到 Agentic Loop

在已有的 Agentic Loop（第 4-5 章实现）里，工具通过 `ToolRegistry` 注册：

```python
# 在 main.py 或 agent_core/ 入口文件里，初始化时注册工具

from tools.weather import WeatherTool
from tools.knowledge_base import KnowledgeBaseTool
from loop.tool_executor import ToolExecutor   # 你的第 5 章实现

# 初始化工具
weather_tool = WeatherTool()
kb_tool = KnowledgeBaseTool(kb_dir="knowledge_base")

# 注册到 ToolExecutor
executor = ToolExecutor()
executor.register(weather_tool)
executor.register(kb_tool)

# 启动 Agent Loop 时传入 executor
result = await run_agent_loop(
    provider=get_provider(),
    system=SYSTEM_PROMPT,
    messages=messages,
    tools=[weather_tool.definition, kb_tool.definition],  # 工具定义传给 LLM
    executor=executor,   # 执行器处理实际调用
)
```

> **如果你还没实现 ToolExecutor**，参考第 5 章。核心逻辑：检测 LLM 输出里的 `ToolUseBlock`，用工具名找到对应的 `BaseTool` 实例，调用 `execute(**block.input)`，把结果包装成 `ToolResultBlock` 追加到消息列表，继续下一轮 Loop。

---

## G.2 用数字量化项目成果

### 为什么要有数字

简历上写"优化了性能"没有说服力。写"通过 Prompt Cache 将 P50 输入 Token 降低 87%，对话延迟从 2.3s 降至 0.8s"才是面试官想看到的。

本节实现一个轻量指标收集器，每次运行后自动打印数字，你拿这些数字填简历。

---

### G.2.1 会话指标收集器 `core/metrics.py`

```python
# core/metrics.py
"""
轻量指标收集器。
在 Agentic Loop 的关键节点埋点，运行结束后打印统计数字。
这些数字就是你简历上能写的"量化成果"。
"""

import time
from dataclasses import dataclass, field


@dataclass
class TurnRecord:
    """一轮 LLM 调用的记录。"""
    turn_index: int
    duration_ms: float          # LLM 调用耗时（毫秒）
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int      # Prompt Cache 命中的 Token 数
    cache_write_tokens: int     # Prompt Cache 新写入的 Token 数
    tool_calls: list[str]       # 本轮调用了哪些工具
    stop_reason: str            # end_turn / tool_use / max_tokens


@dataclass
class ToolRecord:
    """一次工具调用的记录。"""
    tool_name: str
    duration_ms: float
    success: bool
    error: str = ""


@dataclass
class SessionMetrics:
    """一次完整 Agent 会话的指标汇总。"""
    session_id: str
    question: str
    turns: list[TurnRecord] = field(default_factory=list)
    tool_records: list[ToolRecord] = field(default_factory=list)
    _start_time: float = field(default_factory=time.time, repr=False)

    # ── 聚合指标 ───────────────────────────────────────────────────

    @property
    def total_duration_ms(self) -> float:
        return (time.time() - self._start_time) * 1000

    @property
    def total_input_tokens(self) -> int:
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(t.cache_read_tokens for t in self.turns)

    @property
    def cache_hit_rate(self) -> float:
        """Prompt Cache 命中率（命中 Token / 总输入 Token）。"""
        total = self.total_input_tokens
        if total == 0:
            return 0.0
        return self.total_cache_read_tokens / total

    @property
    def avg_llm_latency_ms(self) -> float:
        if not self.turns:
            return 0.0
        return sum(t.duration_ms for t in self.turns) / len(self.turns)

    @property
    def total_tool_calls(self) -> int:
        return len(self.tool_records)

    @property
    def avg_tool_latency_ms(self) -> float:
        if not self.tool_records:
            return 0.0
        return sum(t.duration_ms for t in self.tool_records) / len(self.tool_records)

    @property
    def estimated_cost_usd(self) -> float:
        """
        按 Claude Sonnet 4 定价估算费用。
        Prompt Cache 命中的 Token 按 10% 计费（实际价格需查官网）。
        """
        normal_input = self.total_input_tokens - self.total_cache_read_tokens
        cache_read = self.total_cache_read_tokens

        # 单价（每百万 Token，USD）：输入 $3，缓存命中 $0.3，输出 $15
        cost = (
            normal_input * 3.0 / 1_000_000
            + cache_read * 0.3 / 1_000_000
            + self.total_output_tokens * 15.0 / 1_000_000
        )
        return cost

    # ── 打印报告 ────────────────────────────────────────────────────

    def print_report(self):
        """运行结束后调用，打印可截图的统计报告。"""
        divider = "─" * 52
        print(f"\n{divider}")
        print(f"  📊 会话统计报告")
        print(f"{divider}")
        print(f"  问题：{self.question[:40]}{'...' if len(self.question) > 40 else ''}")
        print(f"  会话 ID：{self.session_id}")
        print(divider)
        print(f"  LLM 调用轮次   : {len(self.turns)} 轮")
        print(f"  平均 LLM 延迟  : {self.avg_llm_latency_ms:.0f} ms")
        print(f"  总端到端延迟   : {self.total_duration_ms:.0f} ms")
        print(divider)
        print(f"  总输入 Token   : {self.total_input_tokens:,}")
        print(f"  总输出 Token   : {self.total_output_tokens:,}")
        if self.total_cache_read_tokens > 0:
            print(f"  缓存命中 Token : {self.total_cache_read_tokens:,}  ({self.cache_hit_rate:.0%})")
        print(f"  估算费用       : ${self.estimated_cost_usd:.5f} USD")
        print(divider)
        if self.tool_records:
            print(f"  工具调用次数   : {self.total_tool_calls} 次")
            print(f"  平均工具延迟   : {self.avg_tool_latency_ms:.0f} ms")
            for rec in self.tool_records:
                status = "✓" if rec.success else "✗"
                print(f"    {status} {rec.tool_name}  ({rec.duration_ms:.0f} ms)")
        print(divider)

    def to_dict(self) -> dict:
        """转成字典，方便写入日志或保存到文件。"""
        return {
            "session_id": self.session_id,
            "question": self.question,
            "total_turns": len(self.turns),
            "total_tool_calls": self.total_tool_calls,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "avg_llm_latency_ms": round(self.avg_llm_latency_ms, 1),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }
```

---

### G.2.2 在 Agentic Loop 里埋点

在第 4 章的 `loop/agent_loop.py`（或你的对应文件）里，找到 LLM 调用和工具执行的位置，加入计时和记录：

```python
# loop/agent_loop.py（在已有代码基础上添加埋点）

import time
import uuid
from core.metrics import SessionMetrics, TurnRecord, ToolRecord
from providers.types import ToolUseBlock, ToolResultBlock, TextBlock, Message


async def run_agent_loop(
    provider,
    system: str,
    messages: list,
    tools: list = None,
    executor=None,
    max_turns: int = 10,
    session_id: str = None,          # ← 新增参数
    metrics: SessionMetrics = None,  # ← 新增参数，传入收集器
) -> tuple[str, SessionMetrics]:     # ← 返回值改为 (文字结果, 指标)
    """
    Agentic Loop 主函数。

    参数：
        metrics - 外部传入的指标收集器，None 时自动创建
    返回：
        (最终回答文字, SessionMetrics 对象)
    """
    if metrics is None:
        question = ""
        for m in messages:
            for block in m.content:
                if isinstance(block, TextBlock):
                    question = block.text
                    break
        metrics = SessionMetrics(
            session_id=session_id or str(uuid.uuid4())[:8],
            question=question,
        )

    for turn_idx in range(max_turns):
        # ── LLM 调用计时开始 ──────────────────────────────────────
        t0 = time.time()

        response = await provider.chat(
            messages=messages,
            system=system,
            tools=tools or [],
        )

        llm_duration_ms = (time.time() - t0) * 1000

        # ── 记录本轮 LLM 调用 ─────────────────────────────────────
        tool_names_this_turn = [
            block.name
            for block in response.content
            if isinstance(block, ToolUseBlock)
        ]
        metrics.turns.append(TurnRecord(
            turn_index=turn_idx,
            duration_ms=llm_duration_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=response.usage.cache_read_tokens,
            cache_write_tokens=response.usage.cache_write_tokens,
            tool_calls=tool_names_this_turn,
            stop_reason=response.stop_reason,
        ))

        # ── 判断是否结束 Loop ─────────────────────────────────────
        if response.stop_reason == "end_turn":
            # 提取最终文字回答
            final_text = ""
            for block in response.content:
                if isinstance(block, TextBlock):
                    final_text += block.text
            return final_text, metrics

        # ── 执行工具调用 ──────────────────────────────────────────
        tool_use_blocks = [
            b for b in response.content if isinstance(b, ToolUseBlock)
        ]

        if not tool_use_blocks:
            # 没有工具调用但也没有 end_turn，提取文字后退出
            final_text = ""
            for block in response.content:
                if isinstance(block, TextBlock):
                    final_text += block.text
            return final_text, metrics

        # 把 LLM 的本轮输出追加到消息历史
        messages.append(Message(role="assistant", content=response.content))

        # 并发执行所有工具（同一轮多工具并行）
        import asyncio as _asyncio

        async def _run_tool(block: ToolUseBlock):
            """执行单个工具，记录耗时和成功/失败。"""
            t_start = time.time()
            try:
                result = await executor.execute(block.name, **block.input)
                t_elapsed = (time.time() - t_start) * 1000
                metrics.tool_records.append(ToolRecord(
                    tool_name=block.name,
                    duration_ms=t_elapsed,
                    success=True,
                ))
                import json
                return ToolResultBlock(
                    tool_use_id=block.id,
                    content=json.dumps(result, ensure_ascii=False),
                    is_error=False,
                )
            except Exception as e:
                t_elapsed = (time.time() - t_start) * 1000
                metrics.tool_records.append(ToolRecord(
                    tool_name=block.name,
                    duration_ms=t_elapsed,
                    success=False,
                    error=str(e),
                ))
                return ToolResultBlock(
                    tool_use_id=block.id,
                    content=f"工具执行失败：{e}",
                    is_error=True,
                )

        # 并发跑所有工具
        tool_results = await _asyncio.gather(*[_run_tool(b) for b in tool_use_blocks])

        # 把工具结果追加到消息历史，继续下一轮
        messages.append(Message(role="user", content=list(tool_results)))

    # 超出最大轮次
    return "已达到最大轮次限制，任务未完成", metrics
```

---

### G.2.3 在 CLI 里调用并打印报告

修改 `cli.py`，每次对话结束后打印统计数字：

```python
# cli.py（在已有代码基础上修改）

import asyncio
from core.metrics import SessionMetrics
from loop.agent_loop import run_agent_loop
from providers.router import get_provider
from providers.types import Message, TextBlock

SYSTEM_PROMPT = "你是一个智能助手，有天气查询和知识库搜索能力，请用中文回答。"


async def main():
    print("=" * 52)
    print("  Agent 已就绪  |  输入 /stats 查看累计统计")
    print("  工具：天气查询 + 知识库搜索")
    print("  Ctrl+C 退出")
    print("=" * 52)

    # 初始化工具（导入你在 G.1 节写的工具）
    from tools.weather import WeatherTool
    from tools.knowledge_base import KnowledgeBaseTool
    from loop.tool_executor import ToolExecutor  # 你的 ToolExecutor

    weather_tool = WeatherTool()
    kb_tool = KnowledgeBaseTool()

    executor = ToolExecutor()
    executor.register(weather_tool)
    executor.register(kb_tool)
    tool_definitions = [weather_tool.definition, kb_tool.definition]

    all_metrics: list[SessionMetrics] = []
    session_counter = 0

    while True:
        try:
            question = input("\n你：").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n已退出。")
            break

        if not question:
            continue

        if question == "/stats":
            if not all_metrics:
                print("  暂无统计数据")
                continue
            total_input = sum(m.total_input_tokens for m in all_metrics)
            total_output = sum(m.total_output_tokens for m in all_metrics)
            total_cache = sum(m.total_cache_read_tokens for m in all_metrics)
            total_cost = sum(m.estimated_cost_usd for m in all_metrics)
            print(f"\n  累计会话次数    : {len(all_metrics)}")
            print(f"  累计输入 Token  : {total_input:,}")
            print(f"  累计输出 Token  : {total_output:,}")
            print(f"  累计缓存命中    : {total_cache:,}")
            print(f"  累计费用估算    : ${total_cost:.4f} USD")
            continue

        if question.lower() in ("q", "quit", "exit", "退出"):
            print("再见！")
            break

        session_counter += 1
        messages = [Message(role="user", content=[TextBlock(text=question)])]

        print("Agent：", end="", flush=True)
        try:
            answer, metrics = await run_agent_loop(
                provider=get_provider(),
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tool_definitions,
                executor=executor,
                session_id=f"cli-{session_counter:03d}",
            )
            print(answer)
            all_metrics.append(metrics)

            # 每次对话结束后打印统计（可截图）
            metrics.print_report()

        except Exception as e:
            print(f"\n[错误] {e}")


if __name__ == "__main__":
    asyncio.run(main())
```

**运行后你会看到类似这样的输出（可截图放简历 / 发给面试官）：**

```
你：北京今天天气怎么样？适合出去跑步吗？

Agent：根据实时天气数据，北京今天气温 26°C，湿度 45%，
微风，天气晴朗，非常适合户外跑步！建议早晨 7-9 点或
傍晚 18-20 点，避开正午高温时段。

────────────────────────────────────────────────────
  📊 会话统计报告
────────────────────────────────────────────────────
  问题：北京今天天气怎么样？适合出去跑步吗？
  会话 ID：cli-001
────────────────────────────────────────────────────
  LLM 调用轮次   : 2 轮
  平均 LLM 延迟  : 1240 ms
  总端到端延迟   : 2890 ms
────────────────────────────────────────────────────
  总输入 Token   : 1,842
  总输出 Token   : 143
  缓存命中 Token : 1,024  (56%)
  估算费用       : $0.00378 USD
────────────────────────────────────────────────────
  工具调用次数   : 1 次
  平均工具延迟   : 412 ms
    ✓ get_weather  (412 ms)
────────────────────────────────────────────────────
```

---

### G.2.4 把数字写进简历

跑几轮对话，记录下来的数字，按这个格式写简历条目：

```
❌ 写法（没有说服力）：
接入 Anthropic Prompt Cache，优化 Token 消耗

✅ 写法（有数据支撑）：
接入 Anthropic Prompt Cache，system prompt 固定部分命中率达 87%，
相同对话场景下输入 Token 费用降低 74%（从 $0.0042 降至 $0.0011/次）

❌ 写法：
实现多工具并行调用

✅ 写法：
实现同轮次多工具并发执行（asyncio.gather），相比串行调用
端到端延迟从 2.4s 降至 1.1s（并行执行天气查询 + 知识库检索两个工具）
```

---

## G.3 制作 Demo 录屏脚本

### 为什么要准备演示脚本

面试前 10 分钟发给面试官一个 GIF 或视频，让他在你进来之前已经对项目有印象。这比你在白板上手画流程图有效 10 倍。

---

### G.3.1 自动演示脚本 `scripts/demo.py`

这个脚本自动运行三个 Coding Agent 场景，每个场景之间有停顿，方便录屏时分段：

```python
# scripts/demo.py
"""
Coding Agent 自动演示脚本。
运行：python scripts/demo.py
建议配合 asciinema 或系统录屏工具录制成 GIF / 视频。
"""

import asyncio
import time
import sys
import textwrap

sys.path.insert(0, ".")

from agent_core.loop import run_agent_loop
from providers.router import get_provider
from providers.types import Message, TextBlock
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from tools.builtin.search_code import SearchCodeTool
from tools.registry import ToolRegistry
from agent_core.executor import ToolExecutor
from core.metrics import SessionMetrics


# ── 演示场景配置 ────────────────────────────────────────────────────

# 用于演示的示例代码（有意埋入 Bug）
_BUGGY_CODE = textwrap.dedent("""
def login(username, password):
    sql = f"SELECT * FROM users WHERE name='{username}' AND pwd='{password}'"
    return db.execute(sql)

def divide(a, b):
    return a / b

def get_user(user_id):
    users = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    for u in users:
        if u["id"] == user_id:
            return u
""").strip()

SCENARIOS = [
    {
        "label": "场景一：代码审查（发现 SQL 注入）",
        "emoji": "🔍",
        "question": (
            "帮我审查以下 Python 代码，找出安全问题：\n\n"
            f"```python\n{_BUGGY_CODE}\n```"
        ),
    },
    {
        "label": "场景二：代码生成 + 自动验证",
        "emoji": "✍️",
        "question": (
            "帮我写一个 Python 函数 `safe_divide(a, b)`，"
            "能处理除零错误，写完后运行验证它能正常工作。"
        ),
    },
    {
        "label": "场景三：多 Agent 串行（审查 → 修复 → 写测试）",
        "emoji": "🤖",
        "question": (
            "以下代码有 SQL 注入漏洞和除零 Bug，"
            "请先审查找出问题，然后修复它们，最后为修复后的代码写单元测试：\n\n"
            f"```python\n{_BUGGY_CODE}\n```"
        ),
    },
]

SYSTEM_PROMPT = """
你是一个专业的 Coding Agent。
工具：read_file（读文件）、search_code（搜索代码）、run_python（执行代码）。
发现代码问题时给出文件名和行号；生成代码后主动用 run_python 验证。
"""


def print_slow(text: str, delay: float = 0.02):
    for char in text:
        print(char, end="", flush=True)
        time.sleep(delay)
    print()


def print_separator(title: str = ""):
    line = "═" * 52
    if title:
        pad = (50 - len(title)) // 2
        print(f"╔{line}╗")
        print(f"║{' ' * pad}{title}{' ' * (50 - pad - len(title))}║")
        print(f"╚{line}╝")
    else:
        print(f"{'─' * 54}")


async def run_demo():
    print_separator("Coding Agent 演示")
    print()
    print("  技术栈：Python · FastAPI · Anthropic Claude")
    print("  功能：代码审查 + 代码生成 + 自动验证 + 多 Agent 协作")
    print()
    input("  按 Enter 开始演示...")

    # 初始化工具
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace="."))
    registry.register(RunPythonTool())
    registry.register(SearchCodeTool(workspace="."))
    executor = ToolExecutor(registry)
    tool_definitions = registry.get_all_definitions()

    provider = get_provider()
    all_metrics = []

    for i, scenario in enumerate(SCENARIOS, 1):
        print()
        print_separator(f"{scenario['emoji']} {scenario['label']}")
        print()
        time.sleep(0.5)

        print("  👤 用户：", end="")
        # 只打印问题的前 80 个字符
        short_q = scenario["question"][:80].replace("\n", " ")
        print_slow(short_q + ("..." if len(scenario["question"]) > 80 else ""), delay=0.015)
        print()
        time.sleep(0.3)

        messages = [Message(role="user", content=[TextBlock(text=scenario["question"])])]
        metrics = SessionMetrics(session_id=f"demo-{i:02d}", question=scenario["label"])

        print("  🤖 Agent 处理中", end="", flush=True)
        start = time.time()
        answer_holder = []

        async def _run():
            ans, m = await run_agent_loop(
                provider=provider,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tool_definitions,
                executor=executor,
                session_id=f"demo-{i:02d}",
                metrics=metrics,
            )
            answer_holder.append(ans)

        task = asyncio.create_task(_run())
        while not task.done():
            print(".", end="", flush=True)
            await asyncio.sleep(0.5)
        await task

        print(f" ({(time.time() - start):.1f}s)\n")

        # 打印工具调用
        if metrics.tool_records:
            print("  🔧 工具调用：")
            for rec in metrics.tool_records:
                status = "✓" if rec.success else "✗"
                print(f"     {status} {rec.tool_name}  {rec.duration_ms:.0f}ms")
            print()

        # 打印回答（截取前 300 字）
        answer = answer_holder[0] if answer_holder else "（无输出）"
        short_ans = answer[:300] + ("..." if len(answer) > 300 else "")
        print("  🤖 Agent：", end="")
        print_slow(short_ans, delay=0.01)
        print()

        metrics.print_report()
        all_metrics.append(metrics)

        if i < len(SCENARIOS):
            input("  按 Enter 继续...")

    # 汇总
    print()
    print_separator("演示完成 · 整体统计")
    print()
    total_input  = sum(m.total_input_tokens for m in all_metrics)
    total_output = sum(m.total_output_tokens for m in all_metrics)
    total_cache  = sum(m.total_cache_read_tokens for m in all_metrics)
    total_cost   = sum(m.estimated_cost_usd for m in all_metrics)
    total_tools  = sum(m.total_tool_calls for m in all_metrics)
    print(f"  演示场景数     : {len(SCENARIOS)} 个")
    print(f"  工具调用总次数 : {total_tools} 次")
    print(f"  总输入 Token   : {total_input:,}")
    print(f"  总输出 Token   : {total_output:,}")
    if total_cache:
        print(f"  缓存命中 Token : {total_cache:,}  ({total_cache/total_input:.0%})")
    print(f"  总费用估算     : ${total_cost:.5f} USD")
    print()
    print("  ✅ 演示结束。截图以上数据用于简历或面试展示。")
    print()


if __name__ == "__main__":
    asyncio.run(run_demo())
```

---

### G.3.2 录制 Demo GIF（推荐工具）

**macOS：**
```bash
# 安装 asciinema（终端录制）
brew install asciinema

# 开始录制
asciinema rec demo.cast

# 运行演示脚本
python scripts/demo.py

# 录制结束后按 Ctrl+D
# 把 .cast 文件转成 GIF（需要 agg 工具）
brew install agg
agg demo.cast demo.gif
```

**Windows / Linux：**
- 使用 OBS Studio（免费，可录制任意窗口区域）
- 或 ShareX（Windows 专用，支持直接导出 GIF）

**录制建议：**
- 把终端字体调大（18px 以上），背景调深色（Dracula / One Dark 主题）
- 录制分辨率 1280×720，GIF 控制在 30 秒以内
- 每个场景录一个独立 GIF，比一个长视频效果好

---

### G.3.3 面试准备清单

```
□ 工具能跑通（运行 python scripts/demo.py，三个场景全部成功）

□ 准备好数字（跑 5-10 次取平均值，填入简历）
  □ 平均 LLM 延迟：____ms
  □ 平均工具延迟：____ms（read_file / run_python 分别记录）
  □ Prompt Cache 命中率：____%
  □ 单次代码审查估算费用：$____

□ 录制了演示视频 / GIF（建议 30 秒内，包含工具调用日志）

□ 能解释 Agentic Loop 的 stop_reason 驱动机制
  参考回答：LLM 每次返回有 stop_reason，"end_turn" 表示完成直接退出，
  "tool_use" 表示需要执行工具，Python 执行后把结果追加到 messages 继续循环，
  这就是 Agent 能多轮思考的原理。不用框架自己写能完整看到每一轮的状态。

□ 能解释 run_python 的沙箱安全机制
  参考回答：在子进程里运行，10 秒超时自动 kill，
  执行前做字符串层面的黑名单检查禁止 subprocess/os.system，
  stdout 和 stderr 分开捕获返回给 LLM，让 Agent 能看到真实执行结果。

□ 能解释多 Agent 串行依赖的实现（DAG 拓扑排序）
  参考回答：Planner 输出的 JSON 里有 depends_on 字段，
  Dispatcher 用拓扑排序把任务分成波次，同一波次并行，
  不同波次等前置波次全部完成才开始，前置任务的结果自动注入到后续任务的 context。
  比如"审查 → 修复 → 写测试"就是三个串行波次。

□ 能解释为什么用四个子 Agent 而不是一个大 Agent 全做
  参考回答：单 Agent 系统提示词越来越长，工具越多 LLM 越容易调用错；
  拆分后每个子 Agent 系统提示词短而精准，工具少而专（CodeReviewer 只有 read_file 和 search_code，
  不需要 run_python），上下文 Token 消耗低，LLM 决策更准确。

□ 面试前一天运行一遍 demo.py，确认 API Key 没过期
```

---

# 第 15 章：阶段 15 —— 多模态 RAG（PDF + 图片知识库）

## 15.1 为什么需要多模态 RAG

第 9 章（附录 G.1.4）实现了一个基础 RAG 工具，只能读取 `.txt` 和 `.md` 纯文本文档。
现实中的知识库文档往往更复杂：

| 文档类型 | 挑战 |
|---|---|
| 带图表的 PDF | 文字可以提取，但图表的数据只存在于图片里 |
| 产品截图 | 界面上的文字和布局信息都在图片里 |
| 技术架构图 | 组件关系只能从视觉理解，文字描述不完整 |
| 纯图片文件 | 没有文字可以提取 |

**解决思路：用视觉 LLM "读懂" 图片，把理解后的文字存入知识库。**

处理流程如下：

```
知识库目录
├── manual.pdf     → docling 解析成 文本/表格/图片 block → 表格导出 Markdown，图片发给视觉 LLM → 合并为文本块
├── screenshot.png → 发给视觉 LLM 描述 → 得到文本块
└── policy.md      → 直接读取文字
         ↓ 统一变成 DocumentChunk（文本）
         ↓ bge-small-zh-v1.5 编码成向量 → 写入 Qdrant（cosine 距离）
         ↓ 用户查询 → 编码成向量 → 向量库近邻检索 → 返回相关块给 LLM 作为上下文
```

---

## 15.2 本章新增内容

```
新增文件：
tools/
├── registry.py                   ← 工具注册表（本章需要）
├── knowledge_base.py             ← 多模态 RAG 工具（BM25+向量+RRF，替换 G.1.4 的版本）
└── document_loaders/
    ├── __init__.py
    ├── base.py                   ← DocumentChunk 数据结构（含切分后的 title_path 等字段）
    ├── splitter.py               ← ★ 结构化切分（标题+段落控长），解决 chunk 粒度过粗
    ├── vision.py                 ← 调用视觉 LLM 生成图片描述
    ├── text_loader.py            ← 加载 .txt / .md（切成多个 chunk）
    ├── pdf_loader.py             ← 加载 PDF（用 docling 解析成文本/表格/图片 block）
    └── image_loader.py           ← 加载独立图片

修改文件：
providers/types.py                ← 新增 ImageBlock 类型
providers/base.py                 ← 新增 supports_vision 能力声明
providers/anthropic.py            ← _to_sdk_messages() 支持 ImageBlock；supports_vision = True
providers/openai.py               ← _to_openai_messages() 支持 ImageBlock；按模型名判断 supports_vision
providers/gemini.py               ← _to_gemini_messages() 支持 inline_data 图片 Part；supports_vision = True
providers/router.py               ← 新增 get_vision_provider()：视觉调用可配置成独立于对话的 Provider
core/config.py                    ← 新增 VISION_PROVIDER / VISION_MODEL 配置项
pyproject.toml                    ← 新增 docling、pillow、sentence-transformers、qdrant-client 依赖

外部服务：
Qdrant（Docker）                   ← 本地向量库，存放文档 embedding，见 15.4
```

> **为什么要新增 `get_vision_provider()`，而不是直接用对话用的 `get_provider()`？**
> 15.3.1 会展开讲：多模态能力是"模型级"的，不是"Provider 级"的。同一个 `openai` Provider，backend 换成 `gpt-4o` 就支持图片，换成 `deepseek-chat` 或本地 `qwen2.5:7b`（纯文本）就不支持。如果图片描述调用硬编码复用 `LLM_PROVIDER`，一旦主对话配置成了不支持视觉的模型，图片会在适配器里被静默丢弃——不报错，只是描述结果变得毫无意义，很难排查。本章让"视觉调用用哪个 Provider/模型"成为一个独立的、可选的配置项，默认回退到对话 Provider，但可以单独指向一个明确支持视觉的模型。

---

## 15.3 核心概念讲解

### 15.3.1 什么是多模态 LLM

传统 LLM 只接受文本输入。**多模态 LLM**（如 Claude claude-sonnet-4-6、GPT-4o）还能接受图片。
调用时，消息内容里可以同时包含文字和图片：

```python
# 普通文本消息（第 1 章就是这种）
{"role": "user", "content": [{"type": "text", "text": "你好"}]}

# 多模态消息（本章新增）
{"role": "user", "content": [
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
    {"type": "text", "text": "请描述这张图片"},
]}
```

Anthropic SDK 原生支持这种格式，我们只需要在 `providers/types.py` 加一个 `ImageBlock` 类型，
然后在各 Provider 适配器里告诉它如何把这个类型转成对应 SDK 所需的格式即可。

### 15.3.1b 视觉能力是"模型级"的，不是"Provider 级"的

本项目的 `LLM_PROVIDER` 选的是**适配器**（anthropic / openai / gemini），但 `openai` 这个适配器
背后可能接的是 GPT-4o（支持图片），也可能接的是 DeepSeek、Ollama 跑的 Qwen2.5（纯文本，不支持图片）。
也就是说，同一个 Provider 名字，换一个模型名，视觉能力就可能完全不同。

如果图片描述功能（`caption_image()`）直接复用对话用的 `get_provider()`：
- 用户在 `.env` 里配置 `LLM_PROVIDER=openai` + `OPENAI_MODEL=deepseek-chat`（纯文本模型）跑对话，
- 知识库加载 PDF 时，图片会被转成 `ImageBlock` 发给这个 Provider，
- 但 `deepseek-chat` 根本不支持图片输入，请求实际发出的是一条"图片被丢弃、只剩空文本"的消息，
- LLM 不会报错，只会基于空输入编一段不相关的描述——知识库检索质量下降，且没有任何报错线索。

**解决方案：让"视觉调用用哪个 Provider/模型"成为独立、可选的配置**（`VISION_PROVIDER` /
`VISION_MODEL`，见 15.6.5），未设置时才回退到对话 Provider；同时给 `BaseProvider` 加一个
`supports_vision` 能力声明（15.6.1），在真正发起视觉调用前做一次显式校验——不支持就直接抛错，
而不是让图片被静默吃掉。这样即使主对话跑在本地纯文本模型上，也能单独指定一个支持视觉的 Provider
（比如 Anthropic 或 GPT-4o）专门处理图片描述，两者互不影响。

### 15.3.2 PDF 解析：为什么用 docling 的 block 模型

**旧方案（pymupdf 纯文本流 + 内嵌图片提取）的两个短板。** 最直接的做法是用 `pymupdf`
（`import fitz`）逐页 `get_text("text")` 拿文字流、`get_images()` 抓内嵌图片对象。它简单，但漏两类内容：

1. **表格行列结构丢失**：`get_text("text")` 是纯文本流提取，不识别表格的行列关系。一张多列表格
   提出来往往是文字按坐标顺序拼在一起、行列错乱的一段——数字密集的表尤其严重，语义完全被破坏。
2. **矢量图表完全捕获不到**：很多"图表"（架构图、柱状图、流程图）是用 PDF 的**矢量绘图指令**画的，
   不是内嵌的 JPEG/PNG 位图。它们既不在文字流里，也不在 `get_images()` 的对象列表里，会被整体漏掉——
   旧版对此只能"暂不处理"。只有本身就是截图的位图不受影响。

**docling 的解决思路：版面分析 + block 模型。** docling（IBM 开源）先用**版面分析模型**理解页面结构，
再把每一页切成一串结构化 block：

- `TextItem` / `SectionHeaderItem`：正文与标题，带层级信息，可继续走本章的 splitter 控长切块；
- `TableItem`：识别出行列结构，可直接 `export_to_markdown()` 导出成 Markdown 表格，**保留行列关系**；
- `PictureItem`：图片/图表区域。关键点是它基于**渲染后的页面裁剪**得到图像，而不是去 PDF 里找内嵌
  图片对象——所以用矢量指令画的架构图/柱状图也会被识别成一个 Picture block 抠出来，弥补了旧版的短板。

**成本权衡**：docling 要在本地跑版面分析、表格结构识别（可选 OCR）等模型，比 pymupdf 的纯规则提取更重
——首次运行要下载模型、单页 CPU 耗时也更高。换来的是表格的结构保真和对矢量图表的覆盖，对"知识库要吃
带图表的 PDF"这个场景是值得的。

### 15.3.3 整体分层设计

本章采用"加载器 + 工具"两层设计：

```
Layer 1：document_loaders/（负责"读懂"各种格式的文件 + 切成语义片段）
  splitter.split_markdown() / split_plain()  → 把长文本按标题+段落控长切成多段
  TextLoader  → list[DocumentChunk]（一份文档切成多个 chunk）
  PDFLoader   → list[DocumentChunk]（docling 解析出 block 后分类处理：文本→切块，表格→Markdown，图片→视觉 LLM 描述）
  ImageLoader → list[DocumentChunk]（异步：调用视觉 LLM）

Layer 2：knowledge_base.py（负责索引、检索）
  KnowledgeBaseTool.ensure_loaded()  → 扫描目录，调用加载器 + 建 dense(向量)+sparse(BM25) 两路索引
  KnowledgeBaseTool.execute()        → BM25 + 向量 双路召回 → RRF 融合，返回 top-k 结果
```

这样做的好处：以后想支持新格式（如 Word、Excel），只需新增一个 Loader，不需要动切分/检索逻辑；
切分逻辑集中在 `splitter.py`，各 Loader 复用同一套控长规则。

### 15.3.4 检索方案：结构化切分 + BM25 与向量双路召回 + RRF 融合

第 9 章的基础 RAG 有两个短板，本章一并补齐（对齐 `DDH-main-agent` 的 `rag/` 实现）：

**短板一：粒度太粗。** 旧版"一份文档 = 一个块"，把整篇 .md / 整个 PDF 编码成一个向量。
长文本的局部要点（某个错误码、某条命名约定）会被整篇的平均语义"稀释"掉，query 命中率低，
命中后返回的还是整篇、下游要自己再找。→ 本章先用 `splitter.py` 把文档按**标题 + 段落 + 控长**
切成多个语义片段（见 15.7.6），每个片段单独编码、单独召回。

**短板二：只有字面匹配（TF-IDF），不理解语义。** 查询"请假"匹配不到写着"休假 / 年假"的文档。
→ 本章不是简单地"换成向量检索"，而是做 **BM25（稀疏/字面）+ 向量（稠密/语义）双路召回，再用
RRF 融合**——两种检索各有盲区，融合后互补：

| 组件 | 作用 | 擅长 / 盲区 | 本项目选型 |
|---|---|---|---|
| **BM25（稀疏）** | 分词后按词频×逆文档频率打分 | 擅长**精确术语/型号/错误码**（`ERR_4001`、`asyncio.gather`）；不懂同义 | `rank-bm25`（进程内现建，零服务） |
| **向量（稠密）** | 文本编码成向量，按余弦夹角找近邻 | 擅长**语义近似**（"请假"↔"年假"）；对罕见精确 token 反而弱 | `bge-small-zh-v1.5` + `Qdrant` |
| **RRF 融合** | 把两路的**排名**（非分数）加权合并 | 消除两套分数量纲不可比的问题，稳定可靠 | 自实现，`k=60`（业界惯例） |

**为什么是 RRF（Reciprocal Rank Fusion）而不是"分数加权求和"：** BM25 的分数范围是 `[0, +∞)`，
cosine 是 `[-1, 1]`，两者量纲完全不同，直接加权要反复调系数且不稳定。RRF 只看**每路里的名次**，
第 r 名贡献 `1/(k + r)`，两路名次相加后重排——无需归一化、无需调参，是混合检索的事实标准。
最终透传的 `rrf_score` 就是 Agent 判断"要不要改写重试"的核心信号（见 15.11 的决策树）。

**为什么是这套选型（BM25 + bge + Qdrant + RRF）：**
- **rank-bm25**：纯 Python、零外部服务，索引在进程内用 chunk 语料现建，几万块以内毫秒级；
- **bge-small-zh-v1.5**：BAAI 智源开源，中文效果强，仅 ~100MB，CPU 就能跑，**完全免费、离线、
  数据不出本地**，无需任何 API Key；
- **Qdrant**：专用向量库（Rust 实现），Docker 一键启动，原生支持 cosine 距离、HNSW 索引和元数据
  过滤，和项目"本地 + Docker 部署"的调性一致；
- 三者都免费、可本地部署，比"单路 TF-IDF 全量扫描"更准也更能扩展。

> **成本权衡**：相比单路 TF-IDF，代价是多了一个 embedding 模型（首次下载 ~100MB）、一个 Qdrant
> 容器和一次 BM25 建索引。换来的是"精确术语 + 语义近似"双覆盖的检索质量，对演示和生产都够用。

---

## 15.4 安装新依赖

本章依赖分两部分：**多模态文档解析**（PDF/图片）和**向量检索**（embedding 模型 + 向量库）。

在项目根目录运行：

```bash
# 激活虚拟环境（确保提示符前有 (.venv)）
uv add docling pillow sentence-transformers qdrant-client rank-bm25
```

`pyproject.toml` 会自动更新，加入：
```toml
"docling>=2.0.0",              # PDF 版面分析：文本/表格/图片 block 解析
"pillow>=10.0.0",              # 图片处理
"sentence-transformers>=3.0.0", # 加载 bge-small-zh-v1.5，本地生成 embedding
"qdrant-client>=1.9.0",        # Qdrant 向量库客户端（稠密/向量检索）
"rank-bm25>=0.2.2",            # BM25 稀疏检索（进程内现建，与向量做 RRF 融合）
```

### 启动 Qdrant（Docker）

Qdrant 是独立服务，用官方镜像一键启动，数据持久化到本地目录：

```bash
docker run -d --name qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v "$(pwd)/qdrant_storage:/qdrant/storage" \
  qdrant/qdrant
```

> **不想起容器？** `KnowledgeBaseTool` 支持传 `qdrant_url=":memory:"` 走纯内存模式，
> 进程退出即清空——适合跑测试或临时演示（16.9 的测试夹具就用这个，见下文）。

### 首次运行会自动下载 embedding 模型

`bge-small-zh-v1.5` 权重约 100MB，首次 `SentenceTransformer(...)` 调用时会自动从
HuggingFace 下载并缓存到 `~/.cache/huggingface/`，之后离线可用。国内网络慢可提前设置镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com   # 可选：走国内镜像加速下载
```

**docling 同样会在首次运行时下载模型。** 第一次用 `DocumentConverter` 解析 PDF 时，docling 会从
HuggingFace 拉取版面分析 + 表格结构识别模型（体积比 bge 大，约几百 MB），同样缓存到本地、之后离线可用。
上面配置的 `HF_ENDPOINT` 镜像对它同样生效，国内网络可复用来加速。

验证安装：
```bash
python -c "from docling.document_converter import DocumentConverter; import PIL; from sentence_transformers import SentenceTransformer; from qdrant_client import QdrantClient; from rank_bm25 import BM25Okapi; print('依赖安装成功')"
# 预期输出：依赖安装成功
```

验证 Qdrant 已启动：
```bash
curl http://localhost:6333/healthz
# 预期输出：healthz check passed
```

---

## 15.5 扩展消息格式 `providers/types.py`

在现有 `providers/types.py` 的 `ToolResultBlock` 类之后，添加 `ImageBlock` 类，
然后更新 `ContentBlock` 联合类型：

```python
# providers/types.py（在 ToolResultBlock 定义之后添加这个新类）

class ImageBlock(BaseModel):
    """
    图片内容块——把图片以 base64 格式传给多模态 LLM。

    使用场景：
      1. 文档加载时：把 PDF 里的图片 / 独立图片文件传给 LLM 生成描述
      2. 直接对话时：如果将来需要让用户上传图片提问

    media_type 支持的值：
      "image/jpeg"  最常见，文件大小小
      "image/png"   截图常用，支持透明度
      "image/webp"  现代格式，比 JPEG 小
      "image/gif"   动图（只取第一帧）
    """
    type: Literal["image"] = "image"
    source_type: Literal["base64"] = "base64"
    media_type: str   # 图片的 MIME 类型
    data: str         # base64 编码的图片，不包含 "data:image/jpeg;base64," 前缀


# 修改这一行，在原来的 Union 里加入 ImageBlock：
# 原来是：ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock]
# 改为：
ContentBlock = Union[TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock]
```

---

## 15.6 更新 Provider 层：图片支持 + 独立的视觉 Provider 配置

上一节说过，视觉能力是"模型级"的。这一节要做三件事：
1. 给 `BaseProvider` 加一个 `supports_vision` 能力声明；
2. 让三个适配器（Anthropic / OpenAI / Gemini）都能把 `ImageBlock` 转成各自 SDK 需要的格式，
   并按各自的方式判断自己是否真的支持视觉；
3. 在 `core/config.py` 和 `providers/router.py` 里新增 `get_vision_provider()`，
   让图片相关的调用可以配置成一个和对话**完全独立**的 Provider/模型。

### 15.6.1 能力声明 `providers/base.py`

打开 `providers/base.py`（对应 3.4 节），在已有的 `supports_tool_use` 属性后面加一个新属性：

```python
# providers/base.py（在 supports_tool_use 属性后面追加）

    @property
    def supports_vision(self) -> bool:
        """
        是否支持图片输入。

        默认返回 False——多模态是模型级能力而不是 Provider 级能力，
        必须由具体子类根据自己接的模型显式声明，不能笼统地假设"这家厂商都支持"。
        """
        return False
```

### 15.6.2 Anthropic 适配器 `providers/anthropic.py`

做两处修改：

**修改 1：顶部导入加入 ImageBlock（约第 11-15 行）**

```python
# 原来：
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, Usage,
    TextDelta, MessageStart, MessageStop,
)

# 改为（加入 ImageBlock）：
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ImageBlock, Usage,
    TextDelta, MessageStart, MessageStop,
)
```

**修改 2：`_to_sdk_messages()` 方法里，在 `else: # TextBlock` 之前加入 ImageBlock 处理，
并加上 `supports_vision` 属性**

找到下面这段（约第 40-56 行），在 `else:` 之前插入 ImageBlock 的处理分支：

```python
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif hasattr(block, "tool_use_id"):  # ToolResultBlock
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    })
                elif isinstance(block, ImageBlock):          # ← 新增这一段
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": block.media_type,
                            "data": block.data,
                        },
                    })
                else:  # TextBlock
                    blocks.append({"type": "text", "text": block.text})
```

再在类里新增 `supports_vision`（放在 `model_name` 属性后面即可）：

```python
    @property
    def supports_vision(self) -> bool:
        # Claude 3 及之后的全系列模型（含本项目默认的 claude-sonnet-4-6）原生支持图片输入。
        return True
```

### 15.6.3 OpenAI 适配器 `providers/openai.py`

`OpenAIProvider` 比较特殊：它是"一个适配器，接多种后端"（GPT-4o、DeepSeek、Ollama 本地模型……），
同一份代码背后的模型是否支持图片完全不确定，不能像 Anthropic 那样写死 `True`。
这里用一个尽力而为的模型名匹配，并允许用 `.env` 显式覆盖，避免匹配不到时死板地拒绝。

**修改 1：`_to_openai_messages()` 加入 ImageBlock 处理**

OpenAI 的图片格式是在 `content` 数组里插入 `{"type": "image_url", ...}`，需要把已有的纯文本
`content` 也改成数组形式：

```python
# providers/openai.py
# 顶部导入加入 ImageBlock：
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)
```

```python
    def _to_openai_messages(self, messages: list[Message], system: str) -> list[dict]:
        result = []
        if system:
            result.append({"role": "system", "content": system})

        for msg in messages:
            text_content = ""
            image_parts = []          # ← 新增：收集图片块
            tool_calls = []
            tool_results = []

            for block in msg.content:
                if isinstance(block, TextBlock):
                    text_content = block.text
                elif isinstance(block, ImageBlock):           # ← 新增这一段
                    image_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{block.media_type};base64,{block.data}",
                        },
                    })
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append({
                        "id": block.id,
                        "type": "function",
                        "function": {
                            "name": block.name,
                            "arguments": json.dumps(block.input, ensure_ascii=False),
                        },
                    })
                elif isinstance(block, ToolResultBlock):
                    tool_results.append(block)

            if tool_results:
                for tr in tool_results:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": tr.content,
                    })
            elif tool_calls:
                m = {"role": "assistant", "content": text_content or None, "tool_calls": tool_calls}
                result.append(m)
            elif image_parts:
                # 有图片：content 必须是数组，文字块和图片块混在一起
                content = image_parts
                if text_content:
                    content = [{"type": "text", "text": text_content}] + image_parts
                result.append({"role": msg.role, "content": content})
            else:
                result.append({"role": msg.role, "content": text_content})

        return result
```

> 对比原来的实现：`ImageBlock` 之前完全没有对应分支，会直接落进 `for` 循环末尾什么都不做——
> 图片被静默丢弃，`text_content` 还是空字符串，最终发出去的是一条空文本消息，不会有任何报错。
> 这正是 15.3.1b 提到的问题，这里把它补上。

**修改 2：新增 `supports_vision`，按模型名判断**

```python
    # 已知支持视觉输入的模型名关键词（按需扩充）。
    # 这是"尽力而为"的启发式判断，不是 OpenAI 官方能力查询接口——
    # 如果你接的是一个不在列表里、但实际支持视觉的模型，用 VISION_CAPABLE=true 显式覆盖（见 15.6.5）。
    _VISION_MODEL_HINTS = ("gpt-4o", "gpt-4.1", "gpt-4-vision", "gpt-5", "o1", "o3", "-vl", "vision")

    def __init__(self, api_key: str, model: str, base_url: str | None = None,
                 supports_vision: bool | None = None):
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        # 显式传入优先；否则按模型名做启发式匹配
        self._supports_vision = (
            supports_vision if supports_vision is not None
            else any(hint in model.lower() for hint in self._VISION_MODEL_HINTS)
        )

    @property
    def supports_vision(self) -> bool:
        return self._supports_vision
```

### 15.6.4 Gemini 适配器 `providers/gemini.py`

Gemini 用 `inline_data` Part 传图片，而不是 `image_url`：

**修改 1：`_to_gemini_messages()` 加入 ImageBlock 处理**

```python
# 顶部导入加入 ImageBlock：
from .types import (
    Message, ToolDefinition, ProviderResponse,
    StreamChunk, TextBlock, ToolUseBlock, ToolResultBlock, ImageBlock,
    Usage, TextDelta, MessageStart, MessageStop,
)
```

```python
            for block in msg.content:
                if isinstance(block, TextBlock):
                    parts.append({"text": block.text})
                elif isinstance(block, ImageBlock):           # ← 新增这一段
                    parts.append({
                        "inline_data": {
                            "mime_type": block.media_type,
                            "data": block.data,
                        }
                    })
                elif isinstance(block, ToolUseBlock):
                    parts.append({
                        "function_call": {
                            "name": block.name,
                            "args": block.input,
                        }
                    })
                elif isinstance(block, ToolResultBlock):
                    parts.append({
                        "function_response": {
                            "name": block.tool_use_id,
                            "response": {
                                "result": block.content,
                                "is_error": block.is_error,
                            },
                        }
                    })
```

**修改 2：新增 `supports_vision`**

```python
    @property
    def supports_vision(self) -> bool:
        # Gemini 1.5 / 2.0 系列（含本项目默认的 gemini-2.0-flash）原生支持图片输入。
        return True
```

### 15.6.5 新增配置项 `core/config.py`

```python
# core/config.py（在 Settings 类里新增，放在 llm_model 字段附近）

    # 视觉调用专用的 Provider/模型（可选）。
    # 留空时回退到对话用的 llm_provider/对应模型，见 providers/router.py 的 get_vision_provider()。
    vision_provider: str = ""
    vision_model: str = ""
    # 当模型名不在 OpenAIProvider 的启发式列表里，但实际支持视觉时，用这个显式声明。
    vision_capable: bool = False
```

对应 `.env` 里新增（放在 15.2 节列出的通用配置区域，全部留空/False 表示"跟对话 Provider 走"）：

```dotenv
# ── 视觉调用专用 Provider（可选，留空则和对话共用同一个 Provider）─────
VISION_PROVIDER=              # 留空 | anthropic | openai | gemini
VISION_MODEL=                 # 留空则用该 Provider 的默认模型
VISION_CAPABLE=false           # 如果 VISION_PROVIDER=openai 且模型不在内置视觉模型名单里，改成 true
```

> 典型用法：对话走本地 Ollama（`LLM_PROVIDER=openai` + `OPENAI_MODEL=qwen2.5:7b`，省 Token、
> 响应快），但知识库里有带图表的 PDF，单独配 `VISION_PROVIDER=anthropic`，图片描述改走 Claude。
> 两条调用链互不干扰，换任意一边都不用动代码。

### 15.6.6 视觉 Provider 路由 `providers/router.py`

把原来 `get_provider()` 里"按名字 + 模型创建实例"的逻辑拆成一个可复用的内部函数，
再加一个 `get_vision_provider()`：

```python
# providers/router.py
"""
根据 .env 配置选择并返回 Provider 实例。

对话用的 Provider 和图片理解用的 Provider 是分开路由的：
  get_provider()        → 对话/工具调用，看 LLM_PROVIDER
  get_vision_provider()  → 图片描述，看 VISION_PROVIDER（留空则回退到 LLM_PROVIDER）
两者可以指向完全不同的后端，互不影响。
"""
from functools import lru_cache
from .base import BaseProvider
from core.config import settings


def _default_model(name: str) -> str:
    """某个 Provider 名字在没有单独指定模型时，用哪个默认模型。"""
    return {
        "anthropic": settings.llm_model,
        "openai": settings.openai_model,
        "ollama": settings.openai_model,
        "deepseek": settings.openai_model,
        "azure": settings.openai_model,
        "gemini": settings.gemini_model,
    }.get(name, settings.llm_model)


@lru_cache(maxsize=8)
def _build_provider(name: str, model: str) -> BaseProvider:
    """
    按 (Provider 名, 模型名) 创建并缓存一个 Provider 实例。

    用 (name, model) 联合做缓存 key，是因为对话 Provider 和视觉 Provider
    可能是同一个适配器（如都用 openai）但配了不同模型，不能共用一个实例。
    """
    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(api_key=settings.anthropic_api_key, model=model)

    elif name in ("openai", "ollama", "deepseek", "azure"):
        from .openai import OpenAIProvider
        return OpenAIProvider(
            api_key=settings.openai_api_key,
            model=model,
            base_url=settings.openai_base_url or None,
            supports_vision=settings.vision_capable or None,
        )

    elif name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(api_key=settings.gemini_api_key, model=model)

    else:
        raise ValueError(
            f"不支持的 Provider: '{name}'。"
            f"可选值：anthropic / openai / gemini。"
            f"检查 .env 里的 LLM_PROVIDER 配置。"
        )


def get_provider() -> BaseProvider:
    """获取对话用的 Provider（看 LLM_PROVIDER）。"""
    name = settings.llm_provider.lower()
    return _build_provider(name, _default_model(name))


def get_vision_provider() -> BaseProvider:
    """
    获取图片理解用的 Provider。

    - 配置了 VISION_PROVIDER：用它（可以和对话 Provider 完全不同）。
    - 没配置：回退到对话 Provider（get_provider()）。
    - 选中的 Provider 必须 supports_vision=True，否则直接报错——
      宁可在调用前就失败，也不要让图片被适配器悄悄丢弃却拿到一段无意义的描述。
    """
    name = (settings.vision_provider or settings.llm_provider).lower()
    model = settings.vision_model or _default_model(name)
    provider = _build_provider(name, model)

    if not provider.supports_vision:
        raise ValueError(
            f"用于视觉调用的 Provider '{name}'（模型 '{model}'）不支持图片输入。\n"
            f"请设置 VISION_PROVIDER / VISION_MODEL 指向一个支持视觉的模型"
            f"（如 anthropic + claude-sonnet-4-6，或 openai + gpt-4o）；"
            f"如果确认该模型其实支持视觉，设置 VISION_CAPABLE=true。"
        )
    return provider


def clear_provider_cache() -> None:
    """测试/切换配置后清空缓存，让下次调用按新配置重新创建实例。"""
    _build_provider.cache_clear()
```

> `get_provider()` 原来用 `@lru_cache(maxsize=1)` 直接包住自己；现在缓存下沉到 `_build_provider`，
> `get_provider()`/`get_vision_provider()` 变成薄封装。行为不变（同名同模型只创建一次客户端），
> 但多了一条路由：`(anthropic, claude-sonnet-4-6)` 和 `(openai, gpt-4o)` 可以同时存在两个实例。

---

## 15.7 文档加载器层

先创建目录：

```bash
mkdir tools/document_loaders
```

---

### 15.7.0 切分规则总览（先读这张表）

本节所有切分逻辑散落在 `base.py` / `splitter.py` / `pdf_loader.py` 三个文件里。动手前先建立
整体心智模型：**为什么这么切、按什么边界切、每个 chunk 最后带什么**。下面几张表是全节的索引。

#### 1. 总原则：绝不定长硬切

不按"每 N 字一刀"切——那会把一句话、一行表格、一个标题从中间截断，导致 chunk 语义破碎、
检索命中后不可读，向量也被无关内容稀释。改为**沿自然边界切（标题 / 空行段落 / 表格整行），
只在超长时才在边界处断**。token 用粗略估算：`max(中文字符数 // 2, 英文词数)`。

三个阈值常量（`splitter.py`）：

| 常量 | 值 | 作用 |
|---|---|---|
| `MAX_TOKENS` | 350 | 单个正文 chunk 的 token 上限，超了在段落边界断 |
| `MIN_TOKENS` | 20 | 过短的尾块并入上一块，避免切出"只有一个标题"的碎块 |
| `TABLE_TOKEN_LIMIT` | 800 | 单个表格块上限（比正文放宽），超了按整行边界切 |

#### 2. 按文件类型分四条切分路径

| 文件类型 | 入口 | 切分边界 | 产出粒度 |
|---|---|---|---|
| `.md` | `TextLoader` → `split_markdown` | `#`~`######` 标题为界，维护标题栈拼 `title_path`；正文超长再按空行段落控长 | 一节 / 一段 |
| `.txt` | `TextLoader` → `split_plain` | 无标题结构，按空行分段 + `MAX_TOKENS` 累加控长 | 一段 |
| `.pdf` | `PDFLoader.load`（docling 版面分析） | 按 block 类型分流，见下表 | 页 / 段 / 表 / 图 |
| 独立图片 | `ImageLoader` | 整图 → 视觉 LLM 描述 → 单块 | 一张图 |

#### 3. PDF 的 block 分流规则（最复杂，改动最大）

**按文档顺序遍历 block，边走边维护 `section_title`（最近小节标题）与 `page_title`（页面级
祖先标题）**；遇到标题 / 表 / 图就先把攒着的正文落盘：

| block 类型 | 处理方式 |
|---|---|
| 文本 | 按当前小节累积，超长走 `split_plain` 控长切成多段 |
| 标题（SectionHeaderItem） | 更新 `section_title`；标题文本**本身也单独成一个小块**（利于"查章节名"直接命中）；命中"大标题特征"（短、单行、含领域关键词）则记为本页 `page_title` |
| 表格（TableItem） | `export_to_markdown()` → `split_table_md`：**整表优先，超长按行切、每片重复标题+表头+分隔行、绝不断行** |
| 图片（PictureItem） | 视觉 LLM 生成描述，前面拼 `[出处] 章节 + 文档主实体`，单独成块并记 `image_path` |

两个关键的"防丢上下文"机制：

- **`page_title`（祖先标题）翻页才重置、同页内不被小节标题覆盖**——解决"X7R/X8R 这种
  页面级上下文被更细的小节标题顶掉"的问题。`title_path` 拼成 `文档 > 祖先标题 > 小节标题`。
- **表格按行切、绝不断行**——二维结构从中间断会行列错位，只在整行边界切，且每片重复表头，
  保证任何一片单独拿出来都能读懂每列是什么。

#### 4. 每个 chunk 最终携带的定位/归因元数据

切完后每个 `DocumentChunk` 都带下列字段（详见 15.7.1）：

| 字段 | 用途 |
|---|---|
| `searchable_text` | `"[标题路径] {title_path}\n" + text`，把所属章节拼进检索文本参与召回 |
| `chunk_type` | `text` / `table` / `image`，下游据此区分处理 |
| `section_title` / `title_path` / `page_title` / `ancestor_title` | 章节定位与来源归因（"出自哪一节"） |
| `page_num` / `page_count` | PDF 页码归因（"第 N 页"） |
| `table_id` / `table_part_idx` / `table_part_count` / `is_table_part` | 大表被按行切成多片时的回溯信息 |
| `image_path` | 图片相对路径，供回答用 `![](image_path)` 回附原图 |

一句话总结：**Markdown 靠标题栈、PDF 靠顺序遍历维护标题状态；正文按段落控长、表格按行
不断行、图片补出身；每个 chunk 都记住"我出自哪一节、第几页"，检索命中即可精准归因。**

---

### 15.7.1 通用数据结构 `tools/document_loaders/base.py`

```python
# tools/document_loaders/base.py
"""
DocumentChunk：从文档中提取的一个内容块。

无论原始文件是 .txt、.pdf 还是 .png，
最终都会被加载器转换成若干个 DocumentChunk。
知识库只和 DocumentChunk 打交道，不关心原始格式。

⚠️ 粒度约定（本章的关键改动）：一个 chunk 是"一个可独立检索、可独立作答的语义片段"，
   **不是一整篇文档**。旧版把整份 .md / 整个 PDF 塞进一个 chunk，粒度太粗会带来两个问题：
     1. 向量被长文本"稀释"——一段几千字的文档编码成一个向量，局部要点（某个错误码、
        某条命名约定）的语义被平均掉，query 命中率下降；
     2. 命中后返回的 content 是整篇，下游 Agent 还要自己在里面找那一句规范。
   所以本章由 `splitter.py` 按"标题 + 段落 + 控长"把文档切成多个 chunk（见 15.7.6），
   Loader 返回的是 `list[DocumentChunk]` 而不再是"一份文档一个块"。
"""
from dataclasses import dataclass


@dataclass
class DocumentChunk:
    """一个可供检索的文本块（语义片段，不是整篇文档）。"""

    source: str
    """原始文件路径，如 'knowledge_base/manual.pdf'。"""

    title: str
    """文档标题，通常取文件名（去掉扩展名）。"""

    text: str
    """chunk 的正文（原文片段）。
    - 纯文本文件：按标题/段落切出来的一段
    - PDF：某一页或某一段的文字，或某张图片的描述
    - 图片文件：视觉 LLM 生成的描述文字
    这是"呈现给下游的原文"，检索命中后原样返回，不做改写。"""

    # ── 检索/定位用的结构化字段（切分后才有意义）──────────────────────────────
    searchable_text: str = ""
    """真正拿去编码/BM25 的文本 = "[标题路径] title_path\\n" + text。
    把所属标题层级拼进检索文本，能让"这段属于哪一节"的上下文参与召回
    （对齐 DDH-main-agent build_chunks 的 searchable_text 设计）。
    为空时检索层会回退用 text。"""

    section_title: str = ""
    """本 chunk 所属的小节标题（最近的一个 Markdown 标题 / PDF 段落标题）。"""

    title_path: str = ""
    """标题层级路径，如 "错误码规范 > 4xx 客户端错误"，供来源归因和定位。"""

    chunk_index: int = 0
    """该 chunk 在所属文档内的序号（从 0 开始），便于回溯上下文顺序。"""

    # ── chunk 类型与祖先标题（对齐 DDH build_chunks / _add_chunk）─────────────
    chunk_type: str = "text"
    """chunk 的内容类型：'text' / 'table' / 'image'。
    下游据此区分处理——例如 image 块要用 image_path 回附图片，table 块要提示"来自表格"。"""

    page_title: str = ""
    """本页的"祖先大标题"（如 PDF 里的 "X8R / Automotive MLCC"）。
    与 section_title 的区别：section_title 是最近的一个小节标题，翻到下一个小标题就被覆盖；
    page_title 是页面级上下文，同一页内不被后续小节标题覆盖，翻页才重置（见 15.7.6 说明）。"""

    ancestor_title: str = ""
    """拼进 title_path 的祖先标题（page_title 与 section_title 不同名时才有值）。
    单独留字段是为了让下游能只取"页面级上下文"而不含小节名。"""

    # ── 表格分块元数据（一张大表被按行切成多块时才有意义，对齐 _split_table_html）──
    table_id: str = ""
    """所属表格的唯一 id。同一张大表切出来的多个块共享同一个 table_id，便于回溯拼回整表。"""

    table_part_idx: int = 0
    """本块是该表格的第几片（从 0 开始）。整表未超长时恒为 0。"""

    table_part_count: int = 1
    """该表格一共被切成几片。未超长时为 1。"""

    is_table_part: bool = False
    """该表格是否被切成了多片（table_part_count > 1 时为 True）。"""

    image_count: int = 0
    """本 chunk 关联的图片数量（纯文本为 0）。"""

    image_path: str = ""
    """图片相对路径（如 'imgs/manual_img_3.jpg'）。仅 chunk_type=='image' 时有值，
    供最终回答用 ![](image_path) 回附原图并标注来源页码（对齐 HPD datasheet-rag SKILL 的强制格式）。"""

    page_num: int = 0
    """所在页码（PDF 才有意义，其他为 0）。"""

    page_count: int = 0
    """文档总页数（PDF 才有意义，其他为 0）。"""
```

---

### 15.7.2 视觉描述函数 `tools/document_loaders/vision.py`

```python
# tools/document_loaders/vision.py
"""
调用视觉 LLM，为图片生成文字描述。

这个函数是多模态 RAG 的核心：
它把图片"翻译"成文字，让基于文本 embedding 的检索引擎也能理解图片内容。
"""

import base64
from providers.types import Message, TextBlock, ImageBlock
from providers.router import get_vision_provider


_CAPTION_SYSTEM = "你是一个文档图片分析专家，擅长从各种图片中提取关键信息。"

_CAPTION_PROMPT = """请用中文详细描述这张图片中的所有重要信息：

- 如果是数据图表（折线图、柱状图、饼图等）：说明图表类型、坐标轴含义、主要数据点和趋势
- 如果是流程图或架构图：说明各模块的名称、相互关系、数据流向
- 如果是截图或界面：说明界面名称、主要功能区域、关键文字信息
- 如果是照片或示意图：说明主要内容、包含的文字

输出纯文字，不要使用 Markdown 格式，不要加标题，直接描述内容。
尽量详细，这些描述将用于知识库检索。"""


def _guess_media_type(suffix: str) -> str:
    """根据文件扩展名猜测 MIME 类型。"""
    mapping = {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".webp": "image/webp",
        ".gif":  "image/gif",
    }
    return mapping.get(suffix.lower(), "image/jpeg")


async def caption_image(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """
    把图片发给视觉 LLM，返回文字描述。

    参数：
        image_bytes  图片的原始二进制数据
        media_type   图片的 MIME 类型

    返回：
        中文文字描述（出错时返回空字符串）

    工作原理：
        1. 把二进制图片数据用 base64 编码，变成文字字符串
        2. 构造一条包含图片块和文字指令的消息
        3. 发给视觉 Provider，等待描述文字

    这里用 get_vision_provider()而不是 get_provider()：图片理解可以配置成和主对话
    完全独立的 Provider/模型（见 15.6.5、15.6.6），不受 LLM_PROVIDER 配的是否支持视觉影响。
    如果没配 VISION_PROVIDER，也没配 VISION_CAPABLE，选中的模型又确实不支持视觉，
    get_vision_provider() 会在这里直接抛 ValueError，而不是把图片悄悄丢掉再返回一段乱猜的描述。
    """
    if not image_bytes:
        return ""

    provider = get_vision_provider()
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    messages = [
        Message(
            role="user",
            content=[
                ImageBlock(
                    source_type="base64",
                    media_type=media_type,
                    data=b64_data,
                ),
                TextBlock(text=_CAPTION_PROMPT),
            ],
        )
    ]

    try:
        response = await provider.chat(
            messages=messages,
            system=_CAPTION_SYSTEM,
            max_tokens=512,
        )
        for block in response.content:
            if isinstance(block, TextBlock):
                return block.text.strip()
    except Exception as e:
        print(f"  [Vision] 图片描述失败：{e}")

    return ""
```

---

### 15.7.3 纯文本加载器 `tools/document_loaders/text_loader.py`

```python
# tools/document_loaders/text_loader.py
"""加载 .txt 和 .md 纯文本文件，并切成多个语义 chunk。"""

from pathlib import Path
from .base import DocumentChunk
from .splitter import split_markdown, split_plain


class TextLoader:

    SUPPORTED_EXTENSIONS = {".txt", ".md"}

    def load(self, path: Path) -> list[DocumentChunk]:
        """
        加载单个文本文件，切成**多个** DocumentChunk。

        .md 走标题感知切分（split_markdown），.txt 走纯段落控长切分（split_plain）。
        文件无法读取或为空时返回空列表（不抛异常，让知识库跳过这个文件）。
        """
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [TextLoader] 无法读取 {path}: {e}")
            return []

        if not content.strip():
            return []

        title = path.stem.replace("_", " ").replace("-", " ")
        pieces = (split_markdown(content) if path.suffix.lower() == ".md"
                  else split_plain(content))

        chunks: list[DocumentChunk] = []
        for i, p in enumerate(pieces):
            # searchable_text 把标题路径拼进检索文本，让"属于哪一节"参与召回
            title_path = p["title_path"]
            searchable = (f"[标题路径] {title_path}\n{p['text']}"
                          if title_path else p["text"])
            chunks.append(DocumentChunk(
                source=str(path),
                title=title,
                text=p["text"],
                searchable_text=searchable,
                section_title=p["section_title"],
                title_path=title_path,
                chunk_index=i,
            ))
        return chunks
```

---

### 15.7.4 PDF 加载器 `tools/document_loaders/pdf_loader.py`

这是本章最复杂的部分，详细注释每一步：

```python
# tools/document_loaders/pdf_loader.py
"""
PDF 文档加载器（基于 docling 版面分析）。

处理流程（每个 PDF 生成**多个** DocumentChunk，粒度到"页 / 段 / 表 / 图"）：
1. 用 docling 的 DocumentConverter 解析 PDF，得到结构化 block 序列
2. **按文档顺序**遍历 block，边走边维护"当前小节标题 section_title / 页面级祖先标题
   page_title"（对齐 DDH build_chunks 的做法——docling 给的是扁平 block 流，标题和正文
   平级出现，只有顺序遍历才能知道某段正文/某张表归属最近哪个标题）：
   - 文本 block：按 section 累积，遇到标题/表/图就先落盘，再走 splitter 控长切成多段
   - 标题 block（SectionHeaderItem）：更新 section_title，命中"大标题"特征则记为本页祖先；
     标题文本本身也单独成一个小块（利于"查章节名"类问题直接命中）
   - 表格 block（TableItem）：export_to_markdown() 导出 Markdown 表格；整表优先，超长则
     按行切、每片重复标题+表头（见 15.7.6 split_table_md），绝不断行
   - 图片 block（PictureItem）：取渲染图，vision.caption_image() 生成描述，并补"出处"
     （所属章节 + 文档主实体），单独成块并记 image_path 供回答回附原图
3. 每个 chunk 记住自己的 page_num 与 title_path，便于来源归因到"第 N 页 / 哪一节"

为什么用 docling 而不是 pymupdf（见 15.3.2）：
- pymupdf 的 get_text("text") 是纯文本流，表格会被拼成行列错乱的一段，结构丢失；
- pymupdf 的 get_images() 只抓内嵌位图，用矢量指令画的架构图/柱状图完全捕获不到。
docling 用版面分析把页面切成 Text/Table/Picture block：表格能导出 Markdown，
Picture 基于渲染页面裁剪（不依赖是否为内嵌对象），所以矢量图表也能被抠出来交给视觉 LLM。

为什么不再"整份 PDF 一个块"：一份几十页的 PDF 编码成一个向量，局部要点会被整篇稀释，
命中后返回的还是整篇。改为按页/段/表/图切，检索命中的是具体那一页那一段。
"""

import asyncio
import io
from pathlib import Path

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling_core.types.doc import TableItem, PictureItem
    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

import re

from .base import DocumentChunk
from .splitter import split_plain, split_table_md
from .vision import caption_image


# 页面级大标题的特征：短文本、无换行，且命中"领域关键词"（介质/汽车级/系列名等）。
# 这类标题是"祖先标题"——同页后续更细的小节标题（如 "Capacitance Range"）会顶掉它，
# 导致 X7R/X8R 这种页面级上下文从数据块里丢失，故单独维护 page_title 不被覆盖。
# 关键词按自己的文档域调整；这里给的是"内部规范/器件手册"常见的一组示例。
_PAGE_TITLE_HINT = re.compile(
    r"(MLCC|X7R|X8R|X5R|NP0|Dielectric|Automotive|Commercial|规范|架构|错误码)",
    re.IGNORECASE,
)


def _is_page_title(text: str) -> bool:
    """判断一个标题是否是"页面级祖先标题"（短、单行、命中领域关键词）。"""
    text = text.strip()
    if not text or len(text) > 60 or "\n" in text:
        return False
    return bool(_PAGE_TITLE_HINT.search(text))


def _doc_main_entity(all_text: str) -> str:
    """统计整份文档出现频次最高的"实体/型号族"（如 LQG15HH / VCHA085D）。
    图片描述由视觉 LLM 生成，不含"该图属于哪个型号/文档"的信息，同类图（两份文档的
    卷盘图）无法区分。用这个主实体给图片补"出身"，让检索能定位到正确文档的图。"""
    from collections import Counter
    cnt: Counter = Counter()
    # 实体样式：字母开头、含数字、长度≥6（如 LQG15HH6N8J02D、VCHA085D）
    for m in re.findall(r"[A-Za-z]{2,}[0-9][A-Za-z0-9]{2,}", all_text):
        cnt[m[:8].upper()] += 1   # 取前 8 位作为"实体族"
    return cnt.most_common(1)[0][0] if cnt else ""


class PDFLoader:

    SUPPORTED_EXTENSIONS = {".pdf"}

    # 单个 PDF 最多处理的图片数量（防止超大 PDF 消耗太多 API 调用）
    MAX_IMAGES = 20

    # 忽略小于此大小的图片（编码为 PNG 后的字节数）——通常是装饰性小图标
    MIN_IMAGE_BYTES = 5000

    def _make_converter(self) -> "DocumentConverter":
        """构造 docling 转换器，开启图片渲染裁剪（否则 PictureItem 拿不到图像）。"""
        opts = PdfPipelineOptions()
        opts.generate_picture_images = True   # 让 PictureItem 携带渲染后的图像
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    async def load(self, path: Path) -> list[DocumentChunk]:
        """
        异步加载单个 PDF 文件。

        docling 的解析是 CPU 密集的同步调用，放进线程池（asyncio.to_thread）不阻塞事件循环；
        图片描述需要调用 LLM（网络请求），多张图片通过 asyncio.gather() 并发处理。
        """
        if not HAS_DOCLING:
            print("  [PDFLoader] 未安装 docling，跳过 PDF。运行：uv add docling")
            return []

        try:
            converter = self._make_converter()
            result = await asyncio.to_thread(converter.convert, str(path))
            document = result.document
        except Exception as e:
            print(f"  [PDFLoader] 无法解析 {path}: {e}")
            return []

        page_count = len(document.pages)
        title = path.stem.replace("_", " ").replace("-", " ")

        def _page_of(item) -> int:
            """从 block 的 provenance 里取 1 基页码，取不到则归到第 1 页。"""
            try:
                return item.prov[0].page_no
            except (AttributeError, IndexError):
                return 1

        # ── 第 1 遍：按文档顺序遍历 block，维护 section_title / page_title ──────
        # 为什么要"按顺序"而不再"按页分组后统一处理"：section_title/page_title 是随
        # block 流推进的状态——只有顺序遍历才能知道某段正文/某张表属于最近哪个标题。
        # 图片需要调 LLM 描述，先只收集字节 + 落一个占位，第 2 遍再并发填描述，保持原有并发。
        pending: list[dict] = []                 # 有序的待生成块（含已解析好的 section/page_title）
        pending_images: list[dict] = []          # 指向 pending 里的图片占位，供第 2 遍回填描述
        text_buf: list[str] = []
        buf_page = 1
        section_title = ""
        page_title = ""
        cur_page = None
        all_text_parts: list[str] = []           # 收集全文用于统计主实体（给图片补出身）

        def _titles_now() -> tuple[str, str]:
            """当前生效的 (title_path, ancestor_title)：page_title > section_title 层级拼接。"""
            ancestor = page_title if page_title and page_title != section_title else ""
            if ancestor and section_title:
                tp = f"{title} > {ancestor} > {section_title}"
            else:
                tp = f"{title} > {section_title}" if section_title else title
            return tp, ancestor

        def flush_text():
            """把攒着的正文按 splitter 控长切成多段，各自落成待生成块。"""
            nonlocal text_buf
            body = "\n".join(text_buf).strip()
            text_buf = []
            if not body:
                return
            tp, ancestor = _titles_now()
            for seg in split_plain(body):
                pending.append({"kind": "text", "text": seg["text"], "page": buf_page,
                                "section_title": section_title, "page_title": page_title,
                                "ancestor_title": ancestor, "title_path": tp})

        for item, _level in document.iterate_items():
            page = _page_of(item)
            if page != cur_page:
                cur_page = page
                page_title = ""        # 翻页重置：祖先标题只在本页内生效

            if isinstance(item, TableItem):
                flush_text()
                md = item.export_to_markdown(document).strip()
                if not md:
                    continue
                tp, ancestor = _titles_now()
                table_id = f"{path.stem}_tbl_p{page}_{len(pending)}"
                # 超长表格按行切、每片重复标题+表头（见 15.7.6 split_table_md）
                for part in split_table_md(md, title=section_title, table_id=table_id):
                    pending.append({"kind": "table", "text": f"【表格】\n{part['text']}",
                                    "page": page, "section_title": section_title,
                                    "page_title": page_title, "ancestor_title": ancestor,
                                    "title_path": tp, "table_id": part["table_id"],
                                    "table_part_idx": part["table_part_idx"],
                                    "table_part_count": part["table_part_count"],
                                    "is_table_part": part["is_table_part"]})

            elif isinstance(item, PictureItem):
                flush_text()
                if len(pending_images) >= self.MAX_IMAGES:
                    continue
                pil_img = item.get_image(document)   # 渲染后的 PIL 图像；未渲染时为 None
                if pil_img is None:
                    continue
                buf = io.BytesIO()
                pil_img.convert("RGB").save(buf, format="PNG")
                img_bytes = buf.getvalue()
                if len(img_bytes) < self.MIN_IMAGE_BYTES:   # 过滤装饰性小图
                    continue
                tp, ancestor = _titles_now()
                slot = {"kind": "image", "text": "", "page": page,
                        "section_title": section_title, "page_title": page_title,
                        "ancestor_title": ancestor, "title_path": tp,
                        "image_path": f"imgs/{path.stem}_img_p{page}_{len(pending_images)}.png",
                        "_img_bytes": img_bytes}
                pending.append(slot)
                pending_images.append(slot)

            elif hasattr(item, "text") and item.text and item.text.strip():
                text = item.text.strip()
                all_text_parts.append(text)
                # SectionHeaderItem 视为标题：切断上文、更新 section_title，标题本身也成块
                if type(item).__name__ == "SectionHeaderItem":
                    flush_text()
                    section_title = text
                    if not page_title and _is_page_title(section_title):
                        page_title = section_title   # 本页第一个"大标题"记为祖先，不被后续覆盖
                    tp, ancestor = _titles_now()
                    pending.append({"kind": "text", "text": text, "page": page,
                                    "section_title": section_title, "page_title": page_title,
                                    "ancestor_title": ancestor, "title_path": tp})
                else:
                    buf_page = page
                    text_buf.append(text)
        flush_text()

        # ── 第 2 遍：并发描述图片，给每张补"出处"（章节 + 文档主实体），回填 pending ──
        if pending_images:
            print(f"  [PDFLoader] {path.name}：正在描述 {len(pending_images)} 张图片（并发）...")
            captions = await asyncio.gather(*[
                caption_image(s["_img_bytes"], "image/png") for s in pending_images
            ])
            main_entity = _doc_main_entity("\n".join(all_text_parts))
            for slot, caption in zip(pending_images, captions):
                if not caption:
                    slot["kind"] = "_drop"       # 描述失败：标记丢弃，第 3 遍跳过
                    continue
                origin = " ".join(x for x in [slot["section_title"], main_entity] if x)
                slot["text"] = "\n".join(x for x in [
                    origin and f"[出处] {origin}",
                    f"【图片描述】: {caption}",
                ] if x)

        # ── 第 3 遍：按原始顺序生成 DocumentChunk ──────────────────────────────
        chunks: list[DocumentChunk] = []
        for i, p in enumerate(x for x in pending if x["kind"] != "_drop"):
            if not p["text"].strip():
                continue
            chunks.append(DocumentChunk(
                source=str(path),
                title=title,
                text=p["text"],
                searchable_text=f"[标题路径] {p['title_path']}\n{p['text']}",
                section_title=p["section_title"],
                title_path=p["title_path"],
                chunk_index=i,
                chunk_type=p["kind"],
                page_title=p["page_title"],
                ancestor_title=p["ancestor_title"],
                table_id=p.get("table_id", ""),
                table_part_idx=p.get("table_part_idx", 0),
                table_part_count=p.get("table_part_count", 1),
                is_table_part=p.get("is_table_part", False),
                image_count=1 if p["kind"] == "image" else 0,
                image_path=p.get("image_path", ""),
                page_num=p["page"],
                page_count=page_count,
            ))
        return chunks
```

---

### 15.7.5 图片加载器 `tools/document_loaders/image_loader.py`

```python
# tools/document_loaders/image_loader.py
"""加载独立图片文件（.jpg, .png, .webp 等）。"""

from pathlib import Path

try:
    from PIL import Image
    import io
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from .base import DocumentChunk
from .vision import caption_image


class ImageLoader:

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    async def load(self, path: Path) -> list[DocumentChunk]:
        """
        异步加载单个图片文件。

        处理步骤：
        1. 用 PIL 打开图片，确认格式可读
        2. 统一转为 JPEG（减小 base64 体积，降低 token 消耗）
        3. 调用视觉 LLM 生成描述
        4. 返回以描述文字为内容的 DocumentChunk
        """
        if not HAS_PIL:
            print("  [ImageLoader] 未安装 pillow，跳过图片。运行：uv add pillow")
            return []

        try:
            img = Image.open(path)

            # 统一转成 RGB + JPEG 格式
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()

        except Exception as e:
            print(f"  [ImageLoader] 无法读取图片 {path}: {e}")
            return []

        print(f"  [ImageLoader] 正在描述图片 {path.name}...")
        caption = await caption_image(img_bytes, "image/jpeg")

        if not caption:
            return []

        title = path.stem.replace("_", " ").replace("-", " ")
        text = f"【图片文件描述】\n{caption}"
        return [
            DocumentChunk(
                source=str(path),
                title=title,
                text=text,
                searchable_text=f"[标题路径] {title}\n{text}",
                section_title=title,
                title_path=title,
                chunk_index=0,
                image_count=1,
                page_count=0,
            )
        ]
```

---

### 15.7.6 切分器 `tools/document_loaders/splitter.py`

这是本章**解决"chunk 粒度太粗"的核心**。设计思路对齐 `DDH-main-agent` 的 `build_chunks`：
**不做定长硬切**（会把一句话/一行表格从中间截断），而是**按文档结构 + 控长**切分：

- **Markdown**：以标题（`#`~`######`）为边界切段，维护"标题路径"（`title_path`，如
  `错误码规范 > 4xx`），让每个 chunk 记住自己属于哪一节；标题下正文过长时再按空行段落
  累加、超过 `MAX_TOKENS` 才断，**绝不从段落中间截断**。
- **纯文本**：没有标题结构，按空行分段后按 `MAX_TOKENS` 累加控长。

```python
# tools/document_loaders/splitter.py
"""
把长文本切成"语义片段"——按结构（Markdown 标题）+ 控长，不做定长硬切。

为什么不定长硬切（如每 500 字一刀）：
  会把一句话、一行表格、一个代码块从中间截断，chunk 语义破碎、检索命中后不可读。
  改为"沿标题/空行等自然边界切，只在超长时才在段落边界断"，每个 chunk 都是完整语义单元。

返回的每个片段是 dict：{"text", "section_title", "title_path"}，
由各 Loader 包装成 DocumentChunk（见 text_loader.py / pdf_loader.py）。
"""

import re

# 单个 chunk 的粗略 token 上限：中文按字符、英文按词估算，取较大者的近似
MAX_TOKENS = 350
# 过短的尾块并入上一块的阈值（避免切出"只有一个标题"的碎块）
MIN_TOKENS = 20
# 单个表格块的 token 上限：超过就按行切分（表格比正文更长，阈值放宽到 MAX_TOKENS 的两倍多）
TABLE_TOKEN_LIMIT = 800

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# Markdown 表格的分隔行（表头与数据之间那一行，如 |---|:--:|---|）
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _rough_tokens(s: str) -> int:
    """粗略 token 估算：中文按字符数、英文按词数，取较大值。"""
    return max(len(s) // 2, len(s.split()))


def _split_long_paragraphs(text: str) -> list[str]:
    """把一段较长的正文按空行分段后，按 MAX_TOKENS 累加控长（不切断段落）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for p in paras:
        pt = _rough_tokens(p)
        if cur and cur_tok + pt > MAX_TOKENS:
            out.append("\n\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(p)
        cur_tok += pt
    if cur:
        out.append("\n\n".join(cur))
    return out


def split_markdown(content: str) -> list[dict]:
    """标题感知切分：以标题为边界，维护 title_path，正文超长再按段落控长。"""
    lines = content.splitlines()
    # 标题栈：[(level, text), ...]，用于拼当前标题路径
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    pieces: list[dict] = []

    def title_path() -> str:
        return " > ".join(t for _, t in stack)

    def section_title() -> str:
        return stack[-1][1] if stack else ""

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        tp, st = title_path(), section_title()
        for seg in _split_long_paragraphs(body):
            pieces.append({"text": seg, "section_title": st, "title_path": tp})

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush()  # 遇到新标题，先把上一节正文落盘
            level = len(m.group(1))
            heading = m.group(2).strip()
            # 弹出同级或更深的标题，维护层级路径
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        else:
            buf.append(line)
    flush()

    # 合并过短的碎块到上一块（常见于"只有一个标题、正文极短"）
    merged: list[dict] = []
    for p in pieces:
        if merged and _rough_tokens(p["text"]) < MIN_TOKENS:
            merged[-1]["text"] += "\n\n" + p["text"]
        else:
            merged.append(p)
    return merged or ([{"text": content.strip(),
                        "section_title": "", "title_path": ""}]
                      if content.strip() else [])


def split_plain(content: str) -> list[dict]:
    """纯文本切分：无标题结构，按空行分段 + MAX_TOKENS 控长。"""
    return [{"text": seg, "section_title": "", "title_path": ""}
            for seg in _split_long_paragraphs(content)]


def split_table_md(table_md: str, title: str = "", table_id: str = "") -> list[dict]:
    """表格切分：整表优先；超长则按 Markdown 行边界切，每块重复标题+表头行，绝不断行。

    为什么表格不能像正文那样"按空行控长切"：表格是二维结构，从中间某个字符断开会
    让行列错位、表头对不上，检索命中后完全不可读。所以只在整行（`| ... |`）边界上切，
    且每一片都重复"标题 + 表头行 + 分隔行"，保证任何一片单独拿出来都能读懂每列是什么。
    （对齐 DDH build_chunks 里的 _split_table_html，只是把 HTML <tr> 换成 docling
    export_to_markdown() 产出的 Markdown 行——见 15.7.4 用的正是 export_to_markdown。）

    返回 [{"text", "table_id", "table_part_idx", "table_part_count", "is_table_part"}]。
    """
    def _one_block(text: str) -> list[dict]:
        return [{
            "text": (f"{title}\n" if title else "") + text,
            "table_id": table_id, "table_part_idx": 0,
            "table_part_count": 1, "is_table_part": False,
        }]

    # 未超长：整表作为一块，不切
    if _rough_tokens(table_md) <= TABLE_TOKEN_LIMIT:
        return _one_block(table_md)

    lines = [ln for ln in table_md.splitlines() if ln.strip()]
    # 找到"表头 + 分隔行"：分隔行（|---|---|）之前的都算表头，之后的是数据行
    sep_idx = next((i for i, ln in enumerate(lines) if _MD_TABLE_SEP_RE.match(ln)), -1)
    # 找不到标准表头结构（异常表格）：无法安全按行切，整表退回单块，绝不硬切
    if sep_idx <= 0 or sep_idx >= len(lines) - 1:
        return _one_block(table_md)

    head_lines = lines[: sep_idx + 1]          # 表头行 + 分隔行，每片都要重复
    data_rows = lines[sep_idx + 1:]
    head = (f"{title}\n" if title else "") + "\n".join(head_lines)

    # 按行累加，超过阈值就断到下一片；每片都以 head（标题+表头+分隔行）开头
    parts: list[list[str]] = []
    cur: list[str] = []
    cur_tokens = _rough_tokens(head)
    for r in data_rows:
        rt = _rough_tokens(r)
        if cur and cur_tokens + rt > TABLE_TOKEN_LIMIT:
            parts.append(cur)
            cur = []
            cur_tokens = _rough_tokens(head)   # 新片重新计入 head 的开销
        cur.append(r)                          # 整行加入，绝不切断一行内部
        cur_tokens += rt
    if cur:
        parts.append(cur)

    out: list[dict] = []
    for i, rows_i in enumerate(parts):
        text_i = head + "\n" + "\n".join(rows_i)   # 第 2、3 片也带标题+表头+分隔行
        out.append({
            "text": text_i,
            "table_id": table_id,
            "table_part_idx": i,
            "table_part_count": len(parts),
            "is_table_part": len(parts) > 1,
        })
    return out
```

> **为什么 Markdown 切分不需要 page_title、PDF 却需要？** `split_markdown` 的标题栈天然
> 保留了完整层级（`一级 > 二级 > 三级`），祖先标题不会丢。而 PDF 经版面分析后拿到的是
> **扁平的 block 序列**（标题和正文平级出现，没有嵌套），一页里如果先出现页面级大标题
> （如 "X8R / Automotive"）再出现细分小节标题（如 "Capacitance Range"），后者会把前者
> "顶掉"，导致数据块丢失页面级上下文。所以 PDF loader 需要单独维护一个"翻页才重置、
> 同页内不被小节标题覆盖"的 `page_title`（见 15.7.4），Markdown 则复用标题栈即可。

> **为什么切分粒度是"标题 + 段落控长"而不是简单定长？** 内部规范类文档（API 约定、
> 错误码表、编码规范）天然是分节的，标题就是最好的切分边界；`title_path` 还顺便解决了
> 来源归因——命中某个 chunk 时能直接告诉下游"这条规范出自《错误码规范 > 4xx 客户端错误》"，
> 而不只是"出自 error_codes.md"。

---

创建空的 `__init__.py`：

```bash
type nul > tools\document_loaders\__init__.py   # Windows
# 或
touch tools/document_loaders/__init__.py          # macOS/Linux
```

---

## 15.8 工具注册表 `tools/registry.py`

工具注册表是一个"工具名 → 工具实例"的映射表。
`ToolExecutor`（第 4 章）要求注册表提供 `.get(name)` 和 `.list_names()` 方法。

```python
# tools/registry.py
"""
工具注册表：管理所有可用工具。

为什么需要注册表：
- ToolExecutor 接到 LLM 返回的工具调用（只有工具名字符串），
  需要通过名字找到对应的工具实例来执行
- 注册表统一管理，避免散落在各处的工具实例难以维护
- default() 方法提供一键注册所有内置工具的快捷方式
"""

from tools.base import BaseTool
from providers.types import ToolDefinition
from observability.logging import logger


class ToolRegistry:

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具。工具名重复时会覆盖旧的。"""
        self._tools[tool.name] = tool
        logger.info("tool_registered", tool_name=tool.name)

    def get(self, name: str) -> BaseTool | None:
        """按名字查找工具，找不到返回 None。"""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """列出所有已注册的工具名。"""
        return list(self._tools.keys())

    def all_definitions(self) -> list[ToolDefinition]:
        """返回所有工具的定义（发给 LLM 用）。"""
        return [tool.definition for tool in self._tools.values()]

    @classmethod
    def default(cls, kb_dir: str = "knowledge_base") -> "ToolRegistry":
        """
        创建并返回一个已注册常用工具的注册表。

        这是启动 Agent 时最常用的入口：
            registry = ToolRegistry.default()
            executor = ToolExecutor(registry)
        """
        registry = cls()

        from tools.knowledge_base import KnowledgeBaseTool
        kb_tool = KnowledgeBaseTool(kb_dir=kb_dir)
        registry.register(kb_tool)

        return registry
```

---

## 15.9 多模态知识库工具 `tools/knowledge_base.py`

```python
# tools/knowledge_base.py
"""
多模态本地知识库检索工具（BM25 + 向量 双路召回 + RRF 融合版）。

支持三种文件格式：
  .txt / .md  → TextLoader（切成多个语义 chunk）
  .pdf        → PDFLoader（docling 解析文本/表格/图片 block，表格转 Markdown + 并发调用视觉 LLM 描述图片）
  .jpg .png 等 → ImageLoader（调用视觉 LLM 描述整张图片）

检索算法：BM25（稀疏/字面）+ Embedding 向量（稠密/语义）双路召回，再用 RRF 融合排序。
  - 稠密：sentence-transformers 加载 bge-small-zh-v1.5 编码成 512 维向量，存 Qdrant（cosine）；
  - 稀疏：rank-bm25 用全部 chunk 语料在进程内现建 BM25 索引，擅长精确术语/型号/错误码；
  - 融合：两路各取 topn，按"名次"做 Reciprocal Rank Fusion（k=60），得到 rrf_score 重排。
  为什么要两路融合：向量懂语义但对罕见精确 token 弱，BM25 擅长精确匹配但不懂同义，
  RRF 只看名次、无需归一化两套不可比的分数，是混合检索的事实标准（对齐 DDH-main-agent）。

为什么是 bge-small-zh-v1.5 + Qdrant + rank-bm25：
  - bge-small-zh-v1.5：BAAI 开源、中文效果强、仅 ~100MB，CPU 就能跑，完全免费、离线、数据不出本地；
  - Qdrant：专用向量库，Docker 一键启动，原生支持 cosine 距离 / HNSW 索引 / 元数据过滤；
  - rank-bm25：纯 Python、零外部服务，索引进程内现建，几万块以内毫秒级；
  - 三者都无需 API Key，和项目"本地 + Docker 部署"的调性一致。
"""

import asyncio
import json
import re
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from rank_bm25 import BM25Okapi

from tools.base import BaseTool
from tools.document_loaders.base import DocumentChunk
from tools.document_loaders.text_loader import TextLoader
from tools.document_loaders.pdf_loader import PDFLoader
from tools.document_loaders.image_loader import ImageLoader
from providers.types import ToolDefinition


# ── Embedding 模型（进程内单例，避免重复加载 ~100MB 权重）─────────────────────────

_EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_EMBED_DIM = 512  # bge-small-zh-v1.5 输出维度
_RRF_K = 60       # RRF 融合常数（业界惯例；越大越平滑，越小越偏向头部名次）
_embedder: SentenceTransformer | None = None


def _tokenize(text: str) -> list[str]:
    """BM25 分词：保留字母数字型号/错误码（如 err4001）与单个中文字符。
    与 DDH-main-agent 的 _tokenize 一致——中文按字切，英文/数字按 token 切。"""
    return re.findall(r"[a-z0-9]+|[一-鿿]", text.lower())


def _get_embedder() -> SentenceTransformer:
    """懒加载 embedding 模型。首次调用会自动从 HuggingFace 下载权重并缓存到本地。"""
    global _embedder
    if _embedder is None:
        # 首次加载耗时几秒；normalize_embeddings 在 encode 时开启，配合 cosine 距离
        _embedder = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    """把一批文本编码成归一化向量（L2 归一化后配合 cosine 距离即等价于点积）。"""
    model = _get_embedder()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,   # 归一化，cosine 相似度更稳定
        convert_to_numpy=True,
    )
    return vectors.tolist()


def _extract_best_chunk(content: str, chunk_size: int = 500) -> str:
    """截取文档开头一段作为展示片段（向量检索命中的是整块，这里只做长度裁剪）。"""
    content = content.strip()
    if len(content) <= chunk_size:
        return content
    return content[:chunk_size] + "..."


# ── 知识库工具 ─────────────────────────────────────────────────────────────────

class KnowledgeBaseTool(BaseTool):

    def __init__(
        self,
        kb_dir: str = "knowledge_base",
        qdrant_url: str = "http://localhost:6333",
        collection: str = "knowledge_base",
    ):
        self.kb_dir = Path(kb_dir)
        self.collection = collection
        self._docs: list[DocumentChunk] = []
        self._bm25: BM25Okapi | None = None   # 稀疏索引，_build_index 时现建
        self._loaded = False

        self._text_loader = TextLoader()
        self._pdf_loader = PDFLoader()
        self._image_loader = ImageLoader()

        # 连接 Qdrant（Docker 起的本地服务，见 15.4）。
        # 也可传 ":memory:" 走纯内存模式，适合测试/演示不想起容器的场景。
        if qdrant_url == ":memory:":
            self._qdrant = QdrantClient(":memory:")
        else:
            self._qdrant = QdrantClient(url=qdrant_url)

    async def ensure_loaded(self):
        """延迟加载：第一次被调用时触发文档加载 + 建两路索引（向量+BM25），之后直接返回。"""
        if not self._loaded:
            await self._load_all()
            self._build_index()
            self._loaded = True

    async def _load_all(self):
        """扫描目录，对每个文件调用对应的加载器（得到 DocumentChunk 列表）。"""
        if not self.kb_dir.exists():
            print(f"  [KnowledgeBase] 目录不存在：{self.kb_dir}")
            return

        text_paths = (
            sorted(self.kb_dir.rglob("*.txt")) +
            sorted(self.kb_dir.rglob("*.md"))
        )
        pdf_paths = sorted(self.kb_dir.rglob("*.pdf"))
        image_paths = []
        for ext in self._image_loader.SUPPORTED_EXTENSIONS:
            image_paths.extend(sorted(self.kb_dir.rglob(f"*{ext}")))

        total = len(text_paths) + len(pdf_paths) + len(image_paths)
        print(f"  [KnowledgeBase] 发现 {total} 个文件"
              f"（文本 {len(text_paths)}，PDF {len(pdf_paths)}，图片 {len(image_paths)}）")

        for path in text_paths:
            self._docs.extend(self._text_loader.load(path))

        if pdf_paths:
            pdf_results = await asyncio.gather(*[
                self._pdf_loader.load(path) for path in pdf_paths
            ])
            for chunks in pdf_results:
                self._docs.extend(chunks)

        if image_paths:
            img_results = await asyncio.gather(*[
                self._image_loader.load(path) for path in image_paths
            ])
            for chunks in img_results:
                self._docs.extend(chunks)

        print(f"  [KnowledgeBase] 加载完成，共 {len(self._docs)} 个文档块")

    @staticmethod
    def _index_text(doc: DocumentChunk) -> str:
        """真正拿去检索的文本：优先 searchable_text（含标题路径），回退 text。"""
        return doc.searchable_text or doc.text

    def _build_index(self):
        """建两路索引：Qdrant 稠密向量 + rank-bm25 稀疏索引（都用 searchable_text）。"""
        # 稠密：每次重建 Qdrant 集合（按 embedding 维度 + cosine 距离）
        self._qdrant.recreate_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=_EMBED_DIM, distance=Distance.COSINE),
        )
        self._bm25 = None

        if not self._docs:
            return

        index_texts = [self._index_text(doc) for doc in self._docs]

        # 稠密索引：编码 searchable_text → 写入 Qdrant。id 用 chunk 下标，检索后据此回取
        vectors = _embed(index_texts)
        points = [
            PointStruct(
                id=i,
                vector=vec,
                # payload 存原始内容 + 定位信息，命中后直接取回，无需回查文件
                payload={
                    "title": doc.title,
                    "source": doc.source,
                    "text": doc.text,
                    "title_path": doc.title_path,
                    "page_num": doc.page_num,
                    "image_count": doc.image_count,
                },
            )
            for i, (vec, doc) in enumerate(zip(vectors, self._docs))
        ]
        self._qdrant.upsert(collection_name=self.collection, points=points)

        # 稀疏索引：用同一份语料在进程内现建 BM25（下标与 self._docs 对齐）
        self._bm25 = BM25Okapi([_tokenize(t) for t in index_texts])

        print(f"  [KnowledgeBase] 双路索引完成：{len(points)} 个向量"
              f"（{_EMBED_MODEL_NAME}，{_EMBED_DIM} 维）+ BM25 稀疏索引")

    async def reload(self):
        """重新加载知识库并重建向量索引（热更新，不重启服务）。"""
        self._docs.clear()
        self._loaded = False
        await self.ensure_loaded()

    @property
    def name(self) -> str:
        return "search_knowledge_base"

    @property
    def description(self) -> str:
        # 只声明"能力"（这个工具能查什么、返回什么），不承载"检索策略"。
        # 何时改写重试、怎么带来源归因等策略，写在 knowledge-base-rag 这个 Skill 里，
        # 由 knowledge_agent 通过 get_skill_guide 按需加载（见 15.11）。
        count = len(self._docs)
        return (
            f"搜索本地知识库（已加载 {count} 个文档块，支持 PDF、图片、文本）。"
            "需要查内部 API 规范、编码规范、错误码、架构约定等文档内容时调用。"
            "采用 BM25 + 向量双路召回、RRF 融合排序，"
            "返回最相关的文档片段（含 title/source/title_path/rrf_score）作为回答依据。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索问题或关键词，越具体结果越准确",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回最相关的文档数量（1-5），默认 3",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["query"],
        }

    # 双路各召回的候选数（融合前）：取得比 top_k 宽，给 RRF 足够的重排空间
    _RECALL_N = 30
    # 融合后 rrf_score 低于此值视为"几乎无匹配"，整体判为未命中
    # （RRF 分数量级由 1/(k+rank) 决定，k=60 时单路第 1 名约 0.016，两路都命中约 0.032）
    _RRF_MIN = 0.005

    def _dense_rank(self, query: str) -> list[int]:
        """稠密召回：query 编码后在 Qdrant 找近邻，返回 chunk 下标按相似度降序。"""
        query_vec = _embed([query])[0]
        hits = self._qdrant.search(
            collection_name=self.collection,
            query_vector=query_vec,
            limit=self._RECALL_N,
        )
        return [int(h.id) for h in hits]

    def _sparse_rank(self, query: str) -> list[int]:
        """稀疏召回：BM25 对 query 打分，返回 chunk 下标按分数降序（取前 _RECALL_N）。"""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        # 只保留有正分的（BM25 无任何词命中时为 0，无意义）
        return [i for i in order if scores[i] > 0][:self._RECALL_N]

    async def execute(self, inputs: dict) -> str:
        """
        检索知识库：BM25 + 向量双路召回 → RRF 融合 → 返回 top_k。

        inputs 字典由 LLM 填写，包含：
            query   搜索问题（必填）
            top_k   返回数量（可选，默认 3）
        """
        await self.ensure_loaded()

        query = inputs.get("query", "")
        top_k = int(inputs.get("top_k", 3))

        if not self._docs:
            return json.dumps({
                "error": f"知识库为空。请在 {self.kb_dir}/ 目录放置文档。"
            }, ensure_ascii=False)

        # 1. 双路召回：稠密（语义）+ 稀疏（字面），各得一份按名次排好的 chunk 下标列表
        dense = self._dense_rank(query)
        sparse = self._sparse_rank(query)

        # 2. RRF 融合：每路里第 rank 名（0 基）贡献 1/(k + rank + 1)，两路相加后重排。
        #    只看名次、不看原始分数，天然消除 cosine 与 BM25 量纲不可比的问题。
        rrf: dict[int, float] = {}
        for rank, i in enumerate(dense):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, i in enumerate(sparse):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)

        ranked = sorted(rrf, key=lambda i: rrf[i], reverse=True)

        results = []
        for i in ranked:
            score = rrf[i]
            if score < self._RRF_MIN:   # 融合后仍极低，判为无匹配
                break
            doc = self._docs[i]
            results.append({
                "title": doc.title,
                "source": doc.source,
                "title_path": doc.title_path,   # 命中片段所在的标题层级，供来源归因
                "rrf_score": round(score, 4),   # 融合置信度，Agent 判断是否改写重试的核心信号
                "content": _extract_best_chunk(doc.text),
                "has_images": doc.image_count > 0,
            })
            if len(results) >= top_k:
                break

        if not results:
            return json.dumps({
                "found": False,
                "message": "未找到相关内容",
            }, ensure_ascii=False)

        return json.dumps({
            "found": True,
            "query": query,
            "total_docs": len(self._docs),
            "results": results,
        }, ensure_ascii=False, indent=2)
```

### 独立运行 demo（不接 Agent，直接验证检索）

写完工具后，别急着接进 Coordinator。先单独跑一遍，确认"加载 → 编码 → 建索引 → 检索"这条链路是通的。
在文件末尾加一段 `__main__`，或另存为 `demo_kb.py`：

```python
# demo_kb.py —— 独立验证知识库向量检索（不调用对话 LLM）
import asyncio
import json

from tools.knowledge_base import KnowledgeBaseTool


async def main():
    # 用 :memory: 内存向量库，不依赖 Docker 起的 Qdrant，跑完即清空
    # 想连真实 Qdrant 就删掉 qdrant_url 参数（默认 http://localhost:6333）
    kb = KnowledgeBaseTool(kb_dir="knowledge_base", qdrant_url=":memory:")

    # 首次调用会：加载文档 → 下载/加载 bge-small-zh-v1.5 → 编码 → 写入向量库
    print("首次加载（含模型下载 + 建索引，可能要几秒到几十秒）...")
    await kb.ensure_loaded()

    # 故意用和原文不同的说法，验证"语义匹配"而非"字面匹配"
    queries = [
        "怎么发 HTTP 请求？",       # 语义 ≈ 内部请求封装（原文写的是 HttpClient / 对外请求）
        "变量和类应该怎么命名",     # 语义 ≈ 命名规范
        "并发跑多个协程怎么写",     # 语义 ≈ 异步规范（asyncio.gather）
        "今天天气怎么样",           # 无关：应命中不到或分数很低
    ]

    for q in queries:
        print(f"\n{'=' * 60}\n查询：{q}")
        raw = await kb.execute({"query": q, "top_k": 2})
        data = json.loads(raw)
        if not data.get("found"):
            print("  → 未命中（符合无关查询的预期）")
            continue
        for i, r in enumerate(data["results"], 1):
            loc = r["title_path"] or r["title"]
            print(f"  {i}. [rrf={r['rrf_score']}] {loc}（{r['source']}）")
            print(f"     {r['content'][:60]}...")


if __name__ == "__main__":
    asyncio.run(main())
```

运行：

```bash
python demo_kb.py
```

预期输出（rrf_score、命中片段因模型/文档/切分而异，重点看**命中的是不是正确的那一节**）：

```
首次加载（含模型下载 + 建索引，可能要几秒到几十秒）...
  [KnowledgeBase] 发现 2 个文件（文本 2，PDF 0，图片 0）
  [KnowledgeBase] 加载完成，共 9 个文档块        # ← 两份 .md 按标题切成 9 段（旧版是 2 个整块）
  [KnowledgeBase] 双路索引完成：9 个向量（BAAI/bge-small-zh-v1.5，512 维）+ BM25 稀疏索引

============================================================
查询：怎么发 HTTP 请求？
  1. [rrf=0.0323] 内部 HTTP 客户端规范 > 统一请求封装（knowledge_base/api_spec.md）
     所有对外请求必须通过 core.http.HttpClient，禁止直接使用 requests / httpx...

============================================================
查询：变量和类应该怎么命名
  1. [rrf=0.0299] Python 编码规范 > 命名（knowledge_base/coding_style.md）
     函数 / 变量：snake_case；类：PascalCase...

============================================================
查询：并发跑多个协程怎么写
  1. [rrf=0.0164] Python 编码规范 > 异步（knowledge_base/coding_style.md）
     并发多个协程用 asyncio.gather，不要手动创建裸 Task 不管理...

============================================================
查询：今天天气怎么样
  → 未命中（符合无关查询的预期）
```

> **看点**：
> 1. **粒度**：同一份 `coding_style.md` 现在被切成"命名 / 异步 / …"多个片段，命中的是**具体那一节**
>    （`title_path` 直接告诉你出自"Python 编码规范 > 命名"），而不再是整篇文档。
> 2. **双路互补**：语义类查询（"发 HTTP 请求"→"对外请求 / HttpClient"）靠向量召回；若查的是精确
>    术语（如 `asyncio.gather`、某个错误码），BM25 会把它顶到前面——两路名次经 RRF 融合后重排。
> 3. **无匹配**：无关查询两路都召不到有效结果，融合分低于 `_RRF_MIN`，返回"未命中"。
>    若无关查询也返回了内容，说明 `_RRF_MIN` 偏低，可上调。

---

## 15.10 准备知识库测试文档

> **知识库该装什么？** 本项目的子 Agent 全是编码相关的（code_writer / code_reviewer /
> debugger / test_writer），所以知识库要装的不是"退换货政策"这类客服问答，而是**能给代码
> 生成/审查当上下文的领域知识**——内部 API 规范、编码规范、错误码表、架构约定等。这样
> RAG 检索出来的内容会被编码子 Agent 直接消费，形成真正的闭环（详见 15.11）。

```bash
mkdir knowledge_base
```

新建 `knowledge_base/api_spec.md`（内部 API 规范）：

```markdown
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
```

新建 `knowledge_base/coding_style.md`（编码规范 + 错误码）：

```markdown
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
```

---

## 15.11 让知识库工具接入 Coordinator 架构（RAG 作为一个 Skill）

> `agent/agent.py` 在第 10 章已经被删除了，所以知识库工具不是"整体替换一个单
> Agent 的工具列表"，而是要接到 Coordinator 的"规划 → 分发 →聚合"结构里——
> 知识库检索本质上是一类子任务，交给一个新的子 Agent 去做最自然，跟
> `code_reviewer`、`test_writer` 是同一种模式（`sub_agents/base.py`）。
>
> **定位很关键**：本项目的子 Agent 都是编码相关的，所以 `knowledge_agent` 不是通用问答机器人，
> 而是**编码任务的"领域知识供给方"**——它查出内部 API 规范、编码规范、错误码等信息，
> 作为前置任务的产出，供后续 `code_writer` / `code_reviewer` 当上下文使用（见文末的编排示例）。

### 核心思想：把「检索操作规范」做成一个 Skill，走第 9 章的 skill index 机制

> **这正是"做成 skill index"。** 第 9 章确立了 Skills 系统的两层结构：**system_prompt 里常驻一张
> 极短的「技能索引表」（只有 `name + 一句话描述`），LLM 语义匹配后主动调用 `get_skill_guide(name)`
> 按需加载全文**（见 9.2/9.3）。RAG 检索**天然就是这样一个 Skill**——"什么时候该查知识库、怎么评估
> 结果够不够、不够怎么改写重试、答案怎么带来源归因"是一整套操作规范，正好对应一份 SKILL.md，
> 进索引表、由 `get_skill_guide` 按需加载，和 `code-review` / 团队规范那些 Skill **走完全同一套机制**。
> 这套设计对齐 `DDH-main-agent` 的 `hpd_agent`：它的 System Prompt 里就是一张技能索引表，`datasheet-rag`
> （RAG 检索）只是其中一行，LLM 命中后调 `get_skill_guide("datasheet-rag")` 拿到检索决策树再驱动检索。

所以这一版不再把检索策略硬编码进 `KnowledgeAgent` 的 system_prompt，而是：

1. 新建 `skills/knowledge-base-rag.md`，把检索决策树、止损纪律、来源归因规范写进去（内容仿照
   DDH-main-agent 的 `datasheet-rag` 技能）。它和团队规范 Skill 一样**自动进第 9 章的技能索引表**
   （`build_skill_index()` 只取它的 `name + description`），无需额外注册；
2. `KnowledgeAgent` 的 system_prompt **瘦身**为一句话："需要检索时，先 `get_skill_guide('knowledge-base-rag')`
   拿到操作指南，再按指南驱动 `search_knowledge_base`"——检索规范的"单一事实来源"是那份 SKILL.md；
3. `KnowledgeAgent` 的 `tools` 同时挂上 `search_knowledge_base` 工具本身。而 `get_skill_guide`
   由 `SubAgent.run()` 恒定注入（见 9.5），无需在这里重复挂。

这就是用户想要的闭环：**索引表让 LLM 知道"有 RAG 这个 Skill" → LLM 决定要用 → `get_skill_guide`
加载全文 → 按操作文档驱动 `search_knowledge_base`**。与第 9 章的团队规范 Skill 是**同一套 skill index
机制**，没有任何旁路。

### 新建 `skills/knowledge-base-rag.md`

> 这是一个"操作文档型" Skill——正文不是编码规范，而是**如何驱动 `search_knowledge_base` 工具**的
> 决策树。它和 9.6 的团队规范 Skill 放在同一个 `skills/` 目录，同样进索引表、同样由
> `get_skill_guide` 按需加载。

````markdown
---
name: knowledge-base-rag
description: 本地知识库语义检索操作指南——迭代检索决策树，评估→改写→重试由你在循环中驱动，回答必须带来源归因。当需要查内部 API 规范、编码规范、错误码、架构约定等文档内容时加载。
---

# 知识库语义检索技能（Agentic RAG）

## 一、这个技能解决什么问题

编码子任务（code_writer / code_reviewer / debugger）需要遵循**团队内部的技术规范**——
API 封装约定、编码规范、错误码表、架构约定等。这些内容存在本地知识库里，必须**语义检索**
出相关段落，再基于检索到的原文提供给编码 Agent 当上下文。

**核心心法：检索不是"查一次就用"，而是你主动驱动的行为。**
每次 `search_knowledge_base` 后，先**评估结果够不够**，再决定：直接采用 / 改写重试 / 如实说明查不到。

## 二、什么时候用 search_knowledge_base（边界判断，先做）

| 需求 | 走哪里 |
|---|---|
| 内部 API 规范 / 编码规范 / 错误码 / 架构约定的**具体内容** | ✅ `search_knowledge_base`（本技能） |
| 通用编程知识（语言语法、标准库用法） | ❌ 直接用你已有的知识回答，不必查库 |
| 知识库里明显不会有的内容（如客服政策、闲聊） | ❌ 如实说明不在知识库范围 |

## 三、可用工具：search_knowledge_base

`search_knowledge_base(query, top_k?)` —— BM25（字面）+ 向量（语义）双路召回、RRF 融合后的 top-k 片段。

**返回关键信号（你的决策全靠它们）：**

| 字段 | 含义与用法 |
|---|---|
| `found` | 是否检索到结果（融合分低于阈值会被过滤，可能整体 found=false） |
| `results[]` | 每条含 `title / source / title_path / rrf_score / content / has_images` |
| `title_path` | 命中片段所在的标题层级路径（如 `错误码规范 > 4xx 客户端错误`），**来源归因用它** |
| `rrf_score` | BM25+向量的 RRF 融合置信度，**判断是否改写重试的首要信号** |

**`rrf_score` 阈值解读（RRF k=60，单路第 1 名约 0.016，两路都命中约 0.032）：**
- `> 0.02`：两路较一致命中 → 进入充分性评估
- `0.01 ~ 0.02`：只有一路命中或名次靠后 → 可用，但要意识到可能不精确，必要时改写再查一次
- `< 0.01` 或整体 `found=false`：**大概率需要改写查询重试**，不要硬凑
- 提示：查**精确术语/错误码/函数名**时靠 BM25，用原词更容易命中；查**概念/意图**时靠向量，换同义说法更有效。

## 四、迭代检索决策树（每轮必须走完）

```
需要内部规范
   │
   ▼
search_knowledge_base(query)     ← 第 1 轮用原始查询
   │
   ▼
[评估] 你自己做（不调工具）：逐条判断"这段原文是否真的覆盖了所需规范？"
   · 保留真正相关的片段，忽略只是词面相似的
   · 记住每条的 source / title，作为来源
   │
   ├── 足够 ─────────► 整理规范（带来源 title_path），交给下游编码 Agent（见第五节）
   │
   └── 不足 ────────► [重试决策] 选一种策略：
           ① rrf_score 普遍 <0.01 → 改写查询（换同义词 / 中英互换 / 拆分子问题）
           ② 查的是精确术语/错误码/函数名却没命中 → 用**原词**再查（走 BM25 字面路更容易命中）
           ③ 查的是概念/意图却命中错主题 → 换更贴近语义的说法（走向量路）
           ④ 结果太窄 → 把 top_k 调到 5，或换更宽的关键词
                   │
                   ▼
           带着改写后的 query 回到 search_knowledge_base
```

**迭代纪律（硬性上限）：**
- `search_knowledge_base` 调用总次数 **≤ 4 次**。达到上限立即停止，如实说明"已尝试多种检索仍未找到相关规范"，**绝不编造 API 或规范**。
- 每次改写要有实质变化，不要重复已试过的查询。
- 第 1 轮就足够时直接采用，不做无谓重试。

## 五、输出格式（强制）

你的输出会作为 `context` 传给 code_writer / code_reviewer，所以：

- **原样、结构化地**整理检索到的规范（保留接口签名、错误码、约定原文），不要改写或"润色"规范内容。
- 每条规范标注来源：**（来自 `source` 的 `title_path`）**，如"（来自 coding_style.md 的《Python 编码规范 > 命名》）"，便于下游精确追溯到具体章节。
- 知识库里没有的，明确写"知识库中无相关规范"，**绝不凭空编造** API 名、参数或错误码。
- 面向的是下游 Agent 而不是最终用户，所以要准确、可直接引用，不要口语化寒暄。

## 六、红线

- 检索不到就如实说，**永不编造**规范内容或来源。
- 来源标注不可省略——没有 `source` 的规范条目视为不可靠。
- `search_knowledge_base` 总调用次数不得超过 4 次，达到上限立即停止。
````

### 新建 `sub_agents/knowledge_agent.py`（system_prompt 瘦身 + 挂 RAG 工具）

```python
# sub_agents/knowledge_agent.py
from .base import SubAgent
from tools.knowledge_base import KnowledgeBaseTool


class KnowledgeAgent(SubAgent):

    def __init__(self, kb_dir: str = "knowledge_base"):
        # kb_dir 可注入，方便测试时换成 tests/fixtures 下的测试知识库（16.10/16.11）
        self._kb_dir = kb_dir

    @property
    def name(self) -> str:
        return "knowledge_agent"

    @property
    def system_prompt(self) -> str:
        # 瘦身：不再把检索策略写死在这里，改为引导 LLM 先加载 knowledge-base-rag 技能，
        # 按其中的决策树操作。检索规范的"单一事实来源"是那份 SKILL.md，不是这段 prompt。
        return (
            "你是团队内部技术知识库检索专家，服务于编码类子任务——"
            "为 code_writer / code_reviewer / debugger 供给团队的内部 API 规范、"
            "编码规范、错误码约定、架构约定。\n"
            "当需要检索知识库时，**先调用 get_skill_guide('knowledge-base-rag') 获取检索操作指南**，"
            "再严格按指南里的决策树驱动 search_knowledge_base 工具（评估→改写→重试→带来源归因）。"
            "绝不凭空编造 API 或规范；知识库里没有的，如实说明。"
        )

    @property
    def tools(self):
        # 只需挂 search_knowledge_base；get_skill_guide 由 SubAgent.run() 恒定注入（见 9.5）。
        return [KnowledgeBaseTool(kb_dir=self._kb_dir)]
```

在 `coordinator/dispatcher.py` 的 `_get_sub_agents()` 里注册它：

```python
# coordinator/dispatcher.py

def _get_sub_agents() -> dict:
    from sub_agents.code_writer import CodeWriterAgent
    from sub_agents.code_reviewer import CodeReviewerAgent
    from sub_agents.debugger import DebuggerAgent
    from sub_agents.test_writer import TestWriterAgent
    from sub_agents.knowledge_agent import KnowledgeAgent
    return {
        "code_writer":     CodeWriterAgent(),
        "code_reviewer":   CodeReviewerAgent(),
        "debugger":        DebuggerAgent(),
        "test_writer":     TestWriterAgent(),
        "knowledge_agent": KnowledgeAgent(),
    }
```

再让 `coordinator/planner.py` 的规划提示词知道有这么一个子 Agent 可用——在
`_COORDINATOR_SYSTEM` 里补一条：

```
- knowledge_agent：检索团队内部 API 规范、编码规范、错误码等技术约定。
  当编码 / 审查任务需要遵循内部规范时，先用它查出规范，再把结果作为上下文
  交给 code_writer / code_reviewer（作为它们的前置依赖任务）。
```

这样"按我们内部规范写一个调用用户服务的函数"这类请求，规划阶段会生成**两步有依赖的任务**，例如：

```json
{
  "tasks": [
    {"id": 1, "agent": "knowledge_agent",
     "input": "检索内部 HTTP 客户端规范、统一响应结构和错误码约定"},
    {"id": 2, "agent": "code_writer",
     "input": "按检索到的规范，写一个调用用户服务、带分页的函数",
     "depends_on": [1]}
  ]
}
```

`dispatch()` 会先跑 `knowledge_agent`。它在自己的 Agentic Loop 里会**先 `get_skill_guide('knowledge-base-rag')`**
拿到检索操作指南，再按指南驱动 `search_knowledge_base`（评估→必要时改写重试→带来源整理），
把检索到的规范作为 `context` 注入到 `code_writer` 的输入里（`SubAgent.run(context=...)`，见 6.4 节），
于是生成的代码就会遵循 `HttpClient` 封装、统一响应结构和错误码——跟审查代码、写测试走的是完全
相同的分发和聚合逻辑，不需要再单独维护一套"给单 Agent 挂工具"的旁路实现。

> **这就是"RAG 作为 Skill"的完整闭环**：LLM 决定要用知识库 → `get_skill_guide('knowledge-base-rag')`
> 加载操作文档 → 按文档里的决策树执行 `search_knowledge_base`。和第 9 章加载团队规范
> 用的是同一套 `get_skill_guide` 机制，RAG 的操作规范也只是众多 Skill 中的一个。

> **对比之前的错配**：如果知识库里装的是"退换货政策"这类内容，planner 面对编码请求
> 根本不会分派给 `knowledge_agent`，这个 RAG 就成了摆设。把知识库换成技术规范、并让
> `knowledge_agent` 明确服务于编码子任务后，它才真正嵌入到 Coordinator 的编码主线里。

---

## 15.12 测试：直接测试 Agent 效果

### 快速验证加载

新建 `test_kb_load.py`：

```python
# test_kb_load.py — 运行：python test_kb_load.py
import asyncio
from tools.knowledge_base import KnowledgeBaseTool

async def main():
    kb = KnowledgeBaseTool(kb_dir="knowledge_base")
    await kb.ensure_loaded()
    print(f"\n加载完成：{len(kb._docs)} 个文档块")
    for doc in kb._docs:
        loc = doc.title_path or doc.title
        print(f"  [{doc.source}] «{loc}»  字符数={len(doc.text)}")

asyncio.run(main())
```

```bash
python test_kb_load.py
```

预期输出（两份 .md 按标题切成多个片段，不再是"一份文档一个块"）：
```
  [KnowledgeBase] 发现 2 个文件（文本 2，PDF 0，图片 0）
  [KnowledgeBase] 加载完成，共 9 个文档块
  [KnowledgeBase] 双路索引完成：9 个向量（BAAI/bge-small-zh-v1.5，512 维）+ BM25 稀疏索引

加载完成：9 个文档块
  [knowledge_base\api_spec.md] «内部 HTTP 客户端规范 > 统一请求封装»  字符数=...
  [knowledge_base\api_spec.md] «内部 HTTP 客户端规范 > 认证»  字符数=...
  [knowledge_base\api_spec.md] «内部 HTTP 客户端规范 > 统一响应结构»  字符数=...
  [knowledge_base\api_spec.md] «内部 HTTP 客户端规范 > 分页约定»  字符数=...
  [knowledge_base\coding_style.md] «Python 编码规范 > 命名»  字符数=...
  [knowledge_base\coding_style.md] «Python 编码规范 > 类型注解»  字符数=...
  [knowledge_base\coding_style.md] «Python 编码规范 > 异步»  字符数=...
  [knowledge_base\coding_style.md] «Python 编码规范 > 异常处理»  字符数=...
  [knowledge_base\coding_style.md] «统一错误码表»  字符数=...
```

### 验证检索效果

```python
# 追加到 test_kb_load.py 末尾，或新建文件运行

import asyncio, json
from tools.knowledge_base import KnowledgeBaseTool

async def test_search():
    kb = KnowledgeBaseTool(kb_dir="knowledge_base")

    # 故意用和原文不同的说法，验证"语义匹配"
    queries = [
        ("怎么发 HTTP 请求？",       "api_spec"),
        ("变量和类应该怎么命名？",   "coding_style"),
    ]

    for query, expected_source in queries:
        result = await kb.execute({"query": query, "top_k": 1})
        data = json.loads(result)
        top = data["results"][0] if data.get("found") else None
        ok = top and expected_source in top["source"]
        print(f"  {'✓' if ok else '✗'}  查询「{query}」→ {top['source'] if top else '无结果'}")

asyncio.run(test_search())
```

### 测试 Agent 完整效果

```bash
python cli.py
```

输入一个"需要遵循内部规范"的编码请求，观察 Agent 是否先查知识库再写代码：
```
你：请按我们内部的 API 规范，写一个调用用户服务、带分页的函数。
```

预期（规划器先派 knowledge_agent 查规范，再交给 code_writer 按规范写）：
```
  [Planner] 生成 2 个任务：knowledge_agent（查规范）→ code_writer（依赖任务 1）
  [Loop] knowledge_agent 第 1 轮，执行 1 个工具调用（get_skill_guide → knowledge-base-rag）
  [Loop] knowledge_agent 第 2 轮，执行 1 个工具调用（search_knowledge_base）
Agent：（生成的代码通过 core.http.HttpClient 发请求，返回值按 code/message/data 解析，
       分页用 page/page_size —— 完全遵循检索到的内部规范）
```

> 注意第 1 轮是 `get_skill_guide('knowledge-base-rag')`——knowledge_agent 先加载检索操作指南，
> 第 2 轮才按指南调 `search_knowledge_base`。这正是"RAG 作为 Skill 按需加载"的闭环。

---

## 15.13 本章检查清单

```
□ 运行：python -c "from docling.document_converter import DocumentConverter; import PIL; from sentence_transformers import SentenceTransformer; from qdrant_client import QdrantClient; print('OK')"  → 输出 OK
□ Qdrant 已启动：curl http://localhost:6333/healthz 返回 healthz check passed
  （或改用 qdrant_url=":memory:" 免容器）
□ 运行 test_kb_load.py，知识库正常加载并建双路索引，输出多个文档块（两份 .md 按标题切成 9 段）+ 双路索引完成日志
□ 检索测试通过：查询「怎么发 HTTP 请求？」返回 api_spec 文档（语义匹配，非字面匹配）
□ skills/ 目录有 knowledge-base-rag.md，其 name+description 出现在技能索引表里
□ cli.py 提"按内部规范写代码"，日志显示规划出 knowledge_agent → code_writer 两步任务，
  且 knowledge_agent 先调 get_skill_guide("knowledge-base-rag") 再调 search_knowledge_base
  （验证 RAG 作为 Skill 按需加载的闭环，而非把检索策略预置在 system_prompt）
□ （可选）在 knowledge_base/ 放一个 PDF，重跑加载，确认提取了图片描述，
  且表格被完整保留为 Markdown 结构、矢量图表也生成了描述（而不仅仅是内嵌位图）
□ （可选）在 knowledge_base/ 放一张 PNG 图片，确认生成了文字描述
□ 验证视觉 Provider 独立配置生效：临时设 VISION_PROVIDER=openai + OPENAI_MODEL=deepseek-chat
  （纯文本模型），重跑图片加载，应看到 get_vision_provider() 抛出 ValueError 而不是静默返回空描述；
  改回 VISION_PROVIDER=（留空）或指向支持视觉的模型后恢复正常
```

---

## 15.14 进阶：训练无关的动态分组与稀疏通信裁剪（CONCAT 范式）

> 本节复用 15.9 节已经搭好的 `bge-small-zh-v1.5` embedding 能力，给第 6 章的 Coordinator 和
> 第 7 章的 Swarm 各叠加一层"按相关性裁剪通信"的优化。它是**纯运行时算法，不涉及任何训练**，
> 因此和第 17 章"RL 只能训本地小模型、不能训 Claude/GPT"的硬约束完全兼容，可以共存。

### 背景：为什么现有编排在"通信"上是浪费的

2026 年多智能体工程的一条主线是**稀疏通信**——不是所有 Agent 都需要看到所有其他 Agent 的
输出。代表性工作 CONCAT（arXiv:2605.29612，2026-05）提出一套训练无关的框架：一致性聚类选出
leader → 用 Theory-of-Mind 启发式预测"谁和谁协作有收益" → 据此裁剪掉大部分通信边，构成稀疏
拓扑。其报告的收益是相对全连接辩论范式效率（准确率/延迟比）最高提升约 2 倍。

对照 PolyCoder 现状，有两处正是它批评的"静态全连接/广播"：

- **Coordinator**（`coordinator/dispatcher.py` 的 `_run_one`）：把前置任务的**全部**结果原样
  塞进下游的 `context`——`context = {dep: prior_results[dep] for dep in spec.depends_on ...}`。
  哪怕前置产出里只有一小段和下游相关，也整段注入，既增加噪音也多烧 token。
- **Swarm**（`swarm/blackboard.py`）：白板全局共享、谁先来谁认领，没有"这批高度相似的任务其实
  做一次就够"的判断。

> **和第 17 章 `RouterAgent` 的分工**：别把两者搞混。`RouterAgent` 解决的是"**这一个请求该
> 派给哪个子 Agent**"的单步分类问题；本节解决的是"**多个子 Agent / 子任务之间该怎么分组、谁
> 该看到谁的结果**"的协调层问题。作用域不同，互补共存。

### 改造一：Coordinator 侧——按相关性裁剪注入的上下文

思路：下游任务真正需要的，往往只是前置产出里语义相关的片段。在 `_run_one` 注入 context 前，
先用 embedding 给"当前任务描述 vs 每段前置结果"打个相关性分，低于阈值的做截断而非全量拼接。
阈值直接沿用 15.11 节 `knowledge-base-rag.md` 已经定义好的经验值，保持全项目一致：

```python
# coordinator/dispatcher.py（在 6.7 节版本上增量修改 _run_one）

from tools.knowledge_base import _embed   # 复用 15.9 节已有的归一化 embedding


def _relevance(a: str, b: str) -> float:
    """两段文本的余弦相似度。_embed 返回的是 L2 归一化向量，点积即 cosine。"""
    va, vb = _embed([a[:2000], b[:2000]])   # 截断防止超长文本拖慢编码
    return sum(x * y for x, y in zip(va, vb))


def _prune_context(task_input: str, raw_context: dict[str, str]) -> dict[str, str]:
    """
    按与当前任务的相关性裁剪前置结果的注入量：
      relevance > 0.5   → 全量注入（高度相关）
      0.3 ~ 0.5         → 截断到前 500 字（相关但不必全给）
      < 0.3             → 只留一行摘要提示，不注入正文（几乎无关）
    注意：这里是"任务↔前置产出"两段文本的直接 cosine（0~1），与 15.11 检索返回的
    rrf_score（融合名次分，量级完全不同）不是一回事，阈值各自独立，勿混用。
    """
    pruned = {}
    for dep_id, text in raw_context.items():
        score = _relevance(task_input, text)
        if score > 0.5:
            pruned[dep_id] = text
        elif score >= 0.3:
            pruned[dep_id] = text[:500] + "…（已按相关性截断）"
        else:
            pruned[dep_id] = f"（前置任务 {dep_id} 的产出与本任务相关度低，已省略，如需可回溯）"
    return pruned
```

在 `_run_one` 里把原来的一行替换掉：

```python
    # 原来：context = {dep: prior_results[dep] for dep in spec.depends_on if dep in prior_results}
    raw_context = {dep: prior_results[dep] for dep in spec.depends_on if dep in prior_results}
    context = _prune_context(spec.input, raw_context) if raw_context else None
```

单个子任务链路（数个前置）额外多几次 embedding 编码，`bge-small-zh-v1.5` 在 CPU 上是毫秒级，
相对一次 LLM 调用可忽略；省下的是注入 LLM 的无关 token（既降本又减少干扰）。

### 改造二：Swarm 侧——对同类型 pending 任务做一致性聚类

思路：当短时间内涌入多条**高度相似**的同类型任务（如批量提交的相似代码审查），没必要每条都
独立跑一遍。给 `Blackboard` 加一个轻量的聚类方法：对同类型 pending 任务的 payload 做 embedding，
用"相似度阈值 + 连通分量"聚类（**不引入任何新聚类库**），每簇选一个代表任务作 leader 真正执行，
其余在 leader 完成后直接复用其结果。

```python
# swarm/blackboard.py（在第 7 章版本上新增方法）

    def cluster_pending(self, task_type: str, threshold: float = 0.85) -> list[list[str]]:
        """
        把同类型的 pending 任务按 payload 相似度聚类，返回 [[task_id, ...], ...]。
        只有相似度 >= threshold（默认 0.85，即"几乎重复"）才归为一簇——阈值刻意设高，
        宁可不聚也不要把"相似但不同"的任务错误合并。
        """
        from tools.knowledge_base import _embed
        pend = [t for t in self._tasks.values()
                if t.type == task_type and t.status == "pending"]
        if len(pend) < 2:
            return [[t.id] for t in pend]

        texts = [str(t.payload)[:2000] for t in pend]
        vecs = _embed(texts)
        n = len(pend)

        # 并查集求连通分量：相似度 >= threshold 的两任务连一条边
        parent = list(range(n))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        for i in range(n):
            for j in range(i + 1, n):
                if sum(a * b for a, b in zip(vecs[i], vecs[j])) >= threshold:
                    parent[find(i)] = find(j)

        clusters: dict[int, list[str]] = {}
        for i, t in enumerate(pend):
            clusters.setdefault(find(i), []).append(t.id)
        return list(clusters.values())
```

消费循环里的用法（示意）：拿到簇后，只让每簇的第一个任务正常 `claim()` 执行，其余标记为
"跟随者"，在 leader `complete()` 时把结果复制过去。**这是可选优化**，仅在"同类型任务短时间
大量涌入"时才有意义——建议加一个触发条件（如某类型 pending 数 > 5 才调 `cluster_pending`），
平时不启用，避免给正常的低并发场景增加无谓开销。

### 刻意不做：多轮 leader 重选

CONCAT 原论文的完整流程包含"聚类→选主→裁剪→重复 m-1 轮"的多轮迭代。**本节只取"一次聚类 +
一次裁剪"的简化版**，不做多轮。理由：PolyCoder 单次请求的任务粒度是数个到十几个子任务，
远小于论文验证的多 Agent 大规模辩论场景，多轮迭代的收益撑不起它的复杂度和额外延迟——这与
15.3.4 节"有明确收益才升级"的一贯判断一致。

### 验证

```bash
# 场景 ①（Coordinator 上下文裁剪）：构造一个前置产出很长、但只有一小段和下游相关的请求，
#   对比改造前后 SessionMetrics 的 total_input_tokens 应明显下降，且下游结果质量不降。
curl -X POST http://localhost:8002/ask -H "Content-Type: application/json" \
  -d '{"question": "先详细讲解整个订单模块的架构，然后只针对其中的分页参数写一个校验函数"}'

# 场景 ②（Swarm 聚类去重）：短时间内提交 5 条几乎相同的 code_review 任务，
#   观察日志应显示它们被聚成一簇，只有 leader 真正跑 LLM，其余复用结果。
for i in 1 2 3 4 5; do
  curl -X POST http://localhost:8002/swarm/ask -H "Content-Type: application/json" \
    -d '{"task_type":"code_review","payload":"审查 def add(a,b): return a-b 是否有逻辑错误"}' &
done; wait
```

---

# 第 16 章：阶段 16 —— 自动化测试模块（Agent 指标收集）

## 16.1 为什么要自动化测试

没有自动化测试，你只能靠手动运行 `cli.py` 感受 Agent 是否正常，
无法量化 Agent 的能力边界，也不知道修改代码后是否引入了退步。

本章解决三个问题：

| 问题 | 解决方案 |
|---|---|
| Agent 回答质量怎么衡量？ | SessionMetrics 收集延迟、token、工具调用次数等指标 |
| RAG 检索准不准？ | 用已知文档写断言：给定问题，验证返回的是预期文档 |
| Agent 会不会用对工具？ | 给 Agent 一个需要查知识库的问题，断言 metrics 里有工具调用记录 |

---

## 16.2 本章新增内容

```
新增文件：
core/metrics.py                        ← 会话指标收集器
tests/
├── conftest.py                        ← pytest 配置和公共 fixtures
├── fixtures/
│   └── knowledge_base/
│       ├── test_api_spec.md           ← 测试用知识库：内部 API 规范（内容固定，可重复测试）
│       └── test_coding_style.md       ← 测试用知识库：编码规范 + 错误码
├── test_metrics.py                    ← 指标单元测试（不调 LLM）
├── test_rag.py                        ← RAG 检索质量测试（不调 LLM）
├── test_tool_accuracy.py              ← 工具调用准确率测试（调 LLM）
└── test_agent_e2e.py                  ← Agent 端到端集成测试（调 LLM）

修改文件：
agent_core/loop.py     ← 在 LLM 调用和工具执行处埋点，收集 metrics
coordinator/agent.py   ← AskResult 新增 metrics 字段
pyproject.toml   ← pytest 异步配置
```

---

## 16.3 核心概念讲解

### 16.3.1 什么是"埋点"

**埋点**（Instrumentation）是指在代码关键节点插入计时和计数代码，收集运行时数据。

比如在 `run_agent_loop` 里：
- LLM 调用前记录时间戳 `t0 = time.time()`
- LLM 调用后算出耗时 `duration = (time.time() - t0) * 1000`
- 把耗时记录到 `TurnRecord` 对象

这样每次 Agent 运行结束后，你就得到了完整的性能数据。

### 16.3.2 测试分层

本章的测试分两层：

```
第一层：不需要调用 LLM（快速，可以在没有 API Key 时运行）
  test_metrics.py  → 验证 SessionMetrics 的计算逻辑
  test_rag.py      → 验证向量检索（embedding + Qdrant 内存模式）是否返回正确文档

第二层：需要调用 LLM（较慢，需要 .env 里配置了 API Key）
  test_tool_accuracy.py → 验证 Agent 对需要查知识库的问题会调用工具
  test_agent_e2e.py     → 验证完整流程：提问 → 工具 → 回答
```

**运行建议：**
- 日常开发：只跑第一层（`pytest tests/test_metrics.py tests/test_rag.py`），秒级完成
- 提交代码前：跑全部（`pytest tests/`），确保没有退步

---

## 16.4 会话指标收集器 `core/metrics.py`

```python
# core/metrics.py
"""
轻量指标收集器。

在 Agentic Loop 的关键节点埋点，运行结束后打印统计数字。
这些数字可以用于：
  1. 量化 Agent 的性能（延迟、费用）
  2. 写自动化测试的断言（如：工具调用了几次？Token 消耗合理吗？）
  3. 填简历时的量化成果
"""

import time
from dataclasses import dataclass, field


@dataclass
class TurnRecord:
    """一轮 LLM 调用的记录（一轮 = LLM 从接收消息到返回响应）。"""

    turn_index: int
    """第几轮（从 0 开始）"""

    duration_ms: float
    """LLM 调用耗时（毫秒）。
    从发出请求到收到完整响应。"""

    input_tokens: int
    """本轮输入的 token 数（包含历史消息、system prompt、工具定义）。"""

    output_tokens: int
    """本轮 LLM 输出的 token 数。"""

    cache_read_tokens: int
    """从 Prompt Cache 命中读取的 token 数（Anthropic 专属）。
    命中缓存的 token 只收约 10% 的费用，可以大幅降低成本。"""

    cache_write_tokens: int
    """写入 Prompt Cache 的 token 数。"""

    tool_calls: list[str]
    """本轮发起的工具调用名称列表，如 ['search_knowledge_base']。"""

    stop_reason: str
    """LLM 停止的原因：
    'end_turn'  → 正常完成，不再需要工具
    'tool_use'  → 需要执行工具，Loop 继续
    'max_tokens' → 达到 token 上限，回答被截断（通常是问题）
    """


@dataclass
class ToolRecord:
    """一次工具调用的记录。"""

    tool_name: str
    """工具名称，如 'search_knowledge_base'。"""

    duration_ms: float
    """工具执行耗时（毫秒）。"""

    success: bool
    """工具是否执行成功。"""

    error: str = ""
    """如果失败，这里存错误信息。"""


@dataclass
class SessionMetrics:
    """一次完整 Agent 会话的指标汇总。"""

    session_id: str
    """会话 ID，用于追踪和日志关联。"""

    question: str
    """用户的原始问题。"""

    turns: list[TurnRecord] = field(default_factory=list)
    """所有轮次的 LLM 调用记录。"""

    tool_records: list[ToolRecord] = field(default_factory=list)
    """所有工具调用记录（跨所有轮次）。"""

    _start_time: float = field(default_factory=time.time, repr=False)
    """会话开始时间戳，用于计算端到端延迟。"""

    # ── 聚合属性（自动从 turns 和 tool_records 计算）────────────────────────

    @property
    def total_duration_ms(self) -> float:
        """会话总耗时（毫秒），从创建 SessionMetrics 到调用此属性。"""
        return (time.time() - self._start_time) * 1000

    @property
    def total_input_tokens(self) -> int:
        """所有轮次的输入 token 总数。"""
        return sum(t.input_tokens for t in self.turns)

    @property
    def total_output_tokens(self) -> int:
        """所有轮次的输出 token 总数。"""
        return sum(t.output_tokens for t in self.turns)

    @property
    def total_cache_read_tokens(self) -> int:
        """从缓存命中的 token 总数。"""
        return sum(t.cache_read_tokens for t in self.turns)

    @property
    def cache_hit_rate(self) -> float:
        """Prompt Cache 命中率（命中 token / 总输入 token）。
        0.0 表示完全没有命中，1.0 表示全部命中（不可能发生）。"""
        total = self.total_input_tokens
        if total == 0:
            return 0.0
        return self.total_cache_read_tokens / total

    @property
    def avg_llm_latency_ms(self) -> float:
        """平均每轮 LLM 调用耗时（毫秒）。"""
        if not self.turns:
            return 0.0
        return sum(t.duration_ms for t in self.turns) / len(self.turns)

    @property
    def total_tool_calls(self) -> int:
        """工具被调用的总次数（跨所有轮次）。"""
        return len(self.tool_records)

    @property
    def avg_tool_latency_ms(self) -> float:
        """平均工具执行耗时（毫秒）。"""
        if not self.tool_records:
            return 0.0
        return sum(t.duration_ms for t in self.tool_records) / len(self.tool_records)

    @property
    def tool_names_called(self) -> list[str]:
        """按调用顺序列出所有被调用的工具名（含重复）。"""
        return [r.tool_name for r in self.tool_records]

    @property
    def estimated_cost_usd(self) -> float:
        """
        按 Claude claude-sonnet-4-6 定价估算费用（USD）。

        定价（每百万 token）：
          输入  $3.00
          缓存命中 $0.30（约 10%）
          输出  $15.00

        注意：这是估算值，实际价格以 Anthropic 官网为准。
        """
        normal_input = self.total_input_tokens - self.total_cache_read_tokens
        cache_read = self.total_cache_read_tokens

        return (
            normal_input * 3.0 / 1_000_000
            + cache_read * 0.3 / 1_000_000
            + self.total_output_tokens * 15.0 / 1_000_000
        )

    # ── 报告输出 ─────────────────────────────────────────────────────────────

    def print_report(self):
        """打印可截图的统计报告。运行结束后调用。"""
        divider = "─" * 52
        print(f"\n{divider}")
        print(f"  会话统计报告")
        print(f"{divider}")
        print(f"  问题：{self.question[:40]}{'...' if len(self.question) > 40 else ''}")
        print(f"  会话 ID：{self.session_id}")
        print(divider)
        print(f"  LLM 调用轮次   : {len(self.turns)} 轮")
        print(f"  平均 LLM 延迟  : {self.avg_llm_latency_ms:.0f} ms")
        print(f"  总端到端延迟   : {self.total_duration_ms:.0f} ms")
        print(divider)
        print(f"  总输入 Token   : {self.total_input_tokens:,}")
        print(f"  总输出 Token   : {self.total_output_tokens:,}")
        if self.total_cache_read_tokens > 0:
            print(f"  缓存命中 Token : {self.total_cache_read_tokens:,}"
                  f"  ({self.cache_hit_rate:.0%})")
        print(f"  估算费用       : ${self.estimated_cost_usd:.5f} USD")
        print(divider)
        if self.tool_records:
            print(f"  工具调用次数   : {self.total_tool_calls} 次")
            print(f"  平均工具延迟   : {self.avg_tool_latency_ms:.0f} ms")
            for rec in self.tool_records:
                status = "✓" if rec.success else "✗"
                print(f"    {status} {rec.tool_name}  ({rec.duration_ms:.0f} ms)")
        print(divider)

    def to_dict(self) -> dict:
        """转成字典，方便写入日志或 JSON 文件。"""
        return {
            "session_id": self.session_id,
            "question": self.question,
            "total_turns": len(self.turns),
            "total_tool_calls": self.total_tool_calls,
            "tool_names": self.tool_names_called,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "avg_llm_latency_ms": round(self.avg_llm_latency_ms, 1),
            "total_duration_ms": round(self.total_duration_ms, 1),
            "estimated_cost_usd": round(self.estimated_cost_usd, 6),
        }
```

---

## 16.5 在 Agentic Loop 中埋点 `agent_core/loop.py`

在现有 `agent_core/loop.py` 的基础上添加 metrics 收集。
这里给出完整的替换版本，便于直接对比：

**修改点 1：顶部添加导入**

在文件顶部的 `import` 区域，加入：

```python
import time
import uuid
from core.metrics import SessionMetrics, TurnRecord, ToolRecord
```

**修改点 2：LoopResult 新增 metrics 字段**

找到 `@dataclass class LoopResult`，在最后加一个字段：

```python
@dataclass
class LoopResult:
    """Agentic Loop 的最终结果。"""
    text: str
    total_usage: Usage
    turn_count: int
    stop_reason: str
    metrics: SessionMetrics | None = None     # ← 新增
```

**修改点 3：`run_agent_loop` 函数签名新增参数**

```python
async def run_agent_loop(
    prompt: str,
    provider: BaseProvider,
    system: str = "",
    tools: list[ToolDefinition] | None = None,
    executor: ToolExecutor | None = None,
    max_turns: int = 10,
    max_tokens: int = 4096,
    on_text_delta: Callable[[str], None] | None = None,
    session_id: str | None = None,           # ← 新增
) -> LoopResult:
```

**修改点 4：函数体开头初始化 metrics**

在 `initial_messages = [...]` 之后，`state = LoopState(...)` 之前，加入：

```python
    # 初始化 metrics 收集器
    _metrics = SessionMetrics(
        session_id=session_id or str(uuid.uuid4())[:8],
        question=prompt,
    )
```

**修改点 5：LLM 调用处加计时**

找到非流式分支里的 `response = await provider.chat(...)` 这一块，
改成：

```python
        else:
            # 非流式模式：等待完整响应
            _t_llm = time.time()
            response = await provider.chat(
                messages=list(state.messages),
                system=system,
                tools=tools or None,
                max_tokens=max_tokens,
            )
            _llm_duration_ms = (time.time() - _t_llm) * 1000

            for block in response.content:
                if isinstance(block, TextBlock):
                    text_chunks.append(block.text)
                elif isinstance(block, ToolUseBlock):
                    tool_calls.append(block)

            turn_usage = response.usage

            # 记录本轮 LLM 调用
            _metrics.turns.append(TurnRecord(
                turn_index=state.turn_count,
                duration_ms=_llm_duration_ms,
                input_tokens=turn_usage.input_tokens,
                output_tokens=turn_usage.output_tokens,
                cache_read_tokens=turn_usage.cache_read_tokens,
                cache_write_tokens=turn_usage.cache_write_tokens,
                tool_calls=[b.name for b in tool_calls],
                stop_reason=response.stop_reason or "end_turn",
            ))
```

**修改点 6：工具执行处加计时**

找到 `tool_results = await executor.execute_all(tool_calls)` 附近，
把执行过程改成逐个计时：

```python
        # loop.py 顶部已 from observability.logging import logger
        logger.info("loop_turn_tools", turn=state.turn_count, tool_count=len(tool_calls))

        # 逐个执行工具并记录耗时（并发执行，各自计时）
        import asyncio as _asyncio

        async def _timed_execute(tc):
            t0 = time.time()
            result_block = (await executor.execute_all([tc]))[0]
            elapsed = (time.time() - t0) * 1000
            _metrics.tool_records.append(ToolRecord(
                tool_name=tc.name,
                duration_ms=elapsed,
                success=not result_block.is_error,
                error="" if not result_block.is_error else result_block.content[:200],
            ))
            return result_block

        tool_results = list(await _asyncio.gather(*[_timed_execute(tc) for tc in tool_calls]))
```

**修改点 7：所有 return LoopResult(...) 处加入 metrics**

共有三处 `return LoopResult(...)`，每处都在末尾加 `metrics=_metrics`：

```python
        # 轮次限制
        return LoopResult(
            text=f"（已达最大轮次限制 {max_turns}，任务可能未完成）",
            total_usage=state.total_usage,
            turn_count=state.turn_count,
            stop_reason=STOP_MAX_TURNS,
            metrics=_metrics,      # ← 加这一行
        )

        # 正常完成
        return LoopResult(
            text="".join(text_chunks),
            total_usage=state.total_usage,
            turn_count=state.turn_count,
            stop_reason=STOP_COMPLETED,
            metrics=_metrics,      # ← 加这一行
        )

        # 无 executor 错误
        return LoopResult(
            text="（错误：Agent 决定使用工具，但未配置 ToolExecutor）",
            total_usage=state.total_usage,
            turn_count=state.turn_count,
            stop_reason=STOP_ABORTED,
            metrics=_metrics,      # ← 加这一行
        )
```

---

## 16.6 更新 `coordinator/agent.py` 暴露指标

`agent/agent.py` 已经删除了，`AskResult` 现在定义在 `coordinator/agent.py`。
跟单 Agent 版本不同的是，Coordinator 一次 `run()` 背后是多次 LLM 调用（规划一次 +
每个子 Agent 各一次），每次调用如果都用 16.5 埋点后的 `run_agent_loop`，会各自产出
一个 `SessionMetrics`——所以这里要做的不是简单地把某一次 `result.metrics` 传下去，
而是把它们合并成一份汇总指标：

```python
# core/metrics.py（新增一个合并函数）

def merge_metrics(session_id: str, question: str, parts: list[SessionMetrics]) -> SessionMetrics:
    """把多个子调用（规划 + 各子 Agent）各自的 SessionMetrics 合并成一份汇总。"""
    merged = SessionMetrics(session_id=session_id, question=question)
    for part in parts:
        merged.turns.extend(part.turns)
        merged.tool_records.extend(part.tool_records)
    return merged
```

```python
# coordinator/agent.py — AskResult 加 metrics 字段，run() 里合并各子调用的指标

from core.metrics import SessionMetrics, merge_metrics   # ← 新增导入

@dataclass
class AskResult:
    text: str
    input_tokens: int
    output_tokens: int
    turn_count: int = 1
    metrics: SessionMetrics | None = None  # ← 新增字段


class CoordinatorAgent:
    async def run(self, user_request: str, session_id: str | None = None) -> AskResult:
        ...
        # make_plan() 内部的规划调用、dispatch() 里每个子 Agent 的调用，
        # 都各自产出一个 SessionMetrics（16.5 埋点后 run_agent_loop 的返回值），
        # 这里统一收集起来再合并
        metrics_parts: list[SessionMetrics] = [plan_result.metrics]
        if tasks:
            metrics_parts.extend(sub_result.metrics for sub_result in dispatch_results)

        total = _sum_usage(usages)
        return AskResult(
            text=text,
            input_tokens=total.input_tokens,
            output_tokens=total.output_tokens,
            metrics=merge_metrics(session_id or "anonymous", user_request, metrics_parts),
        )
```

> 这里省略了 `make_plan`/`dispatch` 具体怎么把 `SessionMetrics` 一路传出来的
> 管道代码——思路跟本章前面给 usage 加汇总（`_sum_usage`）完全一样：`make_plan`
> 额外返回一份 metrics，`dispatch`/`_run_one` 也一样，最后在 `run()` 里统一合并。

---

## 16.7 测试夹具

### 测试用知识库文档

创建目录：

```bash
mkdir -p tests/fixtures/knowledge_base
```

> 测试文档要和 15.10 的定位一致——装**技术规范**，而不是客服问答。下面两篇分别对应
> "内部 API 规范"和"编码规范"，用来验证向量检索在技术知识场景下的语义匹配。

新建 `tests/fixtures/knowledge_base/test_api_spec.md`：

```markdown
# PolyCoder 内部 API 规范

## 统一请求封装
所有对外请求必须通过 core.http.HttpClient，禁止直接使用 requests 或 httpx。
HttpClient 内置超时（默认 10 秒）、重试（指数退避，最多 3 次）和链路追踪 header。

## 统一响应结构
内部接口返回 JSON，固定结构为 code、message、data 三个字段。
code 等于 0 表示成功，data 是业务数据；code 非 0 表示失败，需抛出 ApiError。

## 分页约定
列表接口统一使用 page（从 1 开始）和 page_size（默认 20，最大 100）两个参数。
返回的 data 里包含 items 列表和 total 总数。
```

新建 `tests/fixtures/knowledge_base/test_coding_style.md`：

```markdown
# PolyCoder 编码规范

## 命名与类型
函数和变量用 snake_case，类用 PascalCase，常量用 UPPER_SNAKE_CASE。
所有公开函数必须写完整的类型注解，优先使用 list、dict 等内置泛型。

## 异步规范
IO 操作一律使用 async await，禁止在协程里做阻塞调用。
并发多个协程用 asyncio.gather，不要创建不受管理的裸 Task。

## 错误码约定
参数校验失败返回错误码 1001，未认证返回 1002，资源不存在返回 2001，
服务内部错误返回 5001，上游服务超时返回 5002。
```

### pytest 配置文件 `tests/conftest.py`

```python
# tests/conftest.py
"""
pytest 公共配置和 fixtures。

Fixture 是什么：
  pytest 里的 fixture 类似于"测试准备步骤"。
  比如"每个测试都需要一个干净的知识库工具"，
  可以写成一个 fixture，测试函数在参数里声明需要它，
  pytest 就会自动创建并传进去。
"""

import pytest
from pathlib import Path


# 测试用知识库目录的绝对路径（跟着这个文件走，不依赖工作目录）
FIXTURES_KB_DIR = Path(__file__).parent / "fixtures" / "knowledge_base"


@pytest.fixture
def test_kb_dir() -> str:
    """返回测试用知识库目录的路径字符串。"""
    return str(FIXTURES_KB_DIR)


@pytest.fixture
async def loaded_kb(test_kb_dir):
    """
    返回一个已加载测试文档的 KnowledgeBaseTool 实例。

    用法（在测试函数里声明参数名即可）：
        async def test_something(loaded_kb):
            result = await loaded_kb.execute({"query": "怎么发 HTTP 请求"})

    这里用 qdrant_url=":memory:" 走纯内存向量库，测试不依赖 Docker 起的 Qdrant 服务，
    且每个测试实例互相隔离、退出即清空。
    """
    from tools.knowledge_base import KnowledgeBaseTool
    kb = KnowledgeBaseTool(kb_dir=test_kb_dir, qdrant_url=":memory:")
    await kb.ensure_loaded()
    return kb
```

> **首次跑测试会下载 embedding 模型**（~100MB），之后走本地缓存。CI 环境建议预热缓存
> 或提前 `export HF_ENDPOINT=https://hf-mirror.com`。

在 `pyproject.toml` 里加入 pytest 的 asyncio 配置（避免每个异步测试都要手写 `asyncio.run()`）：

```toml
# pyproject.toml — 在 [project] 同级别加入以下内容

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

---

## 16.8 指标单元测试 `tests/test_metrics.py`

这批测试**完全不需要调用 LLM**，可以在没有 API Key 的环境下运行。

```python
# tests/test_metrics.py
"""
SessionMetrics 单元测试。

测试所有聚合属性的计算逻辑，不调用 LLM。
每个 test_ 函数是一个独立测试用例。
"""

import pytest
from core.metrics import SessionMetrics, TurnRecord, ToolRecord


def make_turn(
    turn_index: int = 0,
    duration_ms: float = 500.0,
    input_tokens: int = 100,
    output_tokens: int = 50,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    tool_calls: list[str] | None = None,
    stop_reason: str = "end_turn",
) -> TurnRecord:
    """辅助函数：快速创建 TurnRecord，测试里常用。"""
    return TurnRecord(
        turn_index=turn_index,
        duration_ms=duration_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        tool_calls=tool_calls or [],
        stop_reason=stop_reason,
    )


def make_metrics(question: str = "测试问题") -> SessionMetrics:
    """辅助函数：创建空的 SessionMetrics。"""
    return SessionMetrics(session_id="test-001", question=question)


# ── 基础属性测试 ──────────────────────────────────────────────────────────────


def test_empty_metrics_zero_values():
    """没有任何记录时，所有聚合属性应该为 0。"""
    m = make_metrics()
    assert m.total_input_tokens == 0
    assert m.total_output_tokens == 0
    assert m.total_tool_calls == 0
    assert m.avg_llm_latency_ms == 0.0
    assert m.cache_hit_rate == 0.0


def test_token_accumulation():
    """多轮 LLM 调用的 token 应该累加。"""
    m = make_metrics()
    m.turns.append(make_turn(input_tokens=200, output_tokens=50))
    m.turns.append(make_turn(input_tokens=300, output_tokens=80))
    m.turns.append(make_turn(input_tokens=400, output_tokens=100))

    assert m.total_input_tokens == 900
    assert m.total_output_tokens == 230


def test_avg_llm_latency():
    """平均 LLM 延迟应该是所有轮次延迟的均值。"""
    m = make_metrics()
    m.turns.append(make_turn(duration_ms=1000.0))
    m.turns.append(make_turn(duration_ms=500.0))
    m.turns.append(make_turn(duration_ms=750.0))

    # (1000 + 500 + 750) / 3 = 750
    assert abs(m.avg_llm_latency_ms - 750.0) < 0.01


# ── Cache 命中率测试 ──────────────────────────────────────────────────────────


def test_cache_hit_rate_no_cache():
    """没有缓存命中时，命中率应该为 0。"""
    m = make_metrics()
    m.turns.append(make_turn(input_tokens=1000, cache_read_tokens=0))
    assert m.cache_hit_rate == 0.0


def test_cache_hit_rate_calculation():
    """命中率 = 缓存命中 token / 总输入 token。"""
    m = make_metrics()
    m.turns.append(make_turn(input_tokens=1000, cache_read_tokens=800))
    # 命中率 = 800 / 1000 = 0.8
    assert abs(m.cache_hit_rate - 0.8) < 0.001


def test_cache_hit_rate_multi_turn():
    """多轮时命中率基于所有轮次的总量计算。"""
    m = make_metrics()
    m.turns.append(make_turn(input_tokens=500, cache_read_tokens=400))
    m.turns.append(make_turn(input_tokens=500, cache_read_tokens=300))
    # 命中率 = (400 + 300) / (500 + 500) = 0.7
    assert abs(m.cache_hit_rate - 0.7) < 0.001


# ── 工具调用统计测试 ──────────────────────────────────────────────────────────


def test_tool_call_count():
    """tool_records 的数量应该等于 total_tool_calls。"""
    m = make_metrics()
    m.tool_records.append(ToolRecord("search_kb", 200.0, True))
    m.tool_records.append(ToolRecord("search_kb", 180.0, True))
    m.tool_records.append(ToolRecord("get_weather", 300.0, False, "网络超时"))

    assert m.total_tool_calls == 3


def test_tool_names_called():
    """tool_names_called 应该按顺序列出所有工具名。"""
    m = make_metrics()
    m.tool_records.append(ToolRecord("search_kb", 200.0, True))
    m.tool_records.append(ToolRecord("run_python", 150.0, True))

    assert m.tool_names_called == ["search_kb", "run_python"]


def test_avg_tool_latency():
    """平均工具延迟应该是所有工具调用延迟的均值。"""
    m = make_metrics()
    m.tool_records.append(ToolRecord("tool_a", 100.0, True))
    m.tool_records.append(ToolRecord("tool_b", 300.0, True))
    # (100 + 300) / 2 = 200
    assert abs(m.avg_tool_latency_ms - 200.0) < 0.01


# ── 费用估算测试 ──────────────────────────────────────────────────────────────


def test_cost_estimation_no_cache():
    """没有缓存命中时，按正常输入价格计算费用。"""
    m = make_metrics()
    # 输入 1000 token，输出 100 token
    m.turns.append(make_turn(input_tokens=1000, output_tokens=100, cache_read_tokens=0))

    # 期望：1000 * 3/1e6 + 100 * 15/1e6 = 0.003 + 0.0015 = 0.0045
    assert abs(m.estimated_cost_usd - 0.0045) < 0.0001


def test_cost_estimation_with_cache():
    """缓存命中的 token 按较低价格计算（约 10%）。"""
    m = make_metrics()
    # 输入 1000 token，其中 800 来自缓存
    m.turns.append(make_turn(
        input_tokens=1000,
        output_tokens=0,
        cache_read_tokens=800,
    ))
    # 正常输入：200 token，缓存：800 token
    # 费用：200 * 3/1e6 + 800 * 0.3/1e6 = 0.0006 + 0.00024 = 0.00084
    assert abs(m.estimated_cost_usd - 0.00084) < 0.00001


# ── 输出测试 ──────────────────────────────────────────────────────────────────


def test_print_report_does_not_crash():
    """print_report() 不应该抛异常（即使数据为空）。"""
    m = make_metrics()
    m.print_report()  # 不抛异常即为通过


def test_print_report_with_data_does_not_crash():
    """有数据时 print_report() 也不应该抛异常。"""
    m = make_metrics("北京今天天气怎么样？")
    m.turns.append(make_turn(
        input_tokens=500, output_tokens=100, cache_read_tokens=300,
        tool_calls=["get_weather"], stop_reason="tool_use",
    ))
    m.turns.append(make_turn(
        input_tokens=600, output_tokens=80,
        stop_reason="end_turn",
    ))
    m.tool_records.append(ToolRecord("get_weather", 412.0, True))
    m.print_report()


def test_to_dict_contains_required_keys():
    """to_dict() 应该包含所有关键字段。"""
    m = make_metrics("测试")
    d = m.to_dict()

    required_keys = {
        "session_id", "question", "total_turns", "total_tool_calls",
        "tool_names", "total_input_tokens", "total_output_tokens",
        "cache_hit_rate", "avg_llm_latency_ms", "total_duration_ms",
        "estimated_cost_usd",
    }
    assert required_keys.issubset(set(d.keys()))
```

---

## 16.9 RAG 检索质量测试 `tests/test_rag.py`

这批测试使用固定的测试文档，验证向量检索的正确性，**不调用对话 LLM**。
（注意：会用到本地 embedding 模型 bge-small-zh-v1.5，首次运行需下载权重；向量库走 `:memory:` 内存模式，无需 Docker。）

```python
# tests/test_rag.py
"""
RAG 检索质量测试。

使用 tests/fixtures/knowledge_base/ 里的已知文档，
验证 embedding + Qdrant 向量检索对不同查询的返回结果是否符合预期。
不调用对话 LLM；embedding 用本地开源模型，向量库走内存模式，运行较快。
"""

import pytest
import json


# ── 加载测试 ──────────────────────────────────────────────────────────────────


async def test_kb_loads_test_documents(loaded_kb):
    """知识库应该能正确加载测试目录里的文档，并按标题切成多个 chunk。"""
    # fixtures 里有 2 个 .md，每个有多个 ## 小节 → 切分后 chunk 数应明显多于文件数
    # （不写死具体数字，避免切分策略微调时测试变脆）
    assert len(loaded_kb._docs) > 2, "文档应被切成多个 chunk，而不是一份文档一个块"


async def test_kb_documents_have_content(loaded_kb):
    """加载的文档应该有非空内容。"""
    for doc in loaded_kb._docs:
        assert len(doc.text) > 10, f"文档 {doc.source} 内容为空"
        assert doc.title, f"文档 {doc.source} 标题为空"


async def test_kb_documents_have_correct_source(loaded_kb):
    """文档的 source 字段应该包含文件路径。"""
    sources = [doc.source for doc in loaded_kb._docs]
    # 验证两个测试文档都被加载了
    has_api_spec = any("test_api_spec" in s for s in sources)
    has_coding_style = any("test_coding_style" in s for s in sources)
    assert has_api_spec, "test_api_spec.md 没有被加载"
    assert has_coding_style, "test_coding_style.md 没有被加载"


# ── 检索准确率测试 ────────────────────────────────────────────────────────────


async def test_retrieve_api_spec_by_http_query(loaded_kb):
    """查询发请求相关问题，应该返回 API 规范文档（而不是编码规范文档）。"""
    # 用"发 HTTP 请求"这种说法，原文写的是"对外请求 / HttpClient"，验证语义匹配
    result = await loaded_kb.execute({"query": "怎么发 HTTP 请求？", "top_k": 1})
    data = json.loads(result)

    assert data["found"] is True, "应该找到相关文档"
    top_source = data["results"][0]["source"]
    assert "test_api_spec" in top_source, (
        f"请求封装问题应该返回 API 规范文档，实际返回：{top_source}"
    )


async def test_retrieve_coding_style_by_naming_query(loaded_kb):
    """查询命名规范，应该返回编码规范文档。"""
    result = await loaded_kb.execute({"query": "变量和类应该怎么命名", "top_k": 1})
    data = json.loads(result)

    assert data["found"] is True
    top_source = data["results"][0]["source"]
    assert "test_coding_style" in top_source, (
        f"命名规范问题应该返回编码规范文档，实际返回：{top_source}"
    )


async def test_retrieve_async_convention(loaded_kb):
    """查询异步/并发规范，应该返回编码规范文档。"""
    result = await loaded_kb.execute({"query": "并发执行多个协程的正确姿势", "top_k": 1})
    data = json.loads(result)

    assert data["found"] is True
    assert "test_coding_style" in data["results"][0]["source"]


async def test_response_structure_content_mentions_code_field(loaded_kb):
    """查询统一响应结构，检索结果应该包含 code/data 等约定信息。"""
    result = await loaded_kb.execute({"query": "接口返回的数据结构是什么样的", "top_k": 1})
    data = json.loads(result)

    assert data["found"] is True
    content = data["results"][0]["content"]
    # test_api_spec.md 里写了 code / data 字段约定
    assert "code" in content or "data" in content or "JSON" in content, (
        f"响应结构应该包含字段约定信息，实际内容：{content[:100]}"
    )


# ── 边界情况测试 ──────────────────────────────────────────────────────────────


async def test_unrelated_query_returns_not_found_or_low_score(loaded_kb):
    """与知识库内容完全无关的查询，不应该返回高相关度结果。"""
    # 这个问题与 API 规范/编码规范完全无关
    result = await loaded_kb.execute({"query": "量子力学薛定谔方程", "top_k": 1})
    data = json.loads(result)

    if data.get("found"):
        # 低于 _RRF_MIN 的会被判为未命中；即使命中，融合置信度也应偏低
        assert data["results"][0]["rrf_score"] < 0.02, (
            f"无关查询的融合置信度过高：{data['results'][0]['rrf_score']}"
        )


async def test_top_k_respected(loaded_kb):
    """top_k 参数应该限制返回结果的数量。"""
    result = await loaded_kb.execute({"query": "规范", "top_k": 1})
    data = json.loads(result)

    if data.get("found"):
        assert len(data["results"]) <= 1, "top_k=1 时最多返回 1 个结果"


async def test_execute_empty_query_does_not_crash(loaded_kb):
    """空查询不应该让程序崩溃。"""
    result = await loaded_kb.execute({"query": ""})
    data = json.loads(result)
    # 不崩溃即可，found 可以是 True 或 False
    assert "found" in data or "error" in data
```

---

## 16.10 工具调用准确率测试 `tests/test_tool_accuracy.py`

这批测试**需要调用 LLM**，验证 Agent 对特定类型的问题会选择正确的工具。

```python
# tests/test_tool_accuracy.py
"""
工具调用准确率测试。

给 Agent 提出明确需要查知识库的问题，
验证 Agent 实际调用了 search_knowledge_base 工具。

这批测试需要调用 LLM，比单元测试慢（每个测试约 3-10 秒）。
需要 .env 里配置了有效的 API Key。
"""

import pytest
from coordinator.agent import CoordinatorAgent


def _patch_knowledge_agent(monkeypatch, test_kb_dir):
    """
    把 dispatcher 里注册的 knowledge_agent 换成指向测试知识库目录的实例，
    不影响真实的 knowledge_base/ 文件夹。15.11 里 KnowledgeAgent 支持传 kb_dir，
    就是为了这里能这样注入测试夹具。
    """
    import coordinator.dispatcher as dispatcher_module
    from sub_agents.knowledge_agent import KnowledgeAgent

    agents = dispatcher_module._get_sub_agents()
    agents["knowledge_agent"] = KnowledgeAgent(kb_dir=test_kb_dir)
    monkeypatch.setattr(dispatcher_module, "_agents", agents)


async def test_coding_task_with_spec_triggers_kb_tool(test_kb_dir, monkeypatch):
    """要求"按内部规范"写代码时，规划器应该先派 knowledge_agent 查规范，触发 search_knowledge_base 工具。"""
    _patch_knowledge_agent(monkeypatch, test_kb_dir)

    coordinator = CoordinatorAgent()
    result = await coordinator.run("请按我们团队的内部 API 规范，写一个调用用户服务的函数。")

    assert result.metrics is not None, "metrics 不应该为 None"
    assert "search_knowledge_base" in result.metrics.tool_names_called, (
        f"涉及内部规范的编码任务应该触发知识库工具，实际工具调用：{result.metrics.tool_names_called}"
    )


async def test_coding_style_question_triggers_kb_tool(test_kb_dir, monkeypatch):
    """问团队编码规范时，同样应该路由到 knowledge_agent 并触发工具调用。"""
    _patch_knowledge_agent(monkeypatch, test_kb_dir)

    coordinator = CoordinatorAgent()
    result = await coordinator.run("我们团队的命名和类型注解规范是怎么要求的？")

    assert result.metrics is not None
    assert "search_knowledge_base" in result.metrics.tool_names_called, (
        f"编码规范问题应该触发知识库工具，实际：{result.metrics.tool_names_called}"
    )


async def test_simple_question_does_not_need_tool():
    """
    普通问题（不需要查知识库的）应该走 direct_reply，不经过任何子 Agent，
    自然也就没有工具调用——规划器判断这类问题不需要拆解成任务。
    """
    coordinator = CoordinatorAgent()
    result = await coordinator.run("1 加 1 等于几？")

    assert result.metrics is not None
    assert not result.metrics.tool_names_called, (
        f"数学问题不应该触发任何工具调用，实际：{result.metrics.tool_names_called}"
    )
    assert "2" in result.text, f"1+1 应该等于 2，实际回答：{result.text}"
```

---

## 16.11 Agent 端到端集成测试 `tests/test_agent_e2e.py`

```python
# tests/test_agent_e2e.py
"""
Agent 端到端集成测试。

验证从用户提问到最终回答的完整流程：
  用户提问 → Agentic Loop → 工具调用 → 最终回答 + 指标收集

这批测试调用真实 LLM，需要 API Key。
"""

import pytest
from coordinator.agent import CoordinatorAgent, AskResult
from core.metrics import SessionMetrics


def _patch_knowledge_agent(monkeypatch, test_kb_dir):
    """同 16.10：把 knowledge_agent 换成指向测试知识库目录的实例。"""
    import coordinator.dispatcher as dispatcher_module
    from sub_agents.knowledge_agent import KnowledgeAgent

    agents = dispatcher_module._get_sub_agents()
    agents["knowledge_agent"] = KnowledgeAgent(kb_dir=test_kb_dir)
    monkeypatch.setattr(dispatcher_module, "_agents", agents)


# ── 基础对话测试 ──────────────────────────────────────────────────────────────


async def test_ask_returns_ask_result():
    """CoordinatorAgent.run() 应该返回 AskResult 类型，不崩溃。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("用一句话解释什么是 Python")
    assert isinstance(result, AskResult)


async def test_ask_returns_non_empty_text():
    """回答不应该为空。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("用一句话解释什么是 Python")
    assert len(result.text) > 10, f"回答太短：{result.text}"


async def test_ask_returns_chinese_response():
    """Agent 应该用中文回答（system prompt 里要求了）。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("What is Python?")
    has_chinese = any("一" <= ch <= "鿿" for ch in result.text)
    assert has_chinese, f"应该包含中文，实际回答：{result.text[:100]}"


# ── 指标收集测试 ──────────────────────────────────────────────────────────────


async def test_metrics_populated_after_ask():
    """run() 完成后，metrics 应该被填充（规划调用 + 直接回复各自的 SessionMetrics 已被 merge_metrics 合并）。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("用一句话解释什么是机器学习")
    assert result.metrics is not None, "metrics 不应该为 None"
    assert isinstance(result.metrics, SessionMetrics)


async def test_metrics_has_at_least_one_turn():
    """至少应该有一轮 LLM 调用记录（规划本身也是一轮）。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("2 + 2 等于多少？")
    assert len(result.metrics.turns) >= 1


async def test_metrics_input_tokens_positive():
    """输入 token 数应该大于 0。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("你好")
    assert result.metrics.total_input_tokens > 0


async def test_metrics_output_tokens_positive():
    """输出 token 数应该大于 0。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("你好")
    assert result.metrics.total_output_tokens > 0


async def test_metrics_latency_positive():
    """LLM 调用延迟应该大于 0（毫秒）。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("你好")
    assert result.metrics.avg_llm_latency_ms > 0


# ── 知识库工具集成测试 ────────────────────────────────────────────────────────


async def test_kb_query_triggers_tool_call(test_kb_dir, monkeypatch):
    """
    知识库相关问题应该被规划器路由给 knowledge_agent，触发工具调用，
    且回答基于知识库内容。

    这是最重要的集成测试：验证 RAG 完整链路：
    提问 → 规划器识别为需查内部规范 → 分发给 knowledge_agent → 检索到相关规范 → 基于规范回答
    """
    _patch_knowledge_agent(monkeypatch, test_kb_dir)

    coordinator = CoordinatorAgent()
    result = await coordinator.run("我们团队内部规定对外请求要怎么发？有什么封装要求？")

    assert result.metrics is not None
    # 验证工具被调用了
    assert result.metrics.total_tool_calls >= 1, "应该调用了至少一次工具"
    assert "search_knowledge_base" in result.metrics.tool_names_called

    # 验证回答基于知识库内容（test_api_spec.md 里写了 HttpClient 封装）
    assert len(result.text) > 20, "回答不应该太短"


async def test_kb_answer_contains_relevant_info(test_kb_dir, monkeypatch):
    """
    基于知识库的回答应该包含文档里的具体信息，而不是泛泛而谈。
    """
    _patch_knowledge_agent(monkeypatch, test_kb_dir)

    coordinator = CoordinatorAgent()
    result = await coordinator.run("内部接口统一的响应结构是什么样的？")

    assert result.metrics is not None
    # test_api_spec.md 里写了 code / message / data 三个字段约定
    assert "code" in result.text or "data" in result.text or "message" in result.text, (
        f"关于响应结构的回答应该包含字段约定，实际：{result.text[:200]}"
    )


# ── 指标合理性测试 ────────────────────────────────────────────────────────────


async def test_multi_turn_when_tool_used(test_kb_dir, monkeypatch):
    """
    当子 Agent 使用工具时，聚合后的 metrics.turns 长度应该 >= 2
    （规划这一轮 + knowledge_agent 内部至少一轮工具调用轮次）。

    注意：CoordinatorAgent.AskResult.turn_count 不再是"单个 Agent 循环的轮次数"
    （Coordinator 本身涉及多次 LLM 调用），这里改成检查合并后的 metrics.turns，
    语义更准确。
    """
    _patch_knowledge_agent(monkeypatch, test_kb_dir)

    coordinator = CoordinatorAgent()
    result = await coordinator.run("内部接口的分页参数是怎么约定的？")

    if result.metrics.total_tool_calls > 0:
        assert len(result.metrics.turns) >= 2, (
            f"调用了工具但只有 {len(result.metrics.turns)} 轮记录，应该 >= 2"
        )


async def test_cost_estimate_is_reasonable():
    """估算费用应该在合理范围内（不为 0，不超过 1 美元）。"""
    coordinator = CoordinatorAgent()
    result = await coordinator.run("你好，请问你是什么？")
    cost = result.metrics.estimated_cost_usd
    assert cost > 0, "费用估算不应该为 0"
    assert cost < 1.0, f"单次问答费用超过 1 美元，异常：${cost:.5f}"
```

---

## 16.12 运行测试与读懂输出

### 安装测试依赖（如果还没安装）

```bash
uv add --dev pytest pytest-asyncio
```

### 运行第一层测试（不需要 LLM，速度快）

```bash
pytest tests/test_metrics.py tests/test_rag.py -v
```

预期输出（全部通过）：
```
tests/test_metrics.py::test_empty_metrics_zero_values PASSED
tests/test_metrics.py::test_token_accumulation PASSED
tests/test_metrics.py::test_avg_llm_latency PASSED
tests/test_metrics.py::test_cache_hit_rate_no_cache PASSED
...
tests/test_rag.py::test_kb_loads_test_documents PASSED
tests/test_rag.py::test_retrieve_api_spec_by_http_query PASSED
tests/test_rag.py::test_retrieve_coding_style_by_naming_query PASSED
...
==================== 20 passed in 4.60s ====================
```

### 运行第二层测试（需要 LLM，较慢）

```bash
# 确保 .env 里配置了 API Key
pytest tests/test_tool_accuracy.py tests/test_agent_e2e.py -v
```

预期输出：
```
tests/test_agent_e2e.py::test_ask_returns_ask_result PASSED
tests/test_agent_e2e.py::test_metrics_has_at_least_one_turn PASSED
tests/test_agent_e2e.py::test_kb_query_triggers_tool_call PASSED
...
==================== 12 passed in 47.83s ====================
```

### 运行全部测试

```bash
pytest tests/ -v
```

### 读懂测试失败信息

如果某个测试失败，pytest 会打印详细原因，例如：

```
FAILED tests/test_rag.py::test_retrieve_api_spec_by_http_query
AssertionError: 请求封装问题应该返回 API 规范文档，实际返回：test_coding_style.md

解读：向量检索返回了错误的文档。
可能原因：test_api_spec.md 的语义与「怎么发 HTTP 请求」查询不够贴近，或两篇测试文档语义太接近难以区分。
修复方向：检查 test_api_spec.md 的内容，让请求封装规范描述更完整具体；必要时调低 _RRF_MIN、调宽 _RECALL_N，或换更强的 embedding 模型（如 bge-base-zh-v1.5）。
```

### 在 cli.py 里打印指标报告

在 `cli.py` 里调用 `metrics.print_report()`，每次对话后看实时数据：

```python
# cli.py — 在 ask() 调用之后加两行

result = await ask(question)
print(result.text)
if result.metrics:
    result.metrics.print_report()   # ← 加这一行
```

运行 `python cli.py`，每次问答后会自动打印：

```
────────────────────────────────────────────────────
  会话统计报告
────────────────────────────────────────────────────
  问题：按我们内部 API 规范写一个调用用户服务的函数
  会话 ID：a3f1b2c4
────────────────────────────────────────────────────
  LLM 调用轮次   : 2 轮
  平均 LLM 延迟  : 1340 ms
  总端到端延迟   : 3210 ms
────────────────────────────────────────────────────
  总输入 Token   : 2,341
  总输出 Token   : 178
  估算费用       : $0.00972 USD
────────────────────────────────────────────────────
  工具调用次数   : 1 次
  平均工具延迟   : 5 ms
    ✓ search_knowledge_base  (5 ms)
────────────────────────────────────────────────────
```

---

## 16.13 失败结构化分类（MAST 三分类）

> 本节把可观测性从"这次失败了"提升到"这类失败为什么系统性发生"。它给 16.4 节的
> `ToolRecord`/`TurnRecord` 加一个可选的归类字段，配合第 11 章已有的 structlog/Loki，
> 就能在 Grafana 里做跨会话的失败模式统计（查询写法见 18.2 节）。

### 为什么需要归类：MAST 分类学

学术界（MAST 失败分类学，arXiv:2503.13657，NeurIPS 2025，标注一致性 Cohen's Kappa=0.88）
把多 Agent 系统的失败归纳为三大类。我们取其可直接落地的简化版：

| 类别 | 含义 | PolyCoder 里的典型例子 |
|---|---|---|
| `spec_flaw` | 规范 / 规划缺陷 | Planner 漏写 `depends_on`，导致下游任务被跳过（见 18.1） |
| `misalignment` | Agent 间失配 | 上游子 Agent 的产出被下游误解，或工具调用格式对不上、重试才成功 |
| `verification_failure` | 任务验证失败 | RAG 检索到了但相关度低，没二次校验就当真，导致后续答案错 |

16.4 节的 `ToolRecord.error` 只回答了"这次成没成"，没回答"属于哪一类"。加上分类维度后，
"某类失败反复出现"才会浮出水面，才谈得上有针对性地改进（比如 `verification_failure` 高就该
去调 15.11 节的 rrf_score 阈值）。

### 给 `core/metrics.py` 加 `failure_category` 字段

在 16.4 节的 `ToolRecord` / `TurnRecord` 上各加一个**可选**字段，默认 `None` 表示"未失败或
未分类"——对老代码零影响：

```python
# core/metrics.py（在 16.4 节版本上增量修改）

from typing import Literal

# 三类失败的类型别名（也可以用 str，这里用 Literal 让 IDE 有补全和校验）
FailureCategory = Literal["spec_flaw", "misalignment", "verification_failure"]


@dataclass
class ToolRecord:
    tool_name: str
    duration_ms: float
    success: bool
    error: str = ""
    failure_category: FailureCategory | None = None   # ← 新增：失败归类，成功时留空


@dataclass
class TurnRecord:
    # ...（原有字段全部不变）...
    failure_category: FailureCategory | None = None   # ← 新增：本轮若失败，归到哪一类
```

再加一个**克制的**分类函数——只覆盖能从现有信息直接判定的规则，判不了的就返回 `None`，
绝不硬猜（硬猜会污染统计）：

```python
# core/metrics.py（新增函数）

def classify_failure(
    error: str = "",
    result_text: str = "",
    retried_then_succeeded: bool = False,
    adopted_low_relevance: bool = False,
) -> FailureCategory | None:
    """
    从已有信号里判定失败类别；判不了返回 None（不追求 100% 覆盖）。

    调用方按自己掌握的信号传参，例如：
      - Coordinator 的 dispatcher 捕获到"前置任务失败被跳过" → 传 error
      - executor 发现工具调用重试后才成功                     → retried_then_succeeded=True
      - knowledge_agent 采纳了 rrf_score<0.01 的检索结果       → adopted_low_relevance=True
    """
    if "前置任务" in error or "MISSING_CONTEXT" in result_text:
        return "spec_flaw"            # 依赖链断了，属于规划缺陷
    if retried_then_succeeded:
        return "misalignment"         # 首次格式/语义对不上，重试才成——典型的 Agent 间失配
    if adopted_low_relevance:
        return "verification_failure" # 检索到了但没校验相关性就用
    return None
```

在 `SessionMetrics` 里补一个按类别计数的聚合，并让报告和 `to_dict()` 带上它：

```python
# core/metrics.py — SessionMetrics 内新增

    @property
    def failure_counts(self) -> dict[str, int]:
        """按 failure_category 统计次数（跨所有 turn 和 tool 记录），成功/未分类不计入。"""
        from collections import Counter
        cats = [t.failure_category for t in self.turns if t.failure_category]
        cats += [r.failure_category for r in self.tool_records if r.failure_category]
        return dict(Counter(cats))

    # print_report() 末尾追加（在原有工具明细之后）：
    #   if self.failure_counts:
    #       print(f"  失败分类       : {self.failure_counts}")
    # to_dict() 的返回字典里追加一项：
    #   "failure_counts": self.failure_counts,
```

### 落地范围与克制原则

- **能自动判的判，判不了的留空**：`classify_failure` 只写了三条最确定的规则。不要为了"每次
  失败都有分类"去堆启发式——错误的分类比没有分类更有害（会让 18.2 的趋势图产生误导）。
- 打标的位置在**调用方显式传信号**，而不是让 `classify_failure` 去猜。Coordinator 的
  dispatcher（18.1）、`agent_core/executor.py`（工具重试）、`knowledge_agent`（低相关度采纳）
  各自最清楚发生了什么，由它们传参最准。
- 这个字段一旦进了 structlog 日志（第 11 章的打点习惯），跨会话聚合查询就是 18.2 节的事了。

---

## 16.14 评测技能化：把测试模式沉淀为可复用 Skill

### 为什么："强编码能力 ≠ 会评估 Agent"

一个和直觉相反、但有实证支撑的发现（AWS AI Labs，arXiv:2605.11378，2026-06）：前沿编码模型
在**没有评估领域知识**时直接写 Agent 评估代码，首次可执行率只有约 30%，且倾向于"过度设计"
（一个 Agent 塞 12+ 个指标）。解法不是换更强的模型，而是把**评估领域知识封装成可复用的
"评测技能"**——程序化指令 + 可复用模板 + 决策树。

这正好套用第 9 章已经确立的机制：**操作规范都走 Skill，由 `get_skill_guide` 按需加载**。
16 章现在的测试（`test_metrics.py` / `test_rag.py` / `test_tool_accuracy.py` / `test_agent_e2e.py`）
是人工手写的项目定制断言，换一个新子 Agent / 新工具就得从零再写一遍，没有沉淀成方法论——
这恰是上面那篇论文批评的"临时手写"模式。把它做成一份 SKILL.md，就和 15.11 节的
`knowledge-base-rag.md` 走了同一条路。

### 新建 `skills/agent-eval-authoring.md`

> 这是一份"操作文档型"Skill，和 15.11 节的 `knowledge-base-rag.md` 放在同一个 `skills/` 目录、
> 同样进第 9 章的索引表、同样由 `get_skill_guide` 按需加载。它面向的是"写测试/评估代码的人"——
> 既是开发者本人，也可以是未来某个自动生成测试的子 Agent。

````markdown
---
name: agent-eval-authoring
description: 给 Agent / 工具写评估测试的操作指南——先判分层（要不要调 LLM），再按标准模板构造最小 fixture 和断言，避免过度设计。当需要为新子 Agent、新工具或新编排流程编写测试时加载。
---

# Agent 评估代码编写技能

## 一、先做分层判断（对应 16.3.2）

写任何一条测试前，先问："这条断言需要真的调 LLM 吗？"

| 你要验证的东西 | 分层 | 放哪个文件 |
|---|---|---|
| 指标计算、缓存命中率、费用估算、检索排序等**确定性逻辑** | 不调 LLM 的快测试 | `test_metrics.py` / `test_rag.py` |
| 工具是否被正确调用、参数对不对 | 不调 LLM（用 mock/固定轨迹） | `test_tool_accuracy.py` |
| 端到端回答质量、多轮编排是否正确 | 需调 LLM 的慢测试 | `test_agent_e2e.py` |

**默认优先写不调 LLM 的那层**——快、稳、不烧钱、可进 CI。只有确实要验证"模型判断质量"时
才落到端到端层。

## 二、标准步骤模板（给新子 Agent / 新工具写测试）

```
1. 构造最小 fixture
   · 只放验证这一条断言所必需的输入，不要堆无关数据
   · ground truth 写死在测试里，不依赖外部环境 / 工作目录

2. 断言"做了该做的事"（结构断言，优先）
   · assert "预期工具名" in metrics.tool_names_called
   · assert metrics.failure_counts == {} 或符合预期分类（用 16.13 的字段）

3. 断言"结果含关键要点"（语义断言，克制）
   · 断言输出里包含核心关键词 / 结构，而不是逐字匹配 LLM 措辞

4. 断言"没有跑偏"（负向断言）
   · assert 不该调的工具没被调用
   · assert 费用 / 轮次在合理上限内
```

## 三、红线（反模式）

- ❌ 一条测试里塞 12+ 个断言——拆成多条，每条只验证一个行为
- ❌ 断言依赖 LLM 的**具体措辞**（`assert "你好，我是" in text`）——模型换一版就挂；
     只断言**语义要点 / 结构**
- ❌ 为了凑覆盖率给每个指标都造断言——只测真正会出错、会回归的路径
- ❌ 端到端测试不设费用/轮次上限——必须有 `assert metrics.total_turns <= N` 之类的兜底
````

### 接入方式

只需把它加进第 9 章的 Skill 索引表（和 `knowledge-base-rag`、`security` 等并列），**不需要**改
`sub_agents/` 的注册——因为它是文档/知识型 Skill，不像 15.11 节的 `knowledge_agent` 那样还要
挂一个 `search_knowledge_base` 工具。加载路径就是标准的
`get_skill_guide('agent-eval-authoring')`。

---

## 16.15 本章检查清单

```
□ 运行 pytest tests/test_metrics.py -v，全部通过（不需要 API Key）
□ 准备了 tests/fixtures/knowledge_base/ 里的两个测试文档
□ 运行 pytest tests/test_rag.py -v，全部通过
□ 修改了 agent_core/loop.py，加入 metrics 埋点
□ 修改了 coordinator/agent.py，AskResult 有 metrics 字段
□ 运行 pytest tests/test_agent_e2e.py::test_ask_returns_ask_result -v，通过
□ 运行全部集成测试 pytest tests/ -v，无错误（或仅有预期的 skip）
□ 修改 cli.py 加入 metrics.print_report()，对话后能看到统计报告
□ （16.13）ToolRecord/TurnRecord 有可选的 failure_category 字段，print_report 能显示失败分类
□ （16.14）skills/agent-eval-authoring.md 已加入 Skill 索引表，可被 get_skill_guide 加载
```

---

# 第 17 章：阶段 17 —— 用强化学习训练本地路由子 Agent（SFT + GRPO）

> 本章参考 hello-agents 第十一章 Agentic-RL，把「强化学习训练一个小模型」这件事，落到 PolyCoder 真实可用的场景上。
> **前置**：一块 ≥8GB 显存的 NVIDIA GPU——本地没有也没关系，**本章默认路线是租一台按量计费的 GPU 服务器**（见 17.2，成本个位数人民币）；坚持用 Mac 本地训练见章末 17.12。此外已完成第 3 章（Provider 抽象层）和第 6 章（Coordinator）。
> **产出**：一个用 SFT + GRPO 训练出来的 Qwen3-0.6B「请求路由模型」，通过 OpenAI-compat 端点接入现有 Coordinator，**不改动主框架代码**。
> 每一小节都有独立的验证命令，跑通即算通过。

---

## 17.1 先想清楚：RL 能给 PolyCoder 加什么、不能加什么

在写任何代码之前，必须先破除一个常见误解。

**你不能对 Claude / GPT / Gemini 做强化学习训练。** 它们的权重在云端，你只能通过 API 调用，改不了一个参数。所以「用 RL 增强 agent」在 PolyCoder 这种**基于 API 的编排系统**里，唯一现实的落地方式是：

> **自己训练一个能跑在本地的小模型（Qwen3-0.6B），让它承担某个"专项、可自动评判"的任务，然后作为一个子 Agent 接进 Coordinator。**

这正好是第 11 章教的全套方法（SFT → GRPO → LoRA → 评估）的一次真实应用。

**为什么选「请求路由分类」作为训练任务？** 对比第 11 章的 GSM8K 数学题，好的 RL 训练任务需要满足三个条件，路由任务全部满足：

| 条件 | GSM8K（第 11 章） | 请求路由（本章） |
|------|------------------|-----------------|
| 答案可自动验证 | 数字对不对 | 路由的子 Agent label 对不对 |
| 有明确 ground truth | `#### 72` | `code_reviewer` |
| 小模型能学会 | 0.6B 够用 | 分类任务，0.6B 绰绰有余 |
| **在本项目里有用** | ❌（只是练手） | ✅ 真的能替代/辅助第 6 章的 planner 路由 |

**本章的数据流**（和第 11 章完全同构，只是把数学题换成路由）：

```
用户请求  ──►  路由模型  ──►  "Final Answer: code_reviewer"
   │                                    │
   │            奖励函数：label 命中 +1，格式正确 +0.2
   └────────────────────────────────────┘
                   GRPO 用这个奖励优化模型
```

训练完成后，它在 PolyCoder 里的位置：

```
用户 → Coordinator → [RouterAgent(本地Qwen)]  ← 本章新增
                        ↓ 输出路由标签
                     code_writer / code_reviewer / debugger / ...
```

> **一句话总结**：第 11 章教你"怎么训一个数学模型"，本章教你"把同一套方法用来训一个对 PolyCoder 有用的路由模型，并接回系统"。理论完全一致，只换了数据集和奖励的语义。

### 17.1 验证

无需代码，回答清楚以下三个问题即算理解到位（写进你自己的笔记）：

- [ ] 为什么不能直接对 Claude 做 GRPO？（答：权重在云端，无法反向传播）
- [ ] 路由任务的 ground truth 是什么？（答：正确的子 Agent 名字）
- [ ] 训练好的模型通过什么机制接回 PolyCoder？（答：OpenAI-compat 端点 + 第 3 章的 OpenAI Provider）

---

## 17.2 环境与依赖

第 11 章用的是 `hello_agents.tools.RLTrainingTool`（一个封装好的黑箱）。**本章不用它**——延续 v2「去掉黑箱、自实现」的理念，我们直接用 TRL 原生 API，每一步都看得见。

### 先租一台 GPU 服务器（本章默认路线）

本章的训练需要一块 NVIDIA 显卡。**如果你本地没有 N 卡（比如只有 Mac 或核显笔记本），最省事的做法是租一台按量计费的 GPU 服务器**——它天然是 Linux + CUDA 环境，下面 17.2–17.10 的代码一行都不用改。训练是「一次性」的：跑通、导出模型就结束，按小时租、跑完即关，成本极低。

> 本地就有 N 卡的读者可跳过本小节，直接在本地按同样步骤操作。用 Mac 的读者若坚持本地训练，见章末 17.12 的 Apple Silicon 适配（要改 5 处，较麻烦，不推荐）。

**租什么档次**：本章模型是 Qwen3-0.6B + LoRA，极小。SFT「几分钟内跑完」（17.5），GRPO 也就几十分钟，**任何一张消费级卡都绰绰有余，不需要 A100 这类高端卡**。

| 需求 | 推荐卡 | 说明 |
|------|--------|------|
| 推荐 | RTX 3090 / 4090（24GB） | AutoDL、恒源云、vast.ai 上最便宜的一档，约 1–2 元/小时 |
| 最低 | T4 / RTX 3060（12–16GB） | 也能跑，只是略慢；前置要求的 ≥8GB 显存指的就是这一档 |

> **成本预估**：整章从头跑通约 1–2 小时机时，按量计费总花费个位数人民币。**跑完务必释放/关闭实例**，否则空转也计费。

**关键思路：训练在云端，推理拉回本地。** 你不需要把整个 PolyCoder 搬上服务器。训练机只干「训练 + 导出模型」这一件事，17.9/17.10 的推理和接回主框架可以放回本地做（推理只跑 0.6B 模型，本地 Ollama / CPU 就够）：

```
云 GPU 服务器（临时）              本地开发机（长期）
┌─────────────────────┐         ┌──────────────────────┐
│ 上传 rl/ 目录         │         │  下载 router_merged/  │
│ 装 requirements-rl   │         │  接回 Coordinator      │
│ build→sft→grpo→merge │  ──►    │  （17.9 / 17.10）      │
└─────────────────────┘  权重~1.2GB └────────────────────┘
```

**上传训练文件到服务器**（用 `scp`，或 AutoDL 等平台自带的网页上传更省事）：

```bash
# 从本地把 rl/ 目录同步到服务器（按你的实例 IP / 端口改）
scp -P <端口> -r ./rl/ requirements-rl.txt <用户名>@<服务器IP>:~/polycoder-rl/
```

后续 17.3–17.8 都在这台服务器上执行；17.8 导出模型后再拉回本地（见 17.8 末尾）。

### 新增依赖

在项目根目录新建 `requirements-rl.txt`（和主 `requirements.txt` 分开，因为这些包很重，只在训练机上装）：

```txt
# requirements-rl.txt —— RL 训练专用依赖（只在有 GPU 的训练机安装）

torch>=2.3.0                # 深度学习框架（按你的 CUDA 版本装对应轮子）
transformers>=4.51.0        # Qwen3 需要 4.51+
trl>=0.17.0                 # SFTTrainer / GRPOTrainer（本章核心）
peft>=0.13.0                # LoRA 参数高效微调
datasets>=3.0.0             # 数据集加载与格式转换
accelerate>=1.0.0           # 训练加速与多卡支持
tensorboard>=2.17.0         # 离线训练曲线可视化（本章用它监控，不依赖 wandb）
```

在服务器上安装（本地有 N 卡则在本地虚拟环境安装）：

```bash
# 登录服务器后，在上传的目录里建虚拟环境
cd ~/polycoder-rl
python -m venv .venv && source .venv/bin/activate

# CUDA 12.1 的 torch 示例；云平台镜像通常已带对应 CUDA 的 torch，可先验证再决定是否重装
pip install torch --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements-rl.txt

# 国内服务器建议设 HuggingFace 镜像，否则下载基础模型会卡住
export HF_ENDPOINT=https://hf-mirror.com
```

### 训练目录骨架

在项目里新建一个 `rl/` 目录，专门放训练相关代码，和主框架隔离：

```
PolyCoder/
├── rl/
│   ├── __init__.py
│   ├── build_dataset.py    ← 17.3 构造路由数据集
│   ├── rewards.py          ← 17.4 奖励函数（纯函数，可单测）
│   ├── train_sft.py        ← 17.5 SFT 训练
│   ├── train_grpo.py       ← 17.7 GRPO 训练
│   ├── evaluate.py         ← 17.6 / 17.9 评估
│   ├── merge_lora.py       ← 17.8 合并导出
│   └── data/               ← 数据集 JSONL 存这里
└── sub_agents/
    └── router_agent.py     ← 17.10 接回 Coordinator
```

### 17.2 验证

```bash
# 1. 确认 GPU 可用（必须输出 True，以及你的显卡型号）
python -c "import torch; print('CUDA:', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NO GPU')"

# 2. 确认 TRL 两个 Trainer 都能导入
python -c "from trl import SFTTrainer, GRPOTrainer, SFTConfig, GRPOConfig; print('TRL OK')"

# 3. 确认能下载并加载基础模型（第一次会下载约 1.2GB）
python -c "from transformers import AutoTokenizer; t = AutoTokenizer.from_pretrained('Qwen/Qwen3-0.6B'); print('tokenizer OK, vocab=', t.vocab_size)"
```

预期：第 1 条输出 `CUDA: True` 和显卡名；第 2、3 条分别打印 `TRL OK` 和 `tokenizer OK`。三条全过才继续。

---

## 17.3 定义任务与构造数据集

第 11 章用的是现成的 GSM8K；我们要自己造一份「请求路由」数据集。因为是分类任务，几十条高质量样本就足够跑通并学到东西（0.6B 模型 + LoRA 很容易过拟合小数据，这对演示反而友好）。

### 路由标签体系

对应第 6 章你已有的子 Agent：

| 标签 | 触发场景 |
|------|---------|
| `code_writer` | 从零写代码、实现某功能 |
| `code_reviewer` | 审查已有代码、找问题、评估质量 |
| `debugger` | 报错了、修 bug、代码跑不通 |
| `test_writer` | 写测试、补充用例、覆盖率 |
| `knowledge_agent` | 问项目规范、API 文档、内部约定 |

### 数据集构造脚本 `rl/build_dataset.py`

我们造两种格式，和第 11 章的 SFT 格式 / RL 格式一一对应：

- **SFT 格式**：`messages` 对话（含完整推理和答案），教模型输出格式。
- **RL 格式**：只有 `prompt` + `ground_truth`（正确标签），让模型自己生成推理。

```python
# rl/build_dataset.py
"""构造 PolyCoder 请求路由数据集（SFT 格式 + RL 格式）。
对应第 11 章的 GSM8K，只是把数学题换成路由分类。"""
import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

LABELS = ["code_writer", "code_reviewer", "debugger", "test_writer", "knowledge_agent"]

SYSTEM_PROMPT = (
    "你是 PolyCoder 的请求路由器。阅读用户请求，判断应该交给哪个子 Agent 处理。"
    "可选标签：code_writer（写新代码）、code_reviewer（审查代码）、"
    "debugger（修 bug/报错）、test_writer（写测试）、knowledge_agent（问规范/文档）。"
    "先简短说明理由，最后一行必须是 `Final Answer: <标签>`。"
)

# 原始训练样本：(用户请求, 正确标签, 一句理由)
RAW = [
    ("帮我实现一个二分查找函数", "code_writer", "从零编写新功能，属于代码生成"),
    ("写一个读取 CSV 并求和的脚本", "code_writer", "需要产出新代码"),
    ("给我一个 FastAPI 的登录接口", "code_writer", "实现新接口，属于写代码"),
    ("实现一个 LRU 缓存类", "code_writer", "编写新的数据结构实现"),
    ("用 Python 画一个正弦曲线", "code_writer", "生成绘图代码"),
    ("帮我把这个函数写出来：计算阶乘", "code_writer", "编写新函数"),
    ("看看我这段代码有没有问题", "code_reviewer", "评估已有代码质量"),
    ("这个函数的可读性怎么样，帮我评审", "code_reviewer", "代码审查诉求"),
    ("检查这段代码是否符合最佳实践", "code_reviewer", "审查代码规范性"),
    ("我的实现有没有性能隐患？", "code_reviewer", "评估已有代码"),
    ("帮我 review 一下这个 PR", "code_reviewer", "典型的代码审查"),
    ("这段代码风格上有什么可以改进的", "code_reviewer", "审查代码风格"),
    ("运行报错 KeyError，帮我看看哪里错了", "debugger", "运行时报错，需要调试"),
    ("这段代码跑不通，一直死循环", "debugger", "代码无法正常运行"),
    ("为什么我的函数返回 None？", "debugger", "行为异常需排查"),
    ("修一下这个 IndexError", "debugger", "明确的报错修复"),
    ("程序崩溃了，栈里是 NullPointer", "debugger", "崩溃排查"),
    ("我的测试挂了，帮我定位原因", "debugger", "定位失败原因属于调试"),
    ("给这个函数补充单元测试", "test_writer", "编写测试用例"),
    ("帮我写 pytest 覆盖边界情况", "test_writer", "补充测试"),
    ("这个模块的测试覆盖率太低，加点用例", "test_writer", "提升测试覆盖"),
    ("为登录接口写一组集成测试", "test_writer", "编写测试"),
    ("给我几个针对这个类的测试样例", "test_writer", "生成测试用例"),
    ("补充异常路径的测试", "test_writer", "编写测试"),
    ("我们内部 API 请求怎么封装的？", "knowledge_agent", "询问项目规范"),
    ("项目的错误码约定是什么", "knowledge_agent", "查内部约定文档"),
    ("命名规范里驼峰还是下划线？", "knowledge_agent", "查编码规范"),
    ("统一响应结构长什么样", "knowledge_agent", "查 API 规范"),
    ("分页参数按什么约定传", "knowledge_agent", "查接口约定"),
    ("异步代码有什么规范要求", "knowledge_agent", "查编码规范"),
]

# 留几条做验证集（模型没见过，用来测泛化）
VAL_RAW = [
    ("帮我实现一个快速排序", "code_writer", "编写新算法"),
    ("审查一下这个类的设计合理吗", "code_reviewer", "代码审查"),
    ("抛了 TypeError，帮我排查", "debugger", "报错调试"),
    ("给这个工具函数加测试", "test_writer", "编写测试"),
    ("项目里日志规范是怎样的", "knowledge_agent", "查规范"),
]


def build_prompt(question: str):
    """构造 chat 格式的 prompt（system + user）。"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]


def build_completion(reason: str, label: str) -> str:
    return f"理由：{reason}。\nFinal Answer: {label}"


def dump_sft(raw, path):
    """SFT 格式：messages 含完整答案，教模型输出格式。"""
    with open(path, "w", encoding="utf-8") as f:
        for q, label, reason in raw:
            messages = build_prompt(q) + [
                {"role": "assistant", "content": build_completion(reason, label)}
            ]
            f.write(json.dumps({"messages": messages}, ensure_ascii=False) + "\n")


def dump_rl(raw, path):
    """RL 格式：只给 prompt + ground_truth，模型自己生成推理。"""
    with open(path, "w", encoding="utf-8") as f:
        for q, label, reason in raw:
            f.write(json.dumps({
                "prompt": build_prompt(q),
                "ground_truth": label,
            }, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    dump_sft(RAW, DATA_DIR / "sft_train.jsonl")
    dump_rl(RAW, DATA_DIR / "rl_train.jsonl")
    dump_rl(VAL_RAW, DATA_DIR / "val.jsonl")
    print(f"✓ SFT 训练集: {len(RAW)} 条 -> {DATA_DIR/'sft_train.jsonl'}")
    print(f"✓ RL  训练集: {len(RAW)} 条 -> {DATA_DIR/'rl_train.jsonl'}")
    print(f"✓ 验证集   : {len(VAL_RAW)} 条 -> {DATA_DIR/'val.jsonl'}")
```

### 17.3 验证

```bash
python rl/build_dataset.py

# 查看生成的样本（确认 messages 结构和 ground_truth 都在）
head -n 1 rl/data/sft_train.jsonl | python -m json.tool
head -n 1 rl/data/rl_train.jsonl | python -m json.tool
```

预期：打印三行 `✓`；`sft_train.jsonl` 第一条含 `messages`（3 条：system/user/assistant）；`rl_train.jsonl` 第一条含 `prompt` 和 `ground_truth: "code_writer"`。

---

## 17.4 奖励函数（纯函数，无需 GPU 即可验证）

奖励函数是 RL 的灵魂（第 11 章 11.2.2）。它是**纯 Python 函数**——不碰模型、不碰 GPU，所以可以先写单元测试验证逻辑正确，再拿去训练。这是最应该先做扎实的一步。

TRL 的 GRPO 奖励函数签名固定为 `reward(completions, **kwargs) -> list[float]`，其中 `completions` 是模型生成的文本列表，数据集里的其它列（如 `ground_truth`）通过 `kwargs` 传入——和第 11 章的自定义奖励函数签名完全一致。

```python
# rl/rewards.py
"""路由任务的奖励函数。对应第 11 章 11.2.2 的准确率/格式奖励。
纯函数，可脱离 GPU 单元测试。"""
import re
from typing import List

LABELS = {"code_writer", "code_reviewer", "debugger", "test_writer", "knowledge_agent"}


def extract_label(text: str) -> str:
    """从模型输出里提取路由标签。
    优先取 `Final Answer: xxx`，否则回退为文中出现的最后一个合法标签。"""
    m = re.search(r"Final Answer:\s*([a-z_]+)", text)
    if m and m.group(1) in LABELS:
        return m.group(1)
    # 回退：找文本里出现的最后一个合法标签
    found = [w for w in re.findall(r"[a-z_]+", text) if w in LABELS]
    return found[-1] if found else ""


def accuracy_reward(completions: List[str], ground_truth: List[str], **kwargs) -> List[float]:
    """准确率奖励：标签命中 +1，否则 0。对应 11.2.2(1)。"""
    return [1.0 if extract_label(c) == gt else 0.0
            for c, gt in zip(completions, ground_truth)]


def format_reward(completions: List[str], **kwargs) -> List[float]:
    """格式奖励：输出里有规范的 `Final Answer: <合法标签>` 就给 +0.2。
    对应 11.2.2(3) 步骤奖励的思路——鼓励可解析的结构化输出。"""
    out = []
    for c in completions:
        m = re.search(r"Final Answer:\s*([a-z_]+)", c)
        out.append(0.2 if (m and m.group(1) in LABELS) else 0.0)
    return out
```

> **为什么拆成两个奖励函数？** TRL 的 GRPOTrainer 接受 `reward_funcs=[fn1, fn2, ...]` 一个列表，会把每个函数的分数相加。这天然实现了第 11 章 11.2.2 结尾说的「组合奖励」（准确率 + 格式），而且比手写加权更清晰。

### 17.4 验证

新建 `rl/test_rewards.py`：

```python
# rl/test_rewards.py —— 运行：pytest rl/test_rewards.py -v（不需要 GPU）
from rl.rewards import extract_label, accuracy_reward, format_reward


def test_extract_from_final_answer():
    assert extract_label("理由：xxx。\nFinal Answer: debugger") == "debugger"

def test_extract_fallback():
    assert extract_label("我觉得应该是 code_reviewer 来处理") == "code_reviewer"

def test_extract_empty_on_garbage():
    assert extract_label("不知道") == ""

def test_accuracy_reward():
    comps = ["Final Answer: code_writer", "Final Answer: debugger"]
    gts = ["code_writer", "code_reviewer"]
    assert accuracy_reward(comps, ground_truth=gts) == [1.0, 0.0]

def test_format_reward():
    assert format_reward(["Final Answer: test_writer"]) == [0.2]
    assert format_reward(["随便瞎写"]) == [0.0]
```

```bash
pytest rl/test_rewards.py -v
```

预期：5 个测试全部 `PASSED`。奖励逻辑正确后，才值得花 GPU 时间去训练。

---

## 17.5 SFT 训练（LoRA + TRL SFTTrainer）

第 11 章 11.3 讲了为什么必须先 SFT：预训练模型不知道输出格式，直接上 GRPO 会失败。我们先用 SFT 让模型学会「先说理由，最后一行 `Final Answer: <标签>`」这个格式。

```python
# rl/train_sft.py
"""SFT 训练：教会模型路由任务的输出格式。对应第 11 章 11.3。"""
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import SFTConfig, SFTTrainer

BASE_MODEL = "Qwen/Qwen3-0.6B"
OUTPUT_DIR = "./models/router_sft"


def main():
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="bfloat16")

    # messages 格式的数据集，SFTTrainer 会自动套用 Qwen 的 chat template
    dataset = load_dataset("json", data_files="rl/data/sft_train.jsonl", split="train")

    # LoRA 配置（第 11 章 11.3.2）：只训练注意力层的低秩矩阵
    peft_config = LoraConfig(
        r=16,                # rank
        lora_alpha=32,       # 通常是 rank 的 2 倍
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        task_type="CAUSAL_LM",
    )

    args = SFTConfig(
        output_dir=OUTPUT_DIR,
        num_train_epochs=8,          # 小数据集，多训几轮让格式学扎实
        per_device_train_batch_size=4,
        learning_rate=1e-4,          # LoRA 可以用比全量微调更大的学习率
        logging_steps=5,
        save_strategy="epoch",
        bf16=True,
        report_to="tensorboard",     # 离线监控，见 17.5 验证
        max_length=512,
    )

    trainer = SFTTrainer(
        model=model,
        args=args,
        train_dataset=dataset,
        peft_config=peft_config,
        processing_class=tokenizer,
    )
    trainer.train()
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"✓ SFT 完成，LoRA 适配器保存在: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
```

### 17.5 验证

```bash
# 1. 开始训练（0.6B + LoRA + 30 条数据，单卡几分钟内跑完）
python rl/train_sft.py

# 2. 训练时/后，另开终端看 loss 曲线（第 11 章 11.3.3 的训练监控）
tensorboard --logdir ./models/router_sft
# 浏览器打开 http://localhost:6006，看 train/loss 是否逐步下降

# 3. 确认 LoRA 适配器文件已生成
ls ./models/router_sft/adapter_model.safetensors
```

预期：训练日志里 `loss` 从 ~2 逐步下降到 <0.5；TensorBoard 里 `train/loss` 曲线单调向下；第 3 条能看到 `adapter_model.safetensors`。**loss 不降**说明学习率或数据有问题（见第 11 章 11.3.3 排查表）。

---

## 17.6 SFT 评估（对比 base 模型）

训练完要验证效果（第 11 章 11.3.4）。我们写一个通用评估脚本，既能评 SFT 模型也能评后面的 GRPO 模型和 base 模型。

```python
# rl/evaluate.py
"""在验证集上评估路由准确率。对应第 11 章 11.3.4 / 11.5。"""
import argparse
import json

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

from rl.rewards import extract_label

BASE_MODEL = "Qwen/Qwen3-0.6B"


def load_model(model_path: str, use_lora: bool):
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="bfloat16",
                                                 device_map="auto")
    if use_lora:
        model = PeftModel.from_pretrained(model, model_path)
    model.eval()
    return model, tokenizer


def evaluate(model_path: str, use_lora: bool, val_file="rl/data/val.jsonl"):
    model, tokenizer = load_model(model_path, use_lora)
    samples = [json.loads(l) for l in open(val_file, encoding="utf-8")]

    correct = 0
    for s in samples:
        text = tokenizer.apply_chat_template(s["prompt"], tokenize=False,
                                             add_generation_prompt=True)
        inputs = tokenizer(text, return_tensors="pt").to(model.device)
        with torch.no_grad():
            out = model.generate(**inputs, max_new_tokens=64, do_sample=False)
        gen = tokenizer.decode(out[0][inputs["input_ids"].shape[1]:],
                               skip_special_tokens=True)
        pred = extract_label(gen)
        ok = pred == s["ground_truth"]
        correct += ok
        print(f"[{'✓' if ok else '✗'}] 预测={pred:<16} 真实={s['ground_truth']}")

    acc = correct / len(samples)
    print(f"\n准确率: {acc:.1%} ({correct}/{len(samples)})")
    return acc


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--model_path", required=True)
    p.add_argument("--use_lora", action="store_true")
    args = p.parse_args()
    evaluate(args.model_path, args.use_lora)
```

### 17.6 验证

```bash
# 1. 评估 base 模型（未训练，通常格式混乱、准确率低）
python -m rl.evaluate --model_path Qwen/Qwen3-0.6B

# 2. 评估 SFT 模型（应显著优于 base）
python -m rl.evaluate --model_path ./models/router_sft --use_lora
```

预期：SFT 模型的准确率明显高于 base（base 可能 <40%，SFT 后通常能到 60–100%，因为任务简单且已过拟合小数据）。关键是**每条都能解析出合法标签**（说明格式学会了），这是 GRPO 的前提。

---

## 17.7 GRPO 训练（强化学习，接入奖励函数）

现在进入核心（第 11 章 11.4）。SFT 让模型「会输出格式」，GRPO 通过试错让它「路由得更准」。GRPO 相比 PPO 不需要 Value Model，用组内相对奖励（第 11 章 11.4.1），非常适合小显存。

关键约束（第 11 章 11.1.5 提到过）：`per_device_train_batch_size` 必须能被 `num_generations` 整除。

```python
# rl/train_grpo.py
"""GRPO 训练：用奖励函数优化路由准确率。对应第 11 章 11.4。
从 SFT 模型继续训练（GRPO 需要一个会输出格式的初始策略）。"""
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer
from trl import GRPOConfig, GRPOTrainer

from rl.rewards import accuracy_reward, format_reward

BASE_MODEL = "Qwen/Qwen3-0.6B"
SFT_ADAPTER = "./models/router_sft"      # 从 SFT 学到的格式出发
OUTPUT_DIR = "./models/router_grpo"


def main():
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)
    # 先加载 base，再把 SFT 的 LoRA 合并进去作为 GRPO 的起点
    from peft import PeftModel
    base = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="bfloat16")
    model = PeftModel.from_pretrained(base, SFT_ADAPTER).merge_and_unload()

    # RL 格式数据集：含 prompt 和 ground_truth
    dataset = load_dataset("json", data_files="rl/data/rl_train.jsonl", split="train")

    peft_config = LoraConfig(
        r=16, lora_alpha=32,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
        task_type="CAUSAL_LM",
    )

    args = GRPOConfig(
        output_dir=OUTPUT_DIR,
        num_train_epochs=5,
        per_device_train_batch_size=8,   # 必须能被 num_generations 整除
        num_generations=8,               # 每个请求生成 8 个答案算组内相对奖励
        max_completion_length=128,
        learning_rate=1e-5,              # GRPO 学习率比 SFT 小（第 11 章 11.4.2）
        beta=0.05,                       # KL 惩罚系数 kl_coef（第 11 章 11.4.2）
        temperature=0.9,                 # 保持探索性
        logging_steps=2,
        bf16=True,
        report_to="tensorboard",
    )

    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[accuracy_reward, format_reward],   # 组合奖励，分数相加
        args=args,
        train_dataset=dataset,
        peft_config=peft_config,
    )
    trainer.train()
    trainer.save_model(OUTPUT_DIR)
    tokenizer.save_pretrained(OUTPUT_DIR)
    print(f"✓ GRPO 完成，模型保存在: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
```

### 17.7 验证

```bash
# 1. 开始 GRPO 训练
python rl/train_grpo.py

# 2. 看 reward 是否上升、KL 是否在合理范围（第 11 章 11.4.3 训练监控）
tensorboard --logdir ./models/router_grpo
# 关注 train/reward（应逐步上升）和 train/kl（应保持在 0.01–0.1，不爆炸）
```

预期：TensorBoard 里 `train/reward` 整体趋势上升（趋近 1.2 = 准确率 1.0 + 格式 0.2）；`train/kl` 保持在合理区间不爆炸。若 reward 不升 / KL 爆炸，按第 11 章 11.4.3 结尾的排查表调 `learning_rate` 和 `beta`。

---

## 17.8 合并 LoRA 并导出可部署模型

第 11 章 11.6.4：为了方便部署，把 LoRA 权重合并回基础模型，得到一个独立的完整模型。

```python
# rl/merge_lora.py
"""把 GRPO 训练的 LoRA 合并进基础模型，导出可直接部署的完整模型。
对应第 11 章 11.6.4(1)。"""
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

BASE_MODEL = "Qwen/Qwen3-0.6B"
ADAPTER = "./models/router_grpo"
MERGED = "./models/router_merged"


def main():
    base = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="bfloat16")
    model = PeftModel.from_pretrained(base, ADAPTER)
    merged = model.merge_and_unload()
    merged.save_pretrained(MERGED)
    AutoTokenizer.from_pretrained(BASE_MODEL).save_pretrained(MERGED)
    print(f"✓ 已导出合并模型到: {MERGED}")


if __name__ == "__main__":
    main()
```

### 17.8 验证

```bash
python rl/merge_lora.py

# 确认导出的是完整模型（有 model.safetensors 和 config.json，不再是 adapter）
ls ./models/router_merged/

# 直接用合并模型评估（注意：不再加 --use_lora，因为已经是完整模型）
python -m rl.evaluate --model_path ./models/router_merged
# 评估脚本里 use_lora=False 时会加载 BASE_MODEL；要评合并模型需临时把
# load_model 的 BASE_MODEL 改成 MERGED 路径，或直接进入 17.9 用服务化方式验证
```

预期：`router_merged/` 下有 `model.safetensors`、`config.json`、`tokenizer.json` 等完整模型文件。

> **提示**：`rl/evaluate.py` 里 base 路径写死为 `Qwen/Qwen3-0.6B`，评估合并模型时最省事的方式是下一节起服务后用 API 验证，避免改评估脚本。

### 把模型拉回本地（服务器路线）

训练到此结束。如果你是在租用的 GPU 服务器上跑的，现在把合并后的完整模型下载回本地，之后的 17.9/17.10（推理 + 接回主框架）都在本地做，**服务器就可以释放、停止计费了**：

```bash
# 在本地执行，把合并后的完整模型下载回来（约 1.2GB）
scp -P <端口> -r <用户名>@<服务器IP>:~/polycoder-rl/models/router_merged ./models/
```

> 若想在释放实例前先确认服务化没问题，也可以留着服务器跑完下一节 17.9 的 vLLM 验证再释放——二选一即可。

---

## 17.9 部署为 OpenAI-compat 端点

这一步是把训练成果接回 PolyCoder 的桥梁。回顾第 0 章、第 3 章：PolyCoder 的 OpenAI Provider 只认一个 `base_url`。所以只要让本地模型暴露成 OpenAI 兼容的 `/v1/chat/completions` 接口，就能无缝接入。

**推理只跑 0.6B 模型，本地就够，不必为此长期租着 GPU。** 两种起服务方式：

- **方式 A：vLLM**（吞吐高、原生 OpenAI-compat，需要 CUDA）——适合你还留着 GPU 服务器、想在释放前一并验证的情况。
- **方式 B：Ollama**（本地 CPU / Mac 统一内存即可跑）——把模型拉回本地后的推荐方式，转 GGUF 导入的完整步骤见章末 17.12 改动 4。

```bash
# 方式 A：vLLM（在带 CUDA 的机器上，如尚未释放的服务器）
pip install vllm
python -m vllm.entrypoints.openai.api_server \
    --model ./models/router_merged \
    --served-model-name polycoder-router \
    --port 8010

# 方式 B：Ollama（本地推理推荐；转 GGUF + ollama create 的完整命令见 17.12 改动 4）
```

### 17.9 验证

```bash
# 起服务后，用 curl 走 OpenAI 兼容接口测一条路由请求
curl http://localhost:8010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "polycoder-router",
    "messages": [
      {"role": "system", "content": "你是 PolyCoder 的请求路由器。可选标签：code_writer、code_reviewer、debugger、test_writer、knowledge_agent。先说理由，最后一行必须是 `Final Answer: <标签>`。"},
      {"role": "user", "content": "报错 KeyError，帮我看看哪里错了"}
    ],
    "max_tokens": 64
  }'
```

预期：返回的 JSON 里 `choices[0].message.content` 以 `Final Answer: debugger` 结尾。能通过 HTTP 拿到正确路由，就说明训练成果已经「服务化」，可以接回主框架了。

---

## 17.10 接入 PolyCoder：新建 RouterAgent 子 Agent

最后一步，把本地路由模型包成一个子 Agent。**关键在于：它只是换了个 base_url 的 OpenAI Provider**——完全复用第 3 章的抽象层，不动主框架任何代码。

### 给 `.env` 增加路由模型配置

```dotenv
# ── 本地路由模型（第 17 章 RL 训练产物）──────────────────
ROUTER_BASE_URL=http://localhost:8010/v1
ROUTER_MODEL=polycoder-router
```

在 `core/config.py` 的 `Settings` 里加两个字段：

```python
    router_base_url: str = "http://localhost:8010/v1"
    router_model: str = "polycoder-router"
```

### 子 Agent 实现 `sub_agents/router_agent.py`

```python
# sub_agents/router_agent.py
"""用本地 RL 训练的模型做请求路由。复用第 3 章 OpenAI Provider，
只是 base_url 指向本地 vLLM 服务。不改动主框架。"""
import re

from core.config import settings
from providers.openai import OpenAIProvider   # 第 3 章已有
from providers.types import Message, TextBlock

LABELS = {"code_writer", "code_reviewer", "debugger", "test_writer", "knowledge_agent"}
SYSTEM_PROMPT = (
    "你是 PolyCoder 的请求路由器。可选标签：code_writer、code_reviewer、"
    "debugger、test_writer、knowledge_agent。先说理由，"
    "最后一行必须是 `Final Answer: <标签>`。"
)


class RouterAgent:
    def __init__(self):
        # 复用第 3 章的 OpenAIProvider，只是换 base_url / model
        self.provider = OpenAIProvider(
            api_key="not-needed",             # 本地服务无需真实 key
            base_url=settings.router_base_url,
            model=settings.router_model,
        )

    async def route(self, user_request: str) -> str:
        """返回路由标签；解析失败时回退到 code_writer。"""
        messages = [
            Message(role="system", content=[TextBlock(text=SYSTEM_PROMPT)]),
            Message(role="user", content=[TextBlock(text=user_request)]),
        ]
        resp = await self.provider.complete(messages, max_tokens=64)
        text = resp.text if hasattr(resp, "text") else str(resp)
        m = re.search(r"Final Answer:\s*([a-z_]+)", text)
        if m and m.group(1) in LABELS:
            return m.group(1)
        found = [w for w in re.findall(r"[a-z_]+", text) if w in LABELS]
        return found[-1] if found else "code_writer"
```

> **注意**：上面 `OpenAIProvider` 的构造参数（`api_key/base_url/model`）和 `complete()` 的返回结构，请对齐你在第 3 章的实际实现签名——如果字段名不同，改这里的调用即可，逻辑不变。

### 17.10 验证

新建 `rl/test_router_integration.py`：

```python
# rl/test_router_integration.py —— 需要 17.9 的 vLLM 服务在跑
import asyncio
from sub_agents.router_agent import RouterAgent


async def main():
    router = RouterAgent()
    cases = [
        ("帮我实现一个快速排序", "code_writer"),
        ("审查一下这段代码", "code_reviewer"),
        ("抛了 TypeError 帮我排查", "debugger"),
        ("给这个函数加测试", "test_writer"),
        ("项目日志规范是什么", "knowledge_agent"),
    ]
    ok = 0
    for req, expect in cases:
        got = await router.route(req)
        hit = got == expect
        ok += hit
        print(f"[{'✓' if hit else '✗'}] {req!r:<30} -> {got} (期望 {expect})")
    print(f"\n端到端路由准确率: {ok}/{len(cases)}")


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
# 确保 17.9 的 vLLM 服务在跑，然后：
python -m rl.test_router_integration
```

预期：多数用例 `✓`，路由准确率接近验证集水平。至此，一个用 SFT+GRPO 训练出来的本地模型，已经作为子 Agent 真正跑在 PolyCoder 里了。

> **接入 Coordinator（可选下一步）**：在第 6 章的 `coordinator/planner.py` 里，可以先调用 `RouterAgent.route()` 拿到标签，再决定分发给哪个专家子 Agent——把「LLM 靠 prompt 猜路由」换成「专门训练过的小模型判路由」，更快、更便宜、更可控。

---

## 17.11 本章检查清单

```
□ 理解架构错配：不能训 Claude，只能训本地小模型当子 Agent（17.1）
□ GPU 可用，TRL/transformers/peft 均可导入（17.2 验证三条全过）
□ 运行 build_dataset.py，生成 sft/rl/val 三个 JSONL（17.3）
□ pytest rl/test_rewards.py -v 全部通过（奖励逻辑正确，17.4）
□ 运行 train_sft.py，TensorBoard 里 loss 下降，生成 adapter（17.5）
□ SFT 模型评估准确率明显高于 base，且都能解析出合法标签（17.6）
□ 运行 train_grpo.py，reward 上升、KL 不爆炸（17.7）
□ merge_lora.py 导出完整合并模型（17.8）
□ vLLM 起服务，curl 能拿到 `Final Answer: <标签>`（17.9）
□ RouterAgent 端到端路由测试多数通过（17.10）
```

> **学习路径提醒**（呼应第 11 章章末）：本章只训了「分类型」任务，奖励是稀疏的准确率。真正的 Agentic RL 进阶方向是**多步工具调用**的奖励设计（每步给中间奖励）——那需要把整条 agent 轨迹作为训练样本，复杂度高很多。建议先把本章这条"单步、可自动验证"的链路彻底跑通、理解每个超参数的作用，再去啃多步轨迹的 RL。

---

## 17.12 Apple Silicon（M 系列芯片）适配指南

> 本章主线是「租 GPU 服务器训练、模型拉回本地推理」（见 17.2）。本节针对两种情况：
> 1. **坚持在 Mac 上本地训练**（不想租服务器）——17.2–17.10 的代码按 CUDA 写，Mac 走 MPS 后端有几处硬冲突必须改，下面的改动 1–3、5 集中列出。
> 2. **走主线、把模型拉回 Mac 做本地推理**——这时只需要下面的**改动 4**（用 Ollama 起服务），训练部分的改动可忽略。
>
> **好消息**：Qwen3-0.6B 很小，M 系列的统一内存（模型权重和"显存"共用同一块内存）足够跑 SFT 甚至 GRPO。**真正的拦路虎只有一个——vLLM 在 Apple Silicon 上跑不起来，必须换 Ollama。**

### 各步骤在 M4 上的可行性

| 步骤 | 能跑吗 | 说明 |
|------|--------|------|
| 17.3 数据集 / 17.4 奖励单测 | ✅ 完全能 | 纯 CPU，与芯片无关 |
| 17.5 SFT 训练 | ✅ 能（比 CUDA 慢） | 0.6B 小模型走 MPS 可行 |
| 17.6 评估 / 17.8 合并 | ✅ 能 | |
| 17.7 GRPO 训练 | ⚠️ 能但吃力 | 要生成多个答案，比 SFT 重；需调小参数 |
| 17.9 vLLM 部署 | ❌ 不行 | vLLM 基本只支持 CUDA → **改用 Ollama** |

### 改动 1：安装 MPS 版 torch（替代 17.2 的 CUDA 轮子）

```bash
# ❌ 不要用 17.2 里的 CUDA 命令（--index-url .../cu121）
# ✅ M 系列直接装默认 torch，自带 MPS 支持
pip install torch
pip install -r requirements-rl.txt   # vLLM 那行可跳过，M 芯片装不了

# 验证 MPS 可用（必须输出 True）
python -c "import torch; print('MPS available:', torch.backends.mps.is_available())"
```

> **注意**：`requirements-rl.txt` 里不需要也装不上 vLLM；17.9 会用 Ollama 替代。

### 改动 2：训练脚本改 fp16、手动搬到 mps（改 17.5 / 17.7）

MPS 对 `bfloat16` 支持不稳定，且 `device_map="auto"`（依赖 accelerate 的 CUDA 分配逻辑）在 MPS 上行为不可靠。三处统一改：

**在 `rl/train_sft.py` 里：**

```python
# 加载模型：bfloat16 → float16
model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="float16")
model = model.to("mps")          # 手动搬到 MPS，别用 device_map

# SFTConfig 里：bf16=True → fp16=True
args = SFTConfig(
    ...
    fp16=True,                   # 原来是 bf16=True
    ...
)
```

**在 `rl/train_grpo.py` 里，除了上面同样的 fp16/mps 改动，再把 GRPO 的重负载参数调小：**

```python
base = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="float16")
model = PeftModel.from_pretrained(base, SFT_ADAPTER).merge_and_unload().to("mps")

args = GRPOConfig(
    ...
    per_device_train_batch_size=4,   # 8 → 4
    num_generations=4,               # 8 → 4（必须能整除 batch_size）
    max_completion_length=128,
    fp16=True,                       # 原来是 bf16=True
    ...
)
```

> 若 fp16 训练出现 NaN loss，退一步：去掉 `fp16=True`（用 fp32），M 芯片内存够，只是慢一点，最稳。

### 改动 3：评估脚本改 mps（改 17.6）

`rl/evaluate.py` 的 `load_model` 里：

```python
model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, torch_dtype="float16")
model = model.to("mps")          # 替换掉 device_map="auto"
```

`inputs = tokenizer(...).to(model.device)` 这行不用改——`model.device` 会自动是 `mps`。

### 改动 4：用 Ollama 替代 vLLM 部署（替代 17.9）

这是 M 芯片和 CUDA 路线最大的分叉。Ollama 是 Apple Silicon 上最顺的本地 OpenAI-compat 服务，自带 `http://localhost:11434/v1` 兼容接口——正好对上第 3 章的 OpenAI Provider。

需要先把 17.8 合并出的 HuggingFace 模型转成 **GGUF** 格式再导入 Ollama：

```bash
# 1. 安装 Ollama（若未装）
brew install ollama
ollama serve &                    # 后台起服务

# 2. 把合并模型转成 GGUF（用 llama.cpp 的转换脚本）
pip install llama-cpp-python      # 或 git clone llama.cpp 自行编译
git clone https://github.com/ggerganov/llama.cpp
python llama.cpp/convert_hf_to_gguf.py ./models/router_merged \
    --outfile ./models/router.gguf --outtype f16

# 3. 写 Modelfile 导入 Ollama
cat > Modelfile << 'EOF'
FROM ./models/router.gguf
EOF
ollama create polycoder-router -f Modelfile
```

### 17.12 验证

```bash
# 1. MPS 可用
python -c "import torch; print(torch.backends.mps.is_available())"   # True

# 2. 改完后跑 SFT，确认能在 MPS 上训练（活动监视器能看到 GPU 占用）
python rl/train_sft.py

# 3. Ollama 服务化后，走 OpenAI 兼容接口测一条路由（注意端口 11434）
curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "polycoder-router",
    "messages": [
      {"role": "system", "content": "你是 PolyCoder 的请求路由器。可选标签：code_writer、code_reviewer、debugger、test_writer、knowledge_agent。先说理由，最后一行必须是 `Final Answer: <标签>`。"},
      {"role": "user", "content": "报错 KeyError，帮我看看哪里错了"}
    ],
    "max_tokens": 64
  }'
```

预期：第 1 条输出 `True`；第 2 条训练日志 loss 正常下降（比 CUDA 慢，属正常）；第 3 条返回内容以 `Final Answer: debugger` 结尾。

### 改动 5：RouterAgent 指向 Ollama 端口（改 17.10）

`.env` 里把路由服务地址改成 Ollama 的端口：

```dotenv
# M 芯片用 Ollama，端口是 11434（vLLM 才是 8010）
ROUTER_BASE_URL=http://localhost:11434/v1
ROUTER_MODEL=polycoder-router
```

其余 `sub_agents/router_agent.py` 和 17.10 的端到端测试**完全不用改**——这正是第 3 章 OpenAI Provider 抽象层的价值：换后端只改一行 `base_url`。

---

# 第 18 章：2026 行业趋势对标增量改进（附录）

> 本章是一份**非侵入式**的改进记录。第 1-14 章是已经落地实现、无需回头翻改的主体；这里
> 收录的是针对第 6 章（Coordinator）、第 11 章（可观测性）两处的增量改进方案，以及一处
> 明确的空白说明。原则是：**不改动前面章节的正文与编号**，需要读者关注的地方只在原章节留
> 一行引用批注（见 6.10、11.7 节末尾的"进阶备注"），完整方案统一放在本章，每一节标题直接
> 点名它对应第几章。
>
> 这些改进来自 2026-07-17 的一次行业趋势对标评估：2026 年多智能体工程的共识已从"越多
> Agent 越好"转向**按需分解、稀疏通信、可验证评估**。第 15 章 15.14 节的 CONCAT 动态分组、
> 第 16 章 16.13 节的 MAST 失败分类、16.14 节的评测技能化，与本章的 DAG 重规划容错，都是
> 这条主线下的具体落点。**所有方案均训练无关**，与第 17 章"RL 只能训本地小模型、不能训
> Claude/GPT"的硬约束完全兼容。

## 18.1 Coordinator：DAG 执行失败的局部重新规划（对应第 6 章）

### 现状与问题

第 6 章的 Coordinator 是一个**一次性规划**的编排器：`coordinator/planner.py` 的 `make_plan()`
调一次 LLM 把用户请求拆成带 `depends_on` 的任务 DAG，`coordinator/dispatcher.py` 的
`dispatch()` 随后按拓扑波次执行到底。这套设计干净，但有一个没有覆盖的失败面：

- **规划本身可能是错的**。Planner 可能漏写依赖（本该 `depends_on:["t1"]` 却写成 `[]`）、
  把粒度切得不对（该拆两步的合成一步），或选错子 Agent。
- 现状下 `dispatch()` 只有一种失败处理：某个任务抛异常或其前置失败时，把它记进 `errors` 并
  跳过下游（见 6.7 节 `dispatch()` 里 `errors[tid] = f"前置任务 '{failed_dep}' 失败，跳过本
  任务"` 的分支）。**它不会尝试纠正规划**——错误的 DAG 会一路执行到所有波次跑完，用户只能
  拿到一个残缺结果，再手动重发请求。

这正是 2026 行业趋势评估里"orchestrator-worker 拓扑的已知风险：supervisor 一次规划失误无
补救"的具体体现。

### 方案：波次结束后的局部重新规划检查点

思路是给 `dispatch()` 的每个波次结束处加一个**可选的检查点**：如果这一波里出现了"非预期
的失败"（不是业务上正常的 `NEEDS_FIX`，而是任务被跳过 / 子 Agent 明确报"上游没给我该有的
上下文"这类规划性错误），就**只针对受影响的子图**重新调一次 `make_plan()`，生成一个补丁
DAG 合并进后续波次——而不是推倒整个请求重来。

给 `coordinator/dispatcher.py` 的 `dispatch()` 增加一个可选参数与波次后钩子：

```python
# coordinator/dispatcher.py（在第 6 章版本上增量修改）

async def dispatch(
    tasks: list[TaskSpec],
    session_id: str | None = None,
    on_task_done: Callable[[TaskSpec, str], None] | None = None,
    allow_replan: bool = False,        # ← 新增：是否允许局部重新规划（默认关，不影响老行为）
    max_replans: int = 1,             # ← 新增：整个请求内最多触发几次，兜底防止无限重规划
    user_request: str | None = None,  # ← 新增：重新规划时要带上原始请求作上下文
) -> tuple[dict[str, str], Usage]:
    ...
    replans_used = 0
    while ready:
        wave = list(ready)
        ready.clear()
        # ...（原有的 runnable/errors 分类与 asyncio.gather 执行逻辑，完全不变）...

        # ── 新增：波次结束检查点 ───────────────────────────────────────────────
        if allow_replan and replans_used < max_replans:
            bad = _detect_planning_failures(wave, spec_by_id, results, errors)
            if bad:
                patch = await _replan_subgraph(
                    bad, spec_by_id, results, errors,
                    user_request=user_request, session_id=session_id,
                )
                if patch:
                    replans_used += 1
                    _merge_patch(patch, spec_by_id, in_degree, dependents, ready, errors)
                    log.warning("dispatch_replanned",
                                failed=[t.id for t in bad], patch_size=len(patch))

        # 更新依赖计数、解锁下一波次（原有逻辑不变）
        for tid in wave:
            ...
    return results, _sum_usage(usages)
```

其中三个辅助函数保持"能自动判的判、判不了的放过"的克制风格：

```python
def _detect_planning_failures(wave, spec_by_id, results, errors) -> list[TaskSpec]:
    """
    只挑出'疑似规划错误'的失败任务，不把所有失败都当成需要重规划。
    判据（保守，宁可漏判不误判）：
      - 任务进了 errors，且错误信息里带"前置任务 ... 失败"（说明依赖链断了）
      - 或子 Agent 结果里出现约定的规划性失败标记（如 "[MISSING_CONTEXT]"）
    业务性失败（如 code_reviewer 判定 NEEDS_FIX）不算，那是正常产出，交给下游正常处理。
    """
    bad = []
    for tid in wave:
        err = errors.get(tid, "")
        if "前置任务" in err or "MISSING_CONTEXT" in results.get(tid, ""):
            bad.append(spec_by_id[tid])
    return bad


async def _replan_subgraph(bad, spec_by_id, results, errors, user_request, session_id):
    """
    只把'失败任务 + 其未执行的下游'的描述重新喂给 make_plan()，
    并带上已成功任务的结果作为上下文，让 LLM 生成一个补丁 DAG。
    注意：不重规划整个请求，避免已经跑完的任务白跑一遍、也避免 token 浪费。
    """
    from .planner import make_plan
    done_context = {tid: results[tid] for tid in results}   # 已完成任务结果作上下文
    hint = (
        f"原始请求：{user_request}\n"
        f"以下子任务因规划问题失败，请只为这些任务及其后续重新给出修正后的任务计划"
        f"（可补依赖、拆分或改派子 Agent）：{[t.id for t in bad]}\n"
        f"已完成并可复用的结果：{list(done_context.keys())}"
    )
    patch_tasks, _reply, _usage = await make_plan(hint, session_id=session_id)
    return patch_tasks


def _merge_patch(patch, spec_by_id, in_degree, dependents, ready, errors):
    """把补丁 DAG 的新任务并入执行状态：登记 spec、重算入度、把无依赖的补丁任务塞进 ready。"""
    for t in patch:
        if t.id in spec_by_id:      # 补丁任务 id 与老任务撞了就改名，避免覆盖
            t.id = f"{t.id}_replan"
        spec_by_id[t.id] = t
        errors.pop(t.id, None)
        in_degree[t.id] = len(t.depends_on)
        for dep in t.depends_on:
            dependents[dep].append(t.id)
        if in_degree[t.id] == 0:
            ready.append(t.id)
```

### 边界：这是"人工兜底"，不是自动拓扑选择器

要克制地界定这个功能的范围。它**只做**"检测到规划性失败 → 局部补一次规划"，**不做**行业
评估里提到的"上来先自动判断这个请求该不该拆、该用哪种拓扑"。原因是：

- "任务能否分解、该用几个 Agent"目前**没有可靠的自动判据**，硬做一个启发式判断器很容易变成
  拍脑袋的魔法阈值，属于过度设计——这与本项目一贯的"先跑通、有明确收益再升级"风格相悖
  （对照 15.3.4 节"为什么现在才从 TF-IDF 升级到向量检索"的论证方式）。
- `max_replans` 默认 1，是刻意的保守设定：重新规划本身也可能再次出错，必须有硬上限兜底，
  不能让 Coordinator 陷入"规划→失败→再规划"的循环烧 token。

### 验证

构造一个 Planner **故意漏写依赖**的场景，观察局部重新规划触发并补全：

```bash
# 1. 临时在 planner 的系统提示词里注入一个会漏 depends_on 的诱导，或直接构造一个
#    "写函数并按检索到的规范修正"但故意不声明依赖的请求：
curl -X POST http://localhost:8002/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "按内部规范写一个分页函数，然后立刻用刚写的规范审查它"}'

# 2. 期望日志里能看到：
#    - 第一波 code_reviewer 因为拿不到前置规范，结果里带 [MISSING_CONTEXT]
#    - dispatch_replanned 事件触发（failed=[...], patch_size=N）
#    - 补丁任务补上了 knowledge_agent→code_reviewer 的依赖，第二轮正常产出
```

对应地，在第 6 章的 "6.11 本章检查清单" 之外，读者可自行补一条验收项：
`□ allow_replan=True 时，模拟 DAG 规划错误能触发局部重新规划而非整体失败`。

---

## 18.2 可观测性：按失败类别聚合查询（对应第 11 章）

> 本节依赖第 16 章 16.13 节新增的 `failure_category` 字段。请先读 16.13 再回到这里。

第 11 章的三支柱（Metrics / Traces / Logs）在"生产级可观测性基础设施"上已经很完整，
`SessionMetrics`（16 章）也提供了足够细的埋点。缺的不是数据，而是**数据之上的一层归类**——
把每次失败归到 16.13 定义的三类（`spec_flaw` / `misalignment` / `verification_failure`）之
一，然后跨会话统计"哪类失败在系统性发生"。

好消息是：这不需要引入任何新工具。16.13 让失败日志带上了 `failure_category` 字段，而第 11 章
的日志本来就是 `structlog` 输出的 JSON、进了 Loki，`| json` 之后即可按该字段过滤聚合。

在 Grafana 的 Loki 数据源里（沿用 11.7.3 的 LogQL 写法）：

```logql
# 看所有被归类为"规范/规划缺陷"的失败（如 Planner 漏依赖导致的跳过）
{container="agent"} | json | failure_category="spec_flaw"

# 按失败类别统计最近 1 小时的发生速率——一眼看出哪类失败最猖獗
sum by (failure_category) (
  count_over_time({container="agent"} | json | failure_category=~".+" [1h])
)

# 结合 session_id，定位某次会话到底栽在哪一类失败上
{container="agent"} | json | session_id="user_001" | failure_category=~".+"
```

把第二条查询做成 Grafana 的一个 `sum by (failure_category)` 时间序列面板，就得到了一张
"失败类别趋势图"：如果 `verification_failure` 常年偏高，说明 RAG 环节采纳了太多低相关度结果
（该调 15.11 节的 rrf_score 阈值或 15.14 节的裁剪策略）；如果 `spec_flaw` 高，说明 Planner
规划质量差（该开 18.1 的局部重规划）。**这是在既有基础设施上叠加的归类层，改造成本低**，
恰是行业评估里"数据充分、只缺归类维度"判断的落地。

---

## 18.3 已知空白：LLM 输出层安全护栏（未来方向，不在本次范围）

诚实地记录一处空白，而不是硬造一个方案来凑"看起来完整"。

PolyCoder 现有的安全防护都在**执行层**，且做得扎实：

- 路径穿越沙箱：`core/workspace.py` 的 `resolve_safe_path()`，所有文件操作限定在工作目录内；
- 子进程沙箱：`run_python` 工具带超时 + 危险模块黑名单；
- SQL 注入防御：MySQL 相关工具用参数化查询；
- 一份 `skills/security.md` 把上述红线沉淀为可加载的 Skill。

但项目**没有 LLM 输出层的护栏**，具体缺三样：

1. **Prompt injection 防御**——用户/检索文档里夹带的"忽略以上指令"类注入，目前没有检测；
2. **输出内容审查**——LLM 生成内容在返回/落盘前没有过滤；
3. **越权工具调用的运行时拦截**——子 Agent 调用超出其职责范围的工具时，没有一层运行时白名单
   校验（现在靠 system prompt 约定，属于"软约束"）。

**为什么本版不给完整设计**：2026 年的行业检索对这一维度本身也缺乏可靠的、可量化的最佳实践
来源（评估报告将其列为"开放问题，不做强行归因"）。在没有可信基准的情况下写一套护栏方案，
风险是把不成熟的做法固化进文档。这里只记录空白和两个**可能**的探索方向，供后续单独立项调研：

- **工具调用白名单校验**：在 `agent_core/executor.py` 执行工具前，按"当前子 Agent → 允许的
  工具集"做一次运行时校验，越权直接拒绝（把软约束变硬约束）。这是三项里最容易落地、最不依赖
  外部基准的一项。
- **system prompt 注入检测启发式**：对进入 LLM 的用户输入/检索片段做轻量启发式扫描（如检测
  "ignore previous instructions"类模式），命中则标记而非直接信任。

在有可信的评测基准之前，这一节保持"记录而不实现"的状态。

---
