# core/workspace.py
"""
全项目统一的工作目录（工作区）。

设计目标：所有工具（read_file / write_file / search_code / list_dir / run_python）
和所有子 Agent 的文件操作，都限定在同一个工作目录内，且禁止路径穿越到目录外。

工作目录的唯一来源是 core.config.settings.workspace（环境变量 WORKSPACE，默认当前目录）。
启动服务时用 WORKSPACE=/path uvicorn main:app 指定，一处设置，全局生效。
"""
from pathlib import Path

from core.config import settings


class PathTraversalError(ValueError):
    """请求路径解析后跑出了工作目录范围。"""


def get_workspace() -> Path:
    """返回工作目录的绝对路径（唯一来源：settings.workspace）。"""
    return Path(settings.workspace).resolve()


def resolve_safe_path(raw_path: str, workspace: Path | None = None) -> Path:
    """
    把相对路径解析为工作目录内的绝对路径；解析后若跑出工作目录，抛 PathTraversalError。

    workspace 不传时用全局工作目录；传入用于测试或临时覆盖。
    """
    workspace = (workspace or get_workspace()).resolve()
    target = (workspace / raw_path).resolve()
    if target != workspace and workspace not in target.parents:
        raise PathTraversalError(f"路径 '{raw_path}' 解析后跑出了工作目录范围，拒绝访问")
    return target


def write_text_sandboxed(raw_path: str, content: str, workspace: Path | None = None) -> Path:
    """在沙箱校验通过后写盘，自动创建缺失的父目录。返回写入的绝对路径。"""
    target = resolve_safe_path(raw_path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
