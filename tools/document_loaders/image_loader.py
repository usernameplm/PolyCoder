# tools/document_loaders/image_loader.py
"""加载独立图片文件（.jpg, .png, .webp 等）。"""

from pathlib import Path

try:
    from PIL import Image
    import io
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

from .base import DocumentChunk
from .vision import caption_image


class ImageLoader:

    SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

    async def load(self, path: Path) -> list[DocumentChunk]:
        """
        异步加载单个图片文件。

        处理步骤：
        1. 用 PIL 打开图片，确认格式可读
        2. 统一转为 JPEG（减小 base64 体积，降低 token 消耗）
        3. 调用视觉 LLM 生成描述
        4. 返回以描述文字为内容的 DocumentChunk
        """
        if not HAS_PIL:
            print("  [ImageLoader] 未安装 pillow，跳过图片。运行：uv add pillow")
            return []

        try:
            img = Image.open(path)

            # 统一转成 RGB + JPEG 格式
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=85)
            img_bytes = buf.getvalue()

        except Exception as e:
            print(f"  [ImageLoader] 无法读取图片 {path}: {e}")
            return []

        print(f"  [ImageLoader] 正在描述图片 {path.name}...")
        caption = await caption_image(img_bytes, "image/jpeg")

        if not caption:
            return []

        return [
            DocumentChunk(
                source=str(path),
                title=path.stem.replace("_", " ").replace("-", " "),
                text=f"【图片文件描述】\n{caption}",
                image_count=1,
                page_count=0,
            )
        ]