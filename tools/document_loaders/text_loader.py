# tools/document_loaders/text_loader.py
"""加载 .txt 和 .md 纯文本文件，并切成多个语义 chunk。"""

from pathlib import Path
from .base import DocumentChunk
from .splitter import split_markdown, split_plain


class TextLoader:

    SUPPORTED_EXTENSIONS = {".txt", ".md"}

    def load(self, path: Path) -> list[DocumentChunk]:
        """
        加载单个文本文件，切成**多个** DocumentChunk。

        .md 走标题感知切分（split_markdown），.txt 走纯段落控长切分（split_plain）。
        文件无法读取或为空时返回空列表（不抛异常，让知识库跳过这个文件）。
        """
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            print(f"  [TextLoader] 无法读取 {path}: {e}")
            return []

        if not content.strip():
            return []

        title = path.stem.replace("_", " ").replace("-", " ")
        pieces = (split_markdown(content) if path.suffix.lower() == ".md"
                  else split_plain(content))

        chunks: list[DocumentChunk] = []
        for i, p in enumerate(pieces):
            # searchable_text 把标题路径拼进检索文本，让"属于哪一节"参与召回
            title_path = p["title_path"]
            searchable = (f"[标题路径] {title_path}\n{p['text']}"
                          if title_path else p["text"])
            chunks.append(DocumentChunk(
                source=str(path),
                title=title,
                text=p["text"],
                searchable_text=searchable,
                section_title=p["section_title"],
                title_path=title_path,
                chunk_index=i,
            ))
        return chunks