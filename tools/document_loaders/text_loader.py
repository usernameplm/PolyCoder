# tools/document_loaders/text_loader.py
"""加载 .txt 和 .md 纯文本文件。"""

from pathlib import Path
from .base import DocumentChunk


class TextLoader:

    SUPPORTED_EXTENSIONS = {".txt", ".md"}

    def load(self, path: Path) -> list[DocumentChunk]:
        """
        加载单个文本文件。

        返回包含一个 DocumentChunk 的列表。
        如果文件无法读取，返回空列表（不抛异常，让知识库跳过这个文件）。
        """
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [TextLoader] 无法读取 {path}: {e}")
            return []

        if not content.strip():
            return []

        return [
            DocumentChunk(
                source=str(path),
                title=path.stem.replace("_", " ").replace("-", " "),
                text=content,
                image_count=0,
                page_count=0,
            )
        ]