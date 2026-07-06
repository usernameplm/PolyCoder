# tools/base.py
"""
工具（Tool）的抽象基类。

所有工具都继承 BaseTool，实现以下抽象属性和方法：
  - name：工具名称（LLM 用这个名字来"点名"调用工具）
  - description：工具描述（★ 非常重要！LLM 靠这段描述决定何时调用）
  - input_schema：参数定义（JSON Schema 格式，LLM 据此填写参数）
  - execute()：实际执行逻辑，返回字符串结果
"""
from abc import ABC, abstractmethod
from providers.types import ToolDefinition


class BaseTool(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        """
        工具名称。
        命名规范：小写字母+下划线，如 "calculator"、"query_weather"。
        LLM 通过这个名字来调用工具。
        """
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """
        工具描述。★ 这是最重要的字段！

        写作原则：
        1. 说清楚"什么时候用这个工具"（触发条件）
        2. 说清楚"能做什么、不能做什么"（能力边界）
        3. 如果有特殊格式要求，在这里说明

        好的描述：
          "计算数学表达式，返回数值结果。
           当用户需要进行加减乘除、乘方、取余等数学运算时使用。
           输入必须是有效的 Python 数学表达式（如 '2 + 3 * 4'）。"

        差的描述：
          "计算"  ← 太模糊，LLM 不知道什么时候用
        """
        ...

    @property
    @abstractmethod
    def input_schema(self) -> dict:
        """
        工具接受的参数定义，JSON Schema 格式。

        标准格式：
        {
          "type": "object",
          "properties": {
            "参数名": {
              "type": "参数类型",         # string / number / integer / boolean / array / object
              "description": "参数说明"   # LLM 填参数时参考的说明
            }
          },
          "required": ["必填参数列表"]
        }
        """
        ...

    @property
    def definition(self) -> ToolDefinition:
        """返回 ToolDefinition 对象（发给 LLM 时用）。不需要重写。"""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
        )

    @abstractmethod
    async def execute(self, inputs: dict) -> str:
        """
        工具执行逻辑。

        参数：
            inputs - LLM 填写的参数字典（和 input_schema 定义的字段对应）

        返回：
            字符串结果（LLM 会读这个字符串来了解工具执行的结果）

        注意：
            - 永远不要抛异常！有错误时返回描述错误的字符串。
            - 返回内容越精简越好（LLM 需要处理这些内容，Token 有限）。
            - 只返回 LLM 需要的信息，过滤掉无关字段。
        """
        ...