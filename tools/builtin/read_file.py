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

        # 文件大小限制：超过 100KB 只读取前 200 行
        size = target.stat().st_size
        try:
            text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return f"错误：{raw_path} 不是文本文件（可能是二进制文件）"

        lines = text.splitlines()
        if size > 100_000:
            preview = "\n".join(lines[:200])
            return (
                f"文件：{raw_path}（共 {len(lines)} 行，仅显示前 200 行）\n"
                f"{'─' * 40}\n{preview}\n{'─' * 40}\n"
                f"[文件过长，已截断。如需查看特定行，请使用 read_file_lines 工具]"
            )

        return f"文件：{raw_path}（{len(lines)} 行）\n{'─' * 40}\n{text}\n{'─' * 40}"