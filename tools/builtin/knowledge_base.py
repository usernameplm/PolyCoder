# tools/builtin/knowledge_base.py
"""
多模态本地知识库检索工具（BM25 + 向量 双路召回 + RRF 融合版）。

支持三种文件格式：
  .txt / .md  → TextLoader（切成多个语义 chunk）
  .pdf        → PDFLoader（docling 解析文本/表格/图片 block，表格转 Markdown + 并发调用视觉 LLM 描述图片）
  .jpg .png 等 → ImageLoader（调用视觉 LLM 描述整张图片）

检索算法：BM25（稀疏/字面）+ Embedding 向量（稠密/语义）双路召回，再用 RRF 融合排序。
  - 稠密：sentence-transformers 加载 bge-small-zh-v1.5 编码成 512 维向量，存 Qdrant（cosine）；
  - 稀疏：rank-bm25 用全部 chunk 语料在进程内现建 BM25 索引，擅长精确术语/型号/错误码；
  - 融合：两路各取 topn，按"名次"做 Reciprocal Rank Fusion（k=60），得到 rrf_score 重排。
  为什么要两路融合：向量懂语义但对罕见精确 token 弱，BM25 擅长精确匹配但不懂同义，
  RRF 只看名次、无需归一化两套不可比的分数，是混合检索的事实标准（对齐 DDH-main-agent）。

为什么是 bge-small-zh-v1.5 + Qdrant + rank-bm25：
  - bge-small-zh-v1.5：BAAI 开源、中文效果强、仅 ~100MB，CPU 就能跑，完全免费、离线、数据不出本地；
  - Qdrant：专用向量库，Docker 一键启动，原生支持 cosine 距离 / HNSW 索引 / 元数据过滤；
  - rank-bm25：纯 Python、零外部服务，索引进程内现建，几万块以内毫秒级；
  - 三者都无需 API Key，和项目"本地 + Docker 部署"的调性一致。
"""

import asyncio
import json
import re
from pathlib import Path

from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from rank_bm25 import BM25Okapi

from tools.base import BaseTool
from tools.document_loaders.base import DocumentChunk
from tools.document_loaders.text_loader import TextLoader
from tools.document_loaders.pdf_loader import PDFLoader
from tools.document_loaders.image_loader import ImageLoader
from providers.types import ToolDefinition


# ── Embedding 模型（进程内单例，避免重复加载 ~100MB 权重）─────────────────────────

_EMBED_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
_EMBED_DIM = 512  # bge-small-zh-v1.5 输出维度
_RRF_K = 60       # RRF 融合常数（业界惯例；越大越平滑，越小越偏向头部名次）
_embedder: SentenceTransformer | None = None


def _tokenize(text: str) -> list[str]:
    """BM25 分词：保留字母数字型号/错误码（如 err4001）与单个中文字符。
    与 DDH-main-agent 的 _tokenize 一致——中文按字切，英文/数字按 token 切。"""
    return re.findall(r"[a-z0-9]+|[一-鿿]", text.lower())


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
        self._bm25: BM25Okapi | None = None   # 稀疏索引，_build_index 时现建
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
        """延迟加载：第一次被调用时触发文档加载 + 建两路索引（向量+BM25），之后直接返回。"""
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

    @staticmethod
    def _index_text(doc: DocumentChunk) -> str:
        """真正拿去检索的文本：优先 searchable_text（含标题路径），回退 text。"""
        return doc.searchable_text or doc.text

    def _build_index(self):
        """建两路索引：Qdrant 稠密向量 + rank-bm25 稀疏索引（都用 searchable_text）。"""
        # 稠密：每次重建 Qdrant 集合（按 embedding 维度 + cosine 距离）
        self._qdrant.recreate_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=_EMBED_DIM, distance=Distance.COSINE),
        )
        self._bm25 = None

        if not self._docs:
            return

        index_texts = [self._index_text(doc) for doc in self._docs]

        # 稠密索引：编码 searchable_text → 写入 Qdrant。id 用 chunk 下标，检索后据此回取
        vectors = _embed(index_texts)
        points = [
            PointStruct(
                id=i,
                vector=vec,
                # payload 存原始内容 + 定位信息，命中后直接取回，无需回查文件
                payload={
                    "title": doc.title,
                    "source": doc.source,
                    "text": doc.text,
                    "title_path": doc.title_path,
                    "page_num": doc.page_num,
                    "image_count": doc.image_count,
                },
            )
            for i, (vec, doc) in enumerate(zip(vectors, self._docs))
        ]
        self._qdrant.upsert(collection_name=self.collection, points=points)

        # 稀疏索引：用同一份语料在进程内现建 BM25（下标与 self._docs 对齐）
        self._bm25 = BM25Okapi([_tokenize(t) for t in index_texts])

        print(f"  [KnowledgeBase] 双路索引完成：{len(points)} 个向量"
              f"（{_EMBED_MODEL_NAME}，{_EMBED_DIM} 维）+ BM25 稀疏索引")

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
            "采用 BM25 + 向量双路召回、RRF 融合排序，"
            "返回最相关的文档片段（含 title/source/title_path/rrf_score）作为回答依据。"
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

    # 双路各召回的候选数（融合前）：取得比 top_k 宽，给 RRF 足够的重排空间
    _RECALL_N = 30
    # 融合后 rrf_score 低于此值视为"几乎无匹配"，整体判为未命中
    # （RRF 分数量级由 1/(k+rank) 决定，k=60 时单路第 1 名约 0.016，两路都命中约 0.032）
    _RRF_MIN = 0.005

    def _dense_rank(self, query: str) -> list[int]:
        """稠密召回：query 编码后在 Qdrant 找近邻，返回 chunk 下标按相似度降序。"""
        query_vec = _embed([query])[0]
        hits = self._qdrant.search(
            collection_name=self.collection,
            query_vector=query_vec,
            limit=self._RECALL_N,
        )
        return [int(h.id) for h in hits]

    def _sparse_rank(self, query: str) -> list[int]:
        """稀疏召回：BM25 对 query 打分，返回 chunk 下标按分数降序（取前 _RECALL_N）。"""
        if self._bm25 is None:
            return []
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        # 只保留有正分的（BM25 无任何词命中时为 0，无意义）
        return [i for i in order if scores[i] > 0][:self._RECALL_N]

    async def execute(self, inputs: dict) -> str:
        """
        检索知识库：BM25 + 向量双路召回 → RRF 融合 → 返回 top_k。

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

        # 1. 双路召回：稠密（语义）+ 稀疏（字面），各得一份按名次排好的 chunk 下标列表
        dense = self._dense_rank(query)
        sparse = self._sparse_rank(query)

        # 2. RRF 融合：每路里第 rank 名（0 基）贡献 1/(k + rank + 1)，两路相加后重排。
        #    只看名次、不看原始分数，天然消除 cosine 与 BM25 量纲不可比的问题。
        rrf: dict[int, float] = {}
        for rank, i in enumerate(dense):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)
        for rank, i in enumerate(sparse):
            rrf[i] = rrf.get(i, 0.0) + 1.0 / (_RRF_K + rank + 1)

        ranked = sorted(rrf, key=lambda i: rrf[i], reverse=True)

        results = []
        for i in ranked:
            score = rrf[i]
            if score < self._RRF_MIN:   # 融合后仍极低，判为无匹配
                break
            doc = self._docs[i]
            results.append({
                "title": doc.title,
                "source": doc.source,
                "title_path": doc.title_path,   # 命中片段所在的标题层级，供来源归因
                "rrf_score": round(score, 4),   # 融合置信度，Agent 判断是否改写重试的核心信号
                "content": _extract_best_chunk(doc.text),
                "has_images": doc.image_count > 0,
            })
            if len(results) >= top_k:
                break

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