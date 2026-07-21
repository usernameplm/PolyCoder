# skills/__init__.py
"""
Skill 系统的公共入口：
  - SKILL_INDEX：技能索引表文本，拼到各子 Agent 的 system prompt 末尾（常驻，极短）
  - get_skill_guide_tool()：返回共享的 GetSkillGuideTool 实例，加进子 Agent 的 tools
两套架构（SubAgent / SwarmAgent）都从这里取，保证索引和工具口径一致。
"""
from skills.loader import load_skills, build_skill_index
from skills.skill_tool import GetSkillGuideTool

# 进程内只加载一次：所有子 Agent 共享同一份 Skill 列表、索引表和工具实例
_SKILLS = load_skills("skills/")
SKILL_INDEX = build_skill_index(_SKILLS)
_SKILL_TOOL = GetSkillGuideTool(skills=_SKILLS)


def get_skill_guide_tool() -> GetSkillGuideTool:
    return _SKILL_TOOL
