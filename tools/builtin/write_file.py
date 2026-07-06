from pathlib import Path
from tools.base import BaseTool


class WriteFileTool(BaseTool):
    @property
    def name(self): return "write_file"
    @property
    def description(self): return "把生成的代码写入指定路径的文件。仅在用户明确要求保存时使用。"
    @property
    def input_schema(self):
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目标文件路径"},
                "content": {"type": "string", "description": "写入的文件内容"},
            },
            "required": ["path", "content"],
        }
    async def execute(self, inputs: dict) -> str:
        path = Path(inputs["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(inputs["content"], encoding="utf-8")
        return f"已写入 {path}（{len(inputs['content'])} 字符）"