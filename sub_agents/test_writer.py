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