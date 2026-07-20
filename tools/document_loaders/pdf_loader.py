# tools/document_loaders/pdf_loader.py
"""
PDF 文档加载器。

处理流程（每个 PDF 生成一个 DocumentChunk）：
1. 用 pymupdf (fitz) 打开 PDF
2. 逐页提取文字，拼接成完整文本
3. 逐页提取图片，对每张图片调用 vision.caption_image()
4. 把图片描述以「图片N描述」格式拼接到文本末尾
5. 返回一个包含所有内容的 DocumentChunk
"""

import asyncio
from pathlib import Path

try:
    import fitz  # pymupdf 安装后用 import fitz
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

from .base import DocumentChunk
from .vision import caption_image, _guess_media_type


class PDFLoader:

    SUPPORTED_EXTENSIONS = {".pdf"}

    # 单个 PDF 最多处理的图片数量（防止超大 PDF 消耗太多 API 调用）
    MAX_IMAGES = 99

    # 忽略小于此大小的图片（字节）——通常是装饰性小图标
    MIN_IMAGE_BYTES = 5000

    async def load(self, path: Path) -> list[DocumentChunk]:
        """
        异步加载单个 PDF 文件。

        这是 async 方法，因为图片描述需要调用 LLM（网络请求）。
        多张图片通过 asyncio.gather() 并发处理，不会串行等待。
        """
        if not HAS_PYMUPDF:
            print("  [PDFLoader] 未安装 pymupdf，跳过 PDF。运行：uv add pymupdf")
            return []

        try:
            doc = fitz.open(str(path))
        except Exception as e:
            print(f"  [PDFLoader] 无法打开 {path}: {e}")
            return []

        all_text_parts: list[str] = []
        all_images: list[tuple[bytes, str]] = []  # (图片二进制, media_type)

        # 遍历每一页
        for page_num in range(len(doc)):
            page = doc[page_num]

            # 步骤 1：提取当前页的文字
            page_text = page.get_text("text").strip()
            if page_text:
                all_text_parts.append(f"[第{page_num + 1}页]\n{page_text}")

            # 步骤 2：提取当前页的图片（如果还没超上限）
            if len(all_images) < self.MAX_IMAGES:
                for img_info in page.get_images(full=True):
                    xref = img_info[0]  # 图片的 xref 编号（PDF 内部 ID）
                    try:
                        img_data = doc.extract_image(xref)
                        # img_data 是字典，包含 "image"（二进制）和 "ext"（扩展名）
                        img_bytes = img_data["image"]
                        img_ext = "." + img_data.get("ext", "jpg").lower()
                        media_type = _guess_media_type(img_ext)

                        # 过滤掉太小的图片
                        if len(img_bytes) < self.MIN_IMAGE_BYTES:
                            continue

                        all_images.append((img_bytes, media_type))

                        if len(all_images) >= self.MAX_IMAGES:
                            break
                    except Exception:
                        continue

        page_count = len(doc)
        doc.close()

        # 步骤 3：并发调用视觉 LLM 描述所有图片
        image_count = len(all_images)
        if all_images:
            print(f"  [PDFLoader] {path.name}：正在描述 {image_count} 张图片（并发）...")
            # asyncio.gather 同时发起所有视觉请求，比串行快很多
            captions = await asyncio.gather(*[
                caption_image(img_bytes, mt)
                for img_bytes, mt in all_images
            ])

            # 把描述追加到文本内容
            for i, caption in enumerate(captions, 1):
                if caption:
                    all_text_parts.append(f"【图片{i}描述】: {caption}")

        # 合并所有内容
        full_text = "\n\n".join(all_text_parts).strip()
        if not full_text:
            return []

        return [
            DocumentChunk(
                source=str(path),
                title=path.stem.replace("_", " ").replace("-", " "),
                text=full_text,
                image_count=image_count,
                page_count=page_count,
            )
        ]