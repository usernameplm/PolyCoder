# tools/knowledge_base.py
"""
多模态本地知识库检索工具（向量检索版）。

支持三种文件格式：
  .txt / .md  → TextLoader（直接读取文字）
  .pdf        → PDFLoader（提取文字 + 并发调用视觉 LLM 描述图片）
  .jpg .png 等 → ImageLoader（调用视觉 LLM 描述整张图片）

检索算法：Embedding（bge-small-zh-v1.5）+ Qdrant 向量库 + 余弦相似度。
  - 用 sentence-transformers 加载本地开源模型 bge-small-zh-v1.5，把文本编码成 512 维向量；
  - 向量存进 Qdrant（本地 Docker，cosine 距离），查询时对 query 编码后做近邻检索；
  - 相比 TF-IDF 的"字面匹配"，向量检索能理解语义（"请假"能匹配到"休假/年假"）。

为什么是 bge-small-zh-v1.5 + Qdrant：
  - bge-small-zh-v1.5：BAAI 开源、中文效果强、仅 ~100MB，CPU 就能跑，完全免费、离线、数据不出本地；
  - Qdrant：专用向量库，Docker 一键启动，原生支持 cosine 距离 / HNSW 索引 / 元数据过滤；
  - 两者都无需 API Key，和项目"本地 + Docker 部署"的调性一致。
"""

import asyncio
import json
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from tools.base import BaseTool
from tools.document_loaders.base import DocumentChunk
from tools.document_loaders.text_loader import TextLoader
from tools.document_loaders.pdf_loader import PDFLoader
from tools.document_loaders.image_loader import ImageLoader
from providers.types import ToolDefinition


# ── Embedding 模型（进程内单例，避免重复加载 ~100MB 权重）─────────────────────────

_EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_EMBED_DIM = 512  # bge-small-zh-v1.5 输出维度
_embedder: SentenceTransformer | None = None


def _get_embedder() -> SentenceTransformer:
    """懒加载 embedding 模型。首次调用会自动从 HuggingFace 下载权重并缓存到本地。"""
    global _embedder
    if _embedder is None:
        # 首次加载耗时几秒；normalize_embeddings 在 encode 时开启，配合 cosine 距离
        _embedder = SentenceTransformer(_EMBED_MODEL_NAME)
    return _embedder


def _embed(texts: list[str]) -> list[list[float]]:
    """把一批文本编码成归一化向量（L2 归一化后配合 cosine 距离即等价于点积）。"""
    model = _get_embedder()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,   # 归一化，cosine 相似度更稳定
        convert_to_numpy=True,
    )
    return vectors.tolist()


def _extract_best_chunk(content: str, chunk_size: int = 500) -> str:
    """截取文档开头一段作为展示片段（向量检索命中的是整块，这里只做长度裁剪）。"""
    content = content.strip()
    if len(content) <= chunk_size:
        return content
    return content[:chunk_size] + "..."


# ── 知识库工具 ─────────────────────────────────────────────────────────────────

class KnowledgeBaseTool(BaseTool):

    def __init__(
        self,
        kb_dir: str = "knowledge_base",
        qdrant_url: str = "http://localhost:6333",
        collection: str = "knowledge_base",
    ):
        self.kb_dir = Path(kb_dir)
        self.collection = collection
        self._docs: list[DocumentChunk] = []
        self._loaded = False

        self._text_loader = TextLoader()
        self._pdf_loader = PDFLoader()
        self._image_loader = ImageLoader()

        # 连接 Qdrant（Docker 起的本地服务，见 15.4）。
        # 也可传 ":memory:" 走纯内存模式，适合测试/演示不想起容器的场景。
        if qdrant_url == ":memory:":
            self._qdrant = QdrantClient(":memory:")
        else:
            self._qdrant = QdrantClient(url=qdrant_url)

    async def ensure_loaded(self):
        """延迟加载：第一次被调用时触发文档加载 + 建向量索引，之后直接返回。"""
        if not self._loaded:
            await self._load_all()
            self._build_index()
            self._loaded = True

    async def _load_all(self):
        """扫描目录，对每个文件调用对应的加载器（得到 DocumentChunk 列表）。"""
        if not self.kb_dir.exists():
            print(f"  [KnowledgeBase] 目录不存在：{self.kb_dir}")
            return

        text_paths = (
            sorted(self.kb_dir.rglob("*.txt")) +
            sorted(self.kb_dir.rglob("*.md"))
        )
        pdf_paths = sorted(self.kb_dir.rglob("*.pdf"))
        image_paths = []
        for ext in self._image_loader.SUPPORTED_EXTENSIONS:
            image_paths.extend(sorted(self.kb_dir.rglob(f"*{ext}")))

        total = len(text_paths) + len(pdf_paths) + len(image_paths)
        print(f"  [KnowledgeBase] 发现 {total} 个文件"
              f"（文本 {len(text_paths)}，PDF {len(pdf_paths)}，图片 {len(image_paths)}）")

        for path in text_paths:
            self._docs.extend(self._text_loader.load(path))

        if pdf_paths:
            pdf_results = await asyncio.gather(*[
                self._pdf_loader.load(path) for path in pdf_paths
            ])
            for chunks in pdf_results:
                self._docs.extend(chunks)

        if image_paths:
            img_results = await asyncio.gather(*[
                self._image_loader.load(path) for path in image_paths
            ])
            for chunks in img_results:
                self._docs.extend(chunks)

        print(f"  [KnowledgeBase] 加载完成，共 {len(self._docs)} 个文档块")

    def _build_index(self):
        """把所有 DocumentChunk 编码成向量，写入 Qdrant 集合（重建）。"""
        # 每次重建：删掉旧集合再按 embedding 维度 + cosine 距离新建
        self._qdrant.recreate_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=_EMBED_DIM, distance=Distance.COSINE),
        )

        if not self._docs:
            return

        vectors = _embed([doc.text for doc in self._docs])
        points = [
            PointStruct(
                id=i,
                vector=vec,
                # payload 存原始内容，检索命中后直接取回，无需再回查文件
                payload={
                    "title": doc.title,
                    "source": doc.source,
                    "text": doc.text,
                    "image_count": doc.image_count,
                },
            )
            for i, (vec, doc) in enumerate(zip(vectors, self._docs))
        ]
        self._qdrant.upsert(collection_name=self.collection, points=points)
        print(f"  [KnowledgeBase] 向量索引完成，共 {len(points)} 个向量"
              f"（模型 {_EMBED_MODEL_NAME}，维度 {_EMBED_DIM}）")

    async def reload(self):
        """重新加载知识库并重建向量索引（热更新，不重启服务）。"""
        self._docs.clear()
        self._loaded = False
        await self.ensure_loaded()

    @property
    def name(self) -> str:
        return "search_knowledge_base"

    @property
    def description(self) -> str:
        # 只声明"能力"（这个工具能查什么、返回什么），不承载"检索策略"。
        # 何时改写重试、怎么带来源归因等策略，写在 knowledge-base-rag 这个 Skill 里，
        # 由 knowledge_agent 通过 get_skill_guide 按需加载（见 15.11）。
        count = len(self._docs)
        return (
            f"搜索本地知识库（已加载 {count} 个文档块，支持 PDF、图片、文本）。"
            "需要查内部 API 规范、编码规范、错误码、架构约定等文档内容时调用。"
            "返回最相关的文档片段（含 title/source/relevance）作为回答依据。"
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索问题或关键词，越具体结果越准确",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回最相关的文档数量（1-5），默认 3",
                    "default": 3,
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["query"],
        }

    # cosine 相似度低于此阈值视为不相关（归一化向量下取值范围约 [-1, 1]）
    _SCORE_THRESHOLD = 0.3

    async def execute(self, inputs: dict) -> str:
        """
        检索知识库，返回 top_k 最相关的文档片段。

        inputs 字典由 LLM 填写，包含：
            query   搜索问题（必填）
            top_k   返回数量（可选，默认 3）
        """
        await self.ensure_loaded()

        query = inputs.get("query", "")
        top_k = int(inputs.get("top_k", 3))

        if not self._docs:
            return json.dumps({
                "error": f"知识库为空。请在 {self.kb_dir}/ 目录放置文档。"
            }, ensure_ascii=False)

        # 1. 把 query 编码成向量  2. 在 Qdrant 里做 cosine 近邻检索
        query_vec = _embed([query])[0]
        hits = self._qdrant.query_points(
            collection_name=self.collection,
            query=query_vec,
            limit=top_k,
            score_threshold=self._SCORE_THRESHOLD,  # 低于阈值的直接被过滤掉
        )

        results = []
        for hit in hits.points:
            payload = hit.payload
            results.append({
                "title": payload["title"],
                "source": payload["source"],
                "relevance": round(hit.score, 4),   # cosine 相似度，越接近 1 越相关
                "content": _extract_best_chunk(payload["text"]),
                "has_images": payload["image_count"] > 0,
            })

        if not results:
            return json.dumps({
                "found": False,
                "message": "未找到相关内容",
            }, ensure_ascii=False)

        return json.dumps({
            "found": True,
            "query": query,
            "total_docs": len(self._docs),
            "results": results,
        }, ensure_ascii=False, indent=2)