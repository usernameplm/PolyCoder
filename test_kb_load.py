# test_kb_load.py — 运行：uv run python test_kb_load.py
import asyncio
import json
from tools.knowledge_base import KnowledgeBaseTool


async def main():
    """
    验证知识库加载 + 语义检索效果。

    分两步：
      1. 加载知识库文档，检查文档块数量
      2. 用不同说法查询，验证语义匹配（非字面匹配）
    """
    kb = KnowledgeBaseTool(kb_dir="knowledge_base", qdrant_url=":memory:")

    # ── 步骤 1：加载 ──────────────────────────
    print("=" * 60)
    print("步骤 1：加载知识库")
    print("=" * 60)
    await kb.ensure_loaded()
    print(f"\n加载完成：{len(kb._docs)} 个文档块")
    for doc in kb._docs:
        print(f"  [{doc.source}]  图片数={doc.image_count}  字符数={len(doc.text)}")

    # ── 步骤 2：检索验证 ──────────────────────
    print(f"\n{'=' * 60}")
    print("步骤 2：语义检索验证")
    print("=" * 60)

    queries = [
        ("怎么发 HTTP 请求？", "api_spec"),
        ("变量和类应该怎么命名？", "coding_style"),
        ("错误码 1002 是什么意思？", "coding_style"),
        ("分页参数怎么传？", "api_spec"),
        ("异步协程怎么写？", "coding_style"),
        ("今天天气怎么样？", None),  # 无关查询，应未命中
    ]

    for query, expected_source in queries:
        result = await kb.execute({"query": query, "top_k": 1})
        data = json.loads(result)

        if expected_source is None:
            ok = not data.get("found")
            status = "✓" if ok else "✗"
            print(f"  {status}  查询「{query}」→ {'未命中' if ok else '意外命中: ' + str(data.get('results', [{}])[0].get('source', '?'))} ")
        else:
            top = data["results"][0] if data.get("found") else None
            ok = top and expected_source in top["source"]
            status = "✓" if ok else "✗"
            source = top["source"] if top else "无结果"
            score = f"({top['relevance']})" if top else ""
            print(f"  {status}  查询「{query}」→ {source} {score}")

    print(f"\n全部测试完成。")


if __name__ == "__main__":
    asyncio.run(main())
