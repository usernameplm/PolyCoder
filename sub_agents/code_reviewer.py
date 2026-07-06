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