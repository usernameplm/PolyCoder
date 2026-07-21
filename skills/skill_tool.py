# skills/skill_tool.py
"""
get_skill_guide 工具：LLM 按需加载某个 Skill 的完整指南。

工作方式（对齐 DDH-main-agent 的 get_skill_guide）：
  1. system prompt 里常驻 build_skill_index() 生成的技能索引表（只有 name + description）；
  2. LLM 判断任务需要某技能 → 发起工具调用 get_skill_guide(skill_name="code-review")；
  3. 本工具读取 skills/code-review.md 全文，作为 tool_result 返回；
  4. LLM 带着完整指南继续工作。

这样每个 Skill 的全文只在"真正需要时"进入上下文，且由模型自己决定，而不是每次无条件预注入。
"""
from tools.base import BaseTool
from skills.loader import Skill, load_skills


class GetSkillGuideTool(BaseTool):

    def __init__(self, skills: list[Skill] | None = None, skills_dir: str = "skills/"):
        # 进程内缓存已加载的 Skill（name → Skill），避免每次调用都重扫磁盘。
        self._skills = skills if skills is not None else load_skills(skills_dir)
        self._by_name = {s.name: s for s in self._skills}

    @property
    def name(self) -> str:
        return "get_skill_guide"

    @property
    def description(self) -> str:
        # 把可用技能内嵌进工具描述——这样即使不看 system prompt 的索引表，
        # LLM 在决定调用本工具时也能从参数说明里看到有哪些合法的 skill_name。
        available = "、".join(f"{s.name}（{s.description}）" for s in self._skills)
        return (
            "按需获取某个子技能的完整操作指南。当任务匹配某技能的适用场景时，"
            "先调用本工具拿到详细规范，再据此执行。\n"
            f"可用技能：{available}"
        )

    @property
    def input_schema(self) -> dict:
        valid_names = [s.name for s in self._skills]
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": f"要加载的技能名称，必须是以下之一：{', '.join(valid_names)}",
                }
            },
            "required": ["skill_name"],
        }

    async def execute(self, inputs: dict) -> str:
        skill_name = (inputs.get("skill_name") or "").strip()
        skill = self._by_name.get(skill_name)

        if skill is None:
            # 找不到时不报错，而是返回可用列表，帮 LLM 纠正到合法的 skill_name。
            available = ", ".join(self._by_name.keys())
            return f"未找到技能 '{skill_name}'。可用技能：{available}"

        # 返回 SKILL.md 全文，供 LLM 遵循其中的流程、规范和输出格式。
        return f"【技能指南 - {skill.name}】\n\n{skill.content}"
