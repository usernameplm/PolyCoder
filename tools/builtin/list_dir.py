from pathlib import Path
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