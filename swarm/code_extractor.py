# swarm/code_extractor.py
"""
从 Agent 的自由文本产出里抽出可写盘的 Python 代码。

DebuggerSwarmAgent / TestWriterSwarmAgent 的输出格式（7.4b / 7.4c 节的 system
prompt 里约定的）都是"说明文字 + ```python 代码块"混在一起的一整段文本，
apply 接口只关心代码块本身，说明文字要剥掉。
"""
import re

_PYTHON_FENCE = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)
_ANY_FENCE = re.compile(r"```[a-zA-Z]*\s*\n(.*?)```", re.DOTALL)


def extract_python_code(text: str) -> tuple[str | None, str]:
    """
    从文本里抽出代码块，返回 (代码, 来源标记)。

    优先级：
      1. 第一个 ```python ... ``` 围栏
      2. 兜底：任意 ``` ... ``` 围栏
      3. 都没有：返回 (None, "none")
    """
    m = _PYTHON_FENCE.search(text)
    if m:
        return m.group(1).strip(), "python_block"

    m = _ANY_FENCE.search(text)
    if m:
        return m.group(1).strip(), "fenced_block"

    return None, "none"
