# tools/document_loaders/splitter.py
"""
把长文本切成"语义片段"——按结构（Markdown 标题）+ 控长，不做定长硬切。

为什么不定长硬切（如每 500 字一刀）：
  会把一句话、一行表格、一个代码块从中间截断，chunk 语义破碎、检索命中后不可读。
  改为"沿标题/空行等自然边界切，只在超长时才在段落边界断"，每个 chunk 都是完整语义单元。

返回的每个片段是 dict：{"text", "section_title", "title_path"}，
由各 Loader 包装成 DocumentChunk（见 text_loader.py / pdf_loader.py）。
"""

import re

# 单个 chunk 的粗略 token 上限：中文按字符、英文按词估算，取较大者的近似
MAX_TOKENS = 350
# 过短的尾块并入上一块的阈值（避免切出"只有一个标题"的碎块）
MIN_TOKENS = 20
# 单个表格块的 token 上限：超过就按行切分（表格比正文更长，阈值放宽到 MAX_TOKENS 的两倍多）
TABLE_TOKEN_LIMIT = 800

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# Markdown 表格的分隔行（表头与数据之间那一行，如 |---|:--:|---|）
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _rough_tokens(s: str) -> int:
    """粗略 token 估算：中文按字符数、英文按词数，取较大值。"""
    return max(len(s) // 2, len(s.split()))


def _split_long_paragraphs(text: str) -> list[str]:
    """把一段较长的正文按空行分段后，按 MAX_TOKENS 累加控长（不切断段落）。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    out: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for p in paras:
        pt = _rough_tokens(p)
        if cur and cur_tok + pt > MAX_TOKENS:
            out.append("\n\n".join(cur))
            cur, cur_tok = [], 0
        cur.append(p)
        cur_tok += pt
    if cur:
        out.append("\n\n".join(cur))
    return out


def split_markdown(content: str) -> list[dict]:
    """标题感知切分：以标题为边界，维护 title_path，正文超长再按段落控长。"""
    lines = content.splitlines()
    # 标题栈：[(level, text), ...]，用于拼当前标题路径
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    pieces: list[dict] = []

    def title_path() -> str:
        return " > ".join(t for _, t in stack)

    def section_title() -> str:
        return stack[-1][1] if stack else ""

    def flush():
        body = "\n".join(buf).strip()
        buf.clear()
        if not body:
            return
        tp, st = title_path(), section_title()
        for seg in _split_long_paragraphs(body):
            pieces.append({"text": seg, "section_title": st, "title_path": tp})

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            flush()  # 遇到新标题，先把上一节正文落盘
            level = len(m.group(1))
            heading = m.group(2).strip()
            # 弹出同级或更深的标题，维护层级路径
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
        else:
            buf.append(line)
    flush()

    # 合并过短的碎块到上一块（常见于"只有一个标题、正文极短"）
    merged: list[dict] = []
    for p in pieces:
        if merged and _rough_tokens(p["text"]) < MIN_TOKENS:
            merged[-1]["text"] += "\n\n" + p["text"]
        else:
            merged.append(p)
    return merged or ([{"text": content.strip(),
                        "section_title": "", "title_path": ""}]
                      if content.strip() else [])


def split_plain(content: str) -> list[dict]:
    """纯文本切分：无标题结构，按空行分段 + MAX_TOKENS 控长。"""
    return [{"text": seg, "section_title": "", "title_path": ""}
            for seg in _split_long_paragraphs(content)]


def split_table_md(table_md: str, title: str = "", table_id: str = "") -> list[dict]:
    """表格切分：整表优先；超长则按 Markdown 行边界切，每块重复标题+表头行，绝不断行。

    为什么表格不能像正文那样"按空行控长切"：表格是二维结构，从中间某个字符断开会
    让行列错位、表头对不上，检索命中后完全不可读。所以只在整行（`| ... |`）边界上切，
    且每一片都重复"标题 + 表头行 + 分隔行"，保证任何一片单独拿出来都能读懂每列是什么。
    （对齐 DDH build_chunks 里的 _split_table_html，只是把 HTML <tr> 换成 docling
    export_to_markdown() 产出的 Markdown 行——见 15.7.4 用的正是 export_to_markdown。）

    返回 [{"text", "table_id", "table_part_idx", "table_part_count", "is_table_part"}]。
    """
    def _one_block(text: str) -> list[dict]:
        return [{
            "text": (f"{title}\n" if title else "") + text,
            "table_id": table_id, "table_part_idx": 0,
            "table_part_count": 1, "is_table_part": False,
        }]

    # 未超长：整表作为一块，不切
    if _rough_tokens(table_md) <= TABLE_TOKEN_LIMIT:
        return _one_block(table_md)

    lines = [ln for ln in table_md.splitlines() if ln.strip()]
    # 找到"表头 + 分隔行"：分隔行（|---|---|）之前的都算表头，之后的是数据行
    sep_idx = next((i for i, ln in enumerate(lines) if _MD_TABLE_SEP_RE.match(ln)), -1)
    # 找不到标准表头结构（异常表格）：无法安全按行切，整表退回单块，绝不硬切
    if sep_idx <= 0 or sep_idx >= len(lines) - 1:
        return _one_block(table_md)

    head_lines = lines[: sep_idx + 1]          # 表头行 + 分隔行，每片都要重复
    data_rows = lines[sep_idx + 1:]
    head = (f"{title}\n" if title else "") + "\n".join(head_lines)

    # 按行累加，超过阈值就断到下一片；每片都以 head（标题+表头+分隔行）开头
    parts: list[list[str]] = []
    cur: list[str] = []
    cur_tokens = _rough_tokens(head)
    for r in data_rows:
        rt = _rough_tokens(r)
        if cur and cur_tokens + rt > TABLE_TOKEN_LIMIT:
            parts.append(cur)
            cur = []
            cur_tokens = _rough_tokens(head)   # 新片重新计入 head 的开销
        cur.append(r)                          # 整行加入，绝不切断一行内部
        cur_tokens += rt
    if cur:
        parts.append(cur)

    out: list[dict] = []
    for i, rows_i in enumerate(parts):
        text_i = head + "\n" + "\n".join(rows_i)   # 第 2、3 片也带标题+表头+分隔行
        out.append({
            "text": text_i,
            "table_id": table_id,
            "table_part_idx": i,
            "table_part_count": len(parts),
            "is_table_part": len(parts) > 1,
        })
    return out