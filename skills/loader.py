# skills/loader.py
"""
Skills 加载器：读取 SKILL.md 文件，解析 frontmatter 和内容。
"""
import re
from pathlib import Path
from dataclasses import dataclass


@dataclass
class Skill:
    name: str
    description: str
    triggers: list[str]   # 触发关键词列表
    content: str          # Skill 的主体内容（注入到 system prompt 的部分）
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
            print(f"[Skills] 加载 {path.name} 失败：{e}")

    print(f"[Skills] 已加载 {len(skills)} 个 Skill")
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

    # 简单解析 YAML（不用 PyYAML 避免额外依赖）
    meta = {}
    current_key = None
    current_list = None

    for line in frontmatter_text.split("\n"):
        if line.startswith("  - "):   # 列表项
            if current_list is not None:
                current_list.append(line[4:].strip())
        elif ": " in line:           # 普通键值对
            key, _, value = line.partition(": ")
            key = key.strip()
            value = value.strip()
            if not value:            # 空值表示后面是列表
                current_list = []
                meta[key] = current_list
                current_key = key
            else:
                meta[key] = value
                current_list = None

    return Skill(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        triggers=meta.get("triggers", []),
        content=body.strip(),
        path=str(path),
    )
