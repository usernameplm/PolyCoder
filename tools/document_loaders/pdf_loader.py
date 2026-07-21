# tools/document_loaders/pdf_loader.py
"""
PDF 文档加载器（基于 docling 版面分析）。

处理流程（每个 PDF 生成**多个** DocumentChunk，粒度到"页 / 段 / 表 / 图"）：
1. 用 docling 的 DocumentConverter 解析 PDF，得到结构化 block 序列
2. **按文档顺序**遍历 block，边走边维护"当前小节标题 section_title / 页面级祖先标题
   page_title"（对齐 DDH build_chunks 的做法——docling 给的是扁平 block 流，标题和正文
   平级出现，只有顺序遍历才能知道某段正文/某张表归属最近哪个标题）：
   - 文本 block：按 section 累积，遇到标题/表/图就先落盘，再走 splitter 控长切成多段
   - 标题 block（SectionHeaderItem）：更新 section_title，命中"大标题"特征则记为本页祖先；
     标题文本本身也单独成一个小块（利于"查章节名"类问题直接命中）
   - 表格 block（TableItem）：export_to_markdown() 导出 Markdown 表格；整表优先，超长则
     按行切、每片重复标题+表头（见 15.7.6 split_table_md），绝不断行
   - 图片 block（PictureItem）：取渲染图，vision.caption_image() 生成描述，并补"出处"
     （所属章节 + 文档主实体），单独成块并记 image_path 供回答回附原图
3. 每个 chunk 记住自己的 page_num 与 title_path，便于来源归因到"第 N 页 / 哪一节"

为什么用 docling 而不是 pymupdf（见 15.3.2）：
- pymupdf 的 get_text("text") 是纯文本流，表格会被拼成行列错乱的一段，结构丢失；
- pymupdf 的 get_images() 只抓内嵌位图，用矢量指令画的架构图/柱状图完全捕获不到。
docling 用版面分析把页面切成 Text/Table/Picture block：表格能导出 Markdown，
Picture 基于渲染页面裁剪（不依赖是否为内嵌对象），所以矢量图表也能被抠出来交给视觉 LLM。

为什么不再"整份 PDF 一个块"：一份几十页的 PDF 编码成一个向量，局部要点会被整篇稀释，
命中后返回的还是整篇。改为按页/段/表/图切，检索命中的是具体那一页那一段。
"""

import asyncio
import io
from pathlib import Path

try:
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling_core.types.doc import TableItem, PictureItem
    HAS_DOCLING = True
except ImportError:
    HAS_DOCLING = False

import re

from .base import DocumentChunk
from .splitter import split_plain, split_table_md
from .vision import caption_image


# 页面级大标题的特征：短文本、无换行，且命中"领域关键词"（介质/汽车级/系列名等）。
# 这类标题是"祖先标题"——同页后续更细的小节标题（如 "Capacitance Range"）会顶掉它，
# 导致 X7R/X8R 这种页面级上下文从数据块里丢失，故单独维护 page_title 不被覆盖。
# 关键词按自己的文档域调整；这里给的是"内部规范/器件手册"常见的一组示例。
_PAGE_TITLE_HINT = re.compile(
    r"(MLCC|X7R|X8R|X5R|NP0|Dielectric|Automotive|Commercial|规范|架构|错误码)",
    re.IGNORECASE,
)


def _is_page_title(text: str) -> bool:
    """判断一个标题是否是"页面级祖先标题"（短、单行、命中领域关键词）。"""
    text = text.strip()
    if not text or len(text) > 60 or "\n" in text:
        return False
    return bool(_PAGE_TITLE_HINT.search(text))


def _doc_main_entity(all_text: str) -> str:
    """统计整份文档出现频次最高的"实体/型号族"（如 LQG15HH / VCHA085D）。
    图片描述由视觉 LLM 生成，不含"该图属于哪个型号/文档"的信息，同类图（两份文档的
    卷盘图）无法区分。用这个主实体给图片补"出身"，让检索能定位到正确文档的图。"""
    from collections import Counter
    cnt: Counter = Counter()
    # 实体样式：字母开头、含数字、长度≥6（如 LQG15HH6N8J02D、VCHA085D）
    for m in re.findall(r"[A-Za-z]{2,}[0-9][A-Za-z0-9]{2,}", all_text):
        cnt[m[:8].upper()] += 1   # 取前 8 位作为"实体族"
    return cnt.most_common(1)[0][0] if cnt else ""


class PDFLoader:

    SUPPORTED_EXTENSIONS = {".pdf"}

    # 单个 PDF 最多处理的图片数量（防止超大 PDF 消耗太多 API 调用）
    MAX_IMAGES = 20

    # 忽略小于此大小的图片（编码为 PNG 后的字节数）——通常是装饰性小图标
    MIN_IMAGE_BYTES = 5000

    def _make_converter(self) -> "DocumentConverter":
        """构造 docling 转换器，开启图片渲染裁剪（否则 PictureItem 拿不到图像）。"""
        opts = PdfPipelineOptions()
        opts.generate_picture_images = True   # 让 PictureItem 携带渲染后的图像
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    async def load(self, path: Path) -> list[DocumentChunk]:
        """
        异步加载单个 PDF 文件。

        docling 的解析是 CPU 密集的同步调用，放进线程池（asyncio.to_thread）不阻塞事件循环；
        图片描述需要调用 LLM（网络请求），多张图片通过 asyncio.gather() 并发处理。
        """
        if not HAS_DOCLING:
            print("  [PDFLoader] 未安装 docling，跳过 PDF。运行：uv add docling")
            return []

        try:
            converter = self._make_converter()
            result = await asyncio.to_thread(converter.convert, str(path))
            document = result.document
        except Exception as e:
            print(f"  [PDFLoader] 无法解析 {path}: {e}")
            return []

        page_count = len(document.pages)
        title = path.stem.replace("_", " ").replace("-", " ")

        def _page_of(item) -> int:
            """从 block 的 provenance 里取 1 基页码，取不到则归到第 1 页。"""
            try:
                return item.prov[0].page_no
            except (AttributeError, IndexError):
                return 1

        # ── 第 1 遍：按文档顺序遍历 block，维护 section_title / page_title ──────
        # 为什么要"按顺序"而不再"按页分组后统一处理"：section_title/page_title 是随
        # block 流推进的状态——只有顺序遍历才能知道某段正文/某张表属于最近哪个标题。
        # 图片需要调 LLM 描述，先只收集字节 + 落一个占位，第 2 遍再并发填描述，保持原有并发。
        pending: list[dict] = []                 # 有序的待生成块（含已解析好的 section/page_title）
        pending_images: list[dict] = []          # 指向 pending 里的图片占位，供第 2 遍回填描述
        text_buf: list[str] = []
        buf_page = 1
        section_title = ""
        page_title = ""
        cur_page = None
        all_text_parts: list[str] = []           # 收集全文用于统计主实体（给图片补出身）

        def _titles_now() -> tuple[str, str]:
            """当前生效的 (title_path, ancestor_title)：page_title > section_title 层级拼接。"""
            ancestor = page_title if page_title and page_title != section_title else ""
            if ancestor and section_title:
                tp = f"{title} > {ancestor} > {section_title}"
            else:
                tp = f"{title} > {section_title}" if section_title else title
            return tp, ancestor

        def flush_text():
            """把攒着的正文按 splitter 控长切成多段，各自落成待生成块。"""
            nonlocal text_buf
            body = "\n".join(text_buf).strip()
            text_buf = []
            if not body:
                return
            tp, ancestor = _titles_now()
            for seg in split_plain(body):
                pending.append({"kind": "text", "text": seg["text"], "page": buf_page,
                                "section_title": section_title, "page_title": page_title,
                                "ancestor_title": ancestor, "title_path": tp})

        for item, _level in document.iterate_items():
            page = _page_of(item)
            if page != cur_page:
                cur_page = page
                page_title = ""        # 翻页重置：祖先标题只在本页内生效

            if isinstance(item, TableItem):
                flush_text()
                md = item.export_to_markdown(document).strip()
                if not md:
                    continue
                tp, ancestor = _titles_now()
                table_id = f"{path.stem}_tbl_p{page}_{len(pending)}"
                # 超长表格按行切、每片重复标题+表头（见 15.7.6 split_table_md）
                for part in split_table_md(md, title=section_title, table_id=table_id):
                    pending.append({"kind": "table", "text": f"【表格】\n{part['text']}",
                                    "page": page, "section_title": section_title,
                                    "page_title": page_title, "ancestor_title": ancestor,
                                    "title_path": tp, "table_id": part["table_id"],
                                    "table_part_idx": part["table_part_idx"],
                                    "table_part_count": part["table_part_count"],
                                    "is_table_part": part["is_table_part"]})

            elif isinstance(item, PictureItem):
                flush_text()
                if len(pending_images) >= self.MAX_IMAGES:
                    continue
                pil_img = item.get_image(document)   # 渲染后的 PIL 图像；未渲染时为 None
                if pil_img is None:
                    continue
                buf = io.BytesIO()
                pil_img.convert("RGB").save(buf, format="PNG")
                img_bytes = buf.getvalue()
                if len(img_bytes) < self.MIN_IMAGE_BYTES:   # 过滤装饰性小图
                    continue
                tp, ancestor = _titles_now()
                slot = {"kind": "image", "text": "", "page": page,
                        "section_title": section_title, "page_title": page_title,
                        "ancestor_title": ancestor, "title_path": tp,
                        "image_path": f"imgs/{path.stem}_img_p{page}_{len(pending_images)}.png",
                        "_img_bytes": img_bytes}
                pending.append(slot)
                pending_images.append(slot)

            elif hasattr(item, "text") and item.text and item.text.strip():
                text = item.text.strip()
                all_text_parts.append(text)
                # SectionHeaderItem 视为标题：切断上文、更新 section_title，标题本身也成块
                if type(item).__name__ == "SectionHeaderItem":
                    flush_text()
                    section_title = text
                    if not page_title and _is_page_title(section_title):
                        page_title = section_title   # 本页第一个"大标题"记为祖先，不被后续覆盖
                    tp, ancestor = _titles_now()
                    pending.append({"kind": "text", "text": text, "page": page,
                                    "section_title": section_title, "page_title": page_title,
                                    "ancestor_title": ancestor, "title_path": tp})
                else:
                    buf_page = page
                    text_buf.append(text)
        flush_text()

        # ── 第 2 遍：并发描述图片，给每张补"出处"（章节 + 文档主实体），回填 pending ──
        if pending_images:
            print(f"  [PDFLoader] {path.name}：正在描述 {len(pending_images)} 张图片（并发）...")
            captions = await asyncio.gather(*[
                caption_image(s["_img_bytes"], "image/png") for s in pending_images
            ])
            main_entity = _doc_main_entity("\n".join(all_text_parts))
            for slot, caption in zip(pending_images, captions):
                if not caption:
                    slot["kind"] = "_drop"       # 描述失败：标记丢弃，第 3 遍跳过
                    continue
                origin = " ".join(x for x in [slot["section_title"], main_entity] if x)
                slot["text"] = "\n".join(x for x in [
                    origin and f"[出处] {origin}",
                    f"【图片描述】: {caption}",
                ] if x)

        # ── 第 3 遍：按原始顺序生成 DocumentChunk ──────────────────────────────
        chunks: list[DocumentChunk] = []
        for i, p in enumerate(x for x in pending if x["kind"] != "_drop"):
            if not p["text"].strip():
                continue
            chunks.append(DocumentChunk(
                source=str(path),
                title=title,
                text=p["text"],
                searchable_text=f"[标题路径] {p['title_path']}\n{p['text']}",
                section_title=p["section_title"],
                title_path=p["title_path"],
                chunk_index=i,
                chunk_type=p["kind"],
                page_title=p["page_title"],
                ancestor_title=p["ancestor_title"],
                table_id=p.get("table_id", ""),
                table_part_idx=p.get("table_part_idx", 0),
                table_part_count=p.get("table_part_count", 1),
                is_table_part=p.get("is_table_part", False),
                image_count=1 if p["kind"] == "image" else 0,
                image_path=p.get("image_path", ""),
                page_num=p["page"],
                page_count=page_count,
            ))
        return chunks