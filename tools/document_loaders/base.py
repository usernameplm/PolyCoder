# tools/document_loaders/base.py
"""
DocumentChunk：从文档中提取的一个内容块。

无论原始文件是 .txt、.pdf 还是 .png，
最终都会被加载器转换成若干个 DocumentChunk。
知识库只和 DocumentChunk 打交道，不关心原始格式。

⚠️ 粒度约定（本章的关键改动）：一个 chunk 是"一个可独立检索、可独立作答的语义片段"，
   **不是一整篇文档**。旧版把整份 .md / 整个 PDF 塞进一个 chunk，粒度太粗会带来两个问题：
     1. 向量被长文本"稀释"——一段几千字的文档编码成一个向量，局部要点（某个错误码、
        某条命名约定）的语义被平均掉，query 命中率下降；
     2. 命中后返回的 content 是整篇，下游 Agent 还要自己在里面找那一句规范。
   所以本章由 `splitter.py` 按"标题 + 段落 + 控长"把文档切成多个 chunk（见 15.7.6），
   Loader 返回的是 `list[DocumentChunk]` 而不再是"一份文档一个块"。
"""
from dataclasses import dataclass


@dataclass
class DocumentChunk:
    """一个可供检索的文本块（语义片段，不是整篇文档）。"""

    source: str
    """原始文件路径，如 'knowledge_base/manual.pdf'。"""

    title: str
    """文档标题，通常取文件名（去掉扩展名）。"""

    text: str
    """chunk 的正文（原文片段）。
    - 纯文本文件：按标题/段落切出来的一段
    - PDF：某一页或某一段的文字，或某张图片的描述
    - 图片文件：视觉 LLM 生成的描述文字
    这是"呈现给下游的原文"，检索命中后原样返回，不做改写。"""

    # ── 检索/定位用的结构化字段（切分后才有意义）──────────────────────────────
    searchable_text: str = ""
    """真正拿去编码/BM25 的文本 = "[标题路径] title_path\\n" + text。
    把所属标题层级拼进检索文本，能让"这段属于哪一节"的上下文参与召回
    （对齐 DDH-main-agent build_chunks 的 searchable_text 设计）。
    为空时检索层会回退用 text。"""

    section_title: str = ""
    """本 chunk 所属的小节标题（最近的一个 Markdown 标题 / PDF 段落标题）。"""

    title_path: str = ""
    """标题层级路径，如 "错误码规范 > 4xx 客户端错误"，供来源归因和定位。"""

    chunk_index: int = 0
    """该 chunk 在所属文档内的序号（从 0 开始），便于回溯上下文顺序。"""

    # ── chunk 类型与祖先标题（对齐 DDH build_chunks / _add_chunk）─────────────
    chunk_type: str = "text"
    """chunk 的内容类型：'text' / 'table' / 'image'。
    下游据此区分处理——例如 image 块要用 image_path 回附图片，table 块要提示"来自表格"。"""

    page_title: str = ""
    """本页的"祖先大标题"（如 PDF 里的 "X8R / Automotive MLCC"）。
    与 section_title 的区别：section_title 是最近的一个小节标题，翻到下一个小标题就被覆盖；
    page_title 是页面级上下文，同一页内不被后续小节标题覆盖，翻页才重置（见 15.7.6 说明）。"""

    ancestor_title: str = ""
    """拼进 title_path 的祖先标题（page_title 与 section_title 不同名时才有值）。
    单独留字段是为了让下游能只取"页面级上下文"而不含小节名。"""

    # ── 表格分块元数据（一张大表被按行切成多块时才有意义，对齐 _split_table_html）──
    table_id: str = ""
    """所属表格的唯一 id。同一张大表切出来的多个块共享同一个 table_id，便于回溯拼回整表。"""

    table_part_idx: int = 0
    """本块是该表格的第几片（从 0 开始）。整表未超长时恒为 0。"""

    table_part_count: int = 1
    """该表格一共被切成几片。未超长时为 1。"""

    is_table_part: bool = False
    """该表格是否被切成了多片（table_part_count > 1 时为 True）。"""

    image_count: int = 0
    """本 chunk 关联的图片数量（纯文本为 0）。"""

    image_path: str = ""
    """图片相对路径（如 'imgs/manual_img_3.jpg'）。仅 chunk_type=='image' 时有值，
    供最终回答用 ![](image_path) 回附原图并标注来源页码（对齐 HPD datasheet-rag SKILL 的强制格式）。"""

    page_num: int = 0
    """所在页码（PDF 才有意义，其他为 0）。"""

    page_count: int = 0
    """文档总页数（PDF 才有意义，其他为 0）。"""