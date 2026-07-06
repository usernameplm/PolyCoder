# swarm/sandbox.py
"""
apply 接口专用的工作目录沙箱：把调用方传来的相对路径解析成绝对路径，
拒绝任何跑出工作目录范围的写入（路径穿越，如 "../../../etc/passwd"）。
"""
import os
from pathlib import Path


class PathTraversalError(ValueError):
    """请求路径解析后跑出了工作目录范围。"""


def get_workspace() -> Path:
    return Path(os.environ.get("SWARM_WORKSPACE", ".")).resolve()


def resolve_safe_path(raw_path: str, workspace: Path | None = None) -> Path:
    """
    把相对路径解析为工作目录内的绝对路径，穿越则抛 PathTraversalError。
    """
    workspace = workspace or get_workspace()
    target = (workspace / raw_path).resolve()
    if not str(target).startswith(str(workspace)):
        raise PathTraversalError(f"路径 '{raw_path}' 解析后跑出了工作目录范围，拒绝写入")
    return target


def write_text_sandboxed(raw_path: str, content: str, workspace: Path | None = None) -> Path:
    """
    在沙箱校验通过后写盘，自动创建缺失的父目录。返回写入的绝对路径。
    """
    target = resolve_safe_path(raw_path, workspace)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target
