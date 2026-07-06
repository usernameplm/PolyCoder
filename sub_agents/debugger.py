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

验证结果：（run_python 执行结果截图或输出）

如果 Bug 无法复现，说明已尝试的场景和推测可能的原因。"""

    @property
    def tools(self):
        return [ReadFileTool(), RunPythonTool(), SearchCodeTool()]
