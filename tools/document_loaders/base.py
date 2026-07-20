# tools/document_loaders/base.py
"""
DocumentChunk：从文档中提取的一个内容块。

无论原始文件是 .txt、.pdf 还是 .png，
最终都会被加载器转换成若干个 DocumentChunk。
知识库只和 DocumentChunk 打交道，不关心原始格式。
"""
from dataclasses import dataclass, field


@dataclass
class DocumentChunk:
    """一个可供检索的文本块。"""

    source: str
    """原始文件路径，如 'knowledge_base/manual.pdf'。"""

    title: str
    """文档标题，通常取文件名（去掉扩展名）。"""

    text: str
    """文本内容。
    - 纯文本文件：直接是文件内容
    - PDF：文字内容 + 图片描述（拼接在一起）
    - 图片文件：视觉 LLM 生成的描述文字
    embedding 就是基于这个字段编码成向量做检索的。"""

    image_count: int = 0
    """原始文档中包含的图片数量（纯文本为 0）。"""

    page_count: int = 0
    """文档页数（PDF 才有意义，其他为 0）。"""