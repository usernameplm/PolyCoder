# tools/builtin/run_python.py
"""
在沙箱环境中执行 Python 代码片段，返回 stdout / stderr。

用途：
- 让 Agent 验证生成的代码是否能跑通
- 执行单元测试，看输出结果
- 快速验算某个逻辑

安全限制（注意：这是"轻量防护"，非真正的安全沙箱）：
- 超时 10 秒自动终止（防止死循环）
- 字符串黑名单挡掉最常见的 subprocess / os.system / __import__ / 写绝对路径
- 在子进程中运行，cwd 限定在工作目录，不影响主进程

⚠️ 黑名单是字符串层面的简单检查，挡不住 import socket 联网、写工作目录内文件等操作。
   若要暴露给不可信的外部输入（如公网飞书/HTTP），必须换成容器/gVisor 等真正的隔离方案。
"""
import asyncio
import sys
import textwrap
from tools.base import BaseTool
from core.workspace import get_workspace

# 危险模块黑名单（在代码字符串层面做简单检查）
_BLOCKED_IMPORTS = [
    "subprocess", "os.system", "shutil.rmtree",
    "open('/", 'open("/', "__import__",
]


class RunPythonTool(BaseTool):

    @property
    def name(self) -> str:
        return "run_python"

    @property
    def description(self) -> str:
        return (
            "执行一段 Python 代码并返回输出结果（stdout + stderr）。"
            "用于：验证生成的代码是否正确、运行单元测试、快速验证逻辑。"
            "超时限制 10 秒，在工作目录下的独立子进程中运行。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的 Python 代码（字符串，支持多行）",
                },
                "timeout": {
                    "type": "integer",
                    "description": "超时时间（秒），默认 10，最大 30",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 30,
                },
            },
            "required": ["code"],
        }

    async def execute(self, inputs: dict) -> str:
        code = inputs.get("code", "").strip()
        timeout = min(int(inputs.get("timeout", 10)), 30)

        if not code:
            return "错误：code 不能为空"

        # 简单安全检查
        for blocked in _BLOCKED_IMPORTS:
            if blocked in code:
                return f"错误：代码包含被禁止的操作（{blocked}），出于安全考虑无法执行"

        # 用 asyncio.create_subprocess_exec 在子进程运行，隔离环境
        # cwd 设为统一工作目录：代码里的相对路径都相对工作目录，和其他工具一致
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", textwrap.dedent(code),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(get_workspace()),
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            return f"错误：代码执行超时（>{timeout}s），进程已终止"

        output_parts = []
        if stdout:
            output_parts.append(f"[stdout]\n{stdout.decode('utf-8', errors='replace')}")
        if stderr:
            output_parts.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace')}")
        if proc.returncode != 0:
            output_parts.append(f"[退出码] {proc.returncode}")

        return "\n".join(output_parts) if output_parts else "[无输出，代码执行成功]"