from pathlib import Path
from tools.base import BaseTool


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
                "path": {"type": "string", "description": "目录路径，默认为当前目录", "default": "."},
                "depth": {"type": "integer", "description": "显示深度（1=仅当前层，默认 2）", "default": 2},
            },
        }
    async def execute(self, inputs: dict) -> str:
        base = Path(inputs.get("path", "."))
        depth = min(int(inputs.get("depth", 2)), 4)
        lines = [str(base)]
        for p in sorted(base.rglob("*")):
            rel = p.relative_to(base)
            if len(rel.parts) > depth: continue
            if any(part.startswith(".") or part == "__pycache__" for part in rel.parts): continue
            indent = "  " * (len(rel.parts) - 1)
            lines.append(f"{indent}{'📁 ' if p.is_dir() else '📄 '}{p.name}")
        return "\n".join(lines[:100])