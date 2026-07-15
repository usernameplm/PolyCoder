# sub_agents/code_writer.py
from tools.builtin.read_file import ReadFileTool
from tools.builtin.run_python import RunPythonTool
from tools.builtin.write_file import WriteFileTool
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
3. 用 write_file 将代码保存到文件
4. 用 run_python 执行生成的代码，验证无语法错误和运行时错误
5. 如果测试失败，自动修复并重新验证

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
        return [ReadFileTool(), RunPythonTool(), WriteFileTool()]