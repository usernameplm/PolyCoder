# tools/document_loaders/vision.py
"""
调用视觉 LLM，为图片生成文字描述。

这个函数是多模态 RAG 的核心：
它把图片"翻译"成文字，让基于文本 embedding 的检索引擎也能理解图片内容。
"""

import base64
from providers.types import Message, TextBlock, ImageBlock
from providers.router import get_vision_provider


_CAPTION_SYSTEM = "你是一个文档图片分析专家，擅长从各种图片中提取关键信息。"

_CAPTION_PROMPT = """请用中文详细描述这张图片中的所有重要信息：

- 如果是数据图表（折线图、柱状图、饼图等）：说明图表类型、坐标轴含义、主要数据点和趋势
- 如果是流程图或架构图：说明各模块的名称、相互关系、数据流向
- 如果是截图或界面：说明界面名称、主要功能区域、关键文字信息
- 如果是照片或示意图：说明主要内容、包含的文字

输出纯文字，不要使用 Markdown 格式，不要加标题，直接描述内容。
尽量详细，这些描述将用于知识库检索。"""


def _guess_media_type(suffix: str) -> str:
    """根据文件扩展名猜测 MIME 类型。"""
    mapping = {
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png":  "image/png",
        ".webp": "image/webp",
        ".gif":  "image/gif",
    }
    return mapping.get(suffix.lower(), "image/jpeg")


async def caption_image(image_bytes: bytes, media_type: str = "image/jpeg") -> str:
    """
    把图片发给视觉 LLM，返回文字描述。

    参数：
        image_bytes  图片的原始二进制数据
        media_type   图片的 MIME 类型

    返回：
        中文文字描述（出错时返回空字符串）

    工作原理：
        1. 把二进制图片数据用 base64 编码，变成文字字符串
        2. 构造一条包含图片块和文字指令的消息
        3. 发给视觉 Provider，等待描述文字

    这里用 get_vision_provider()而不是 get_provider()：图片理解可以配置成和主对话
    完全独立的 Provider/模型（见 15.6.5、15.6.6），不受 LLM_PROVIDER 配的是否支持视觉影响。
    如果没配 VISION_PROVIDER，也没配 VISION_CAPABLE，选中的模型又确实不支持视觉，
    get_vision_provider() 会在这里直接抛 ValueError，而不是把图片悄悄丢掉再返回一段乱猜的描述。
    """
    if not image_bytes:
        return ""

    provider = get_vision_provider()
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    messages = [
        Message(
            role="user",
            content=[
                ImageBlock(
                    source_type="base64",
                    media_type=media_type,
                    data=b64_data,
                ),
                TextBlock(text=_CAPTION_PROMPT),
            ],
        )
    ]

    try:
        response = await provider.chat(
            messages=messages,
            system=_CAPTION_SYSTEM,
            max_tokens=512,
        )
        for block in response.content:
            if isinstance(block, TextBlock):
                return block.text.strip()
    except Exception as e:
        print(f"  [Vision] 图片描述失败：{e}")

    return ""