# demo_kb.py —— 独立验证知识库向量检索（不调用对话 LLM）
import asyncio
import json

from tools.builtin.knowledge_base import KnowledgeBaseTool


async def main():
    # 用 :memory: 内存向量库，不依赖 Docker 起的 Qdrant，跑完即清空
    # 想连真实 Qdrant 就删掉 qdrant_url 参数（默认 http://localhost:6333）
    kb = KnowledgeBaseTool(kb_dir="knowledge_base", qdrant_url=":memory:")

    # 首次调用会：加载文档 → 下载/加载 bge-small-zh-v1.5 → 编码 → 写入向量库
    print("首次加载（含模型下载 + 建索引，可能要几秒到几十秒）...")
    await kb.ensure_loaded()

    # 故意用和原文不同的说法，验证"语义匹配"而非"字面匹配"
    queries = [
        "怎么发 HTTP 请求？",       # 语义 ≈ 内部请求封装（原文写的是 HttpClient / 对外请求）
        "变量和类应该怎么命名",     # 语义 ≈ 命名规范
        "并发跑多个协程怎么写",     # 语义 ≈ 异步规范（asyncio.gather）
        "今天天气怎么样",           # 无关：应命中不到或分数很低
    ]

    for q in queries:
        print(f"\n{'=' * 60}\n查询：{q}")
        raw = await kb.execute({"query": q, "top_k": 2})
        data = json.loads(raw)
        if not data.get("found"):
            print("  → 未命中（符合无关查询的预期）")
            continue
        for i, r in enumerate(data["results"], 1):
            loc = r["title_path"] or r["title"]
            print(f"  {i}. [rrf={r['rrf_score']}] {loc}（{r['source']}）")
            print(f"     {r['content'][:60]}...")


if __name__ == "__main__":
    asyncio.run(main())