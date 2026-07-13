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