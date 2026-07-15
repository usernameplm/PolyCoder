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