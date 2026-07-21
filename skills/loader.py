# skills/loader.py
"""
Skills 加载器：读取 SKILL.md 文件，解析 frontmatter 和内容。
"""
import re
from pathlib import Path
from dataclasses import dataclass

from observability.logging import logger


@dataclass
class Skill:
    name: str
    description: str      # 一句话说明"是什么 + 何时加载"——进索引表和工具描述
    content: str          # Skill 的主体内容（被 get_skill_guide 按需读取的全文）
    path: str


def load_skills(skills_dir: str = "skills/") -> list[Skill]:
    """扫描目录，加载所有 SKILL.md 文件。"""
    skills = []
    skills_path = Path(skills_dir)

    if not skills_path.exists():
        return []

    for path in skills_path.glob("*.md"):
        try:
            skill = _parse_skill_file(path)
            if skill:
                skills.append(skill)
        except Exception as e:
            logger.warning("skill_load_failed", file=path.name, error=str(e))

    logger.info("skills_loaded", count=len(skills))
    return skills


def _parse_skill_file(path: Path) -> Skill | None:
    """解析单个 SKILL.md 文件（frontmatter + 正文）。"""
    content = path.read_text(encoding="utf-8")

    # 解析 YAML frontmatter（--- 之间的部分）
    frontmatter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not frontmatter_match:
        return None

    frontmatter_text = frontmatter_match.group(1)
    body = content[frontmatter_match.end():]

    # 简单解析 YAML（不用 PyYAML 避免额外依赖）。
    # 现在 frontmatter 只有 name / description 两个标量键，不再有 triggers 列表，
    # 所以解析逻辑简化为纯键值对。
    meta = {}
    for line in frontmatter_text.split("\n"):
        if ": " in line:
            key, _, value = line.partition(": ")
            meta[key.strip()] = value.strip()

    return Skill(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        content=body.strip(),
        path=str(path),
    )


def build_skill_index(skills: list[Skill]) -> str:
    """把所有 Skill 渲染成一张「技能索引表」（只含 name + description），供常驻 system prompt。

    这是"按需加载"机制的第一层：LLM 靠这张表知道"有哪些技能、各自何时用"，
    但不加载任何全文——全文由 LLM 自主调用 get_skill_guide(name) 时才读取（第二层）。
    索引表只有几行，token 开销极小，和把 Skill 全文预注入 system prompt 是本质区别。
    """
    if not skills:
        return ""

    rows = "\n".join(f"| `{s.name}` | {s.description} |" for s in skills)
    return (
        "## 可用技能（Skills）\n\n"
        "以下技能提供各领域的团队规范和操作指南。"
        "**当任务匹配某个技能的适用场景时，先调用 `get_skill_guide(skill_name)` 工具"
        "获取它的完整指南，再据此执行**；任务用不到任何技能时，不必调用。\n\n"
        "| skill_name | 适用场景 |\n"
        "|------------|----------|\n"
        f"{rows}"
    )
