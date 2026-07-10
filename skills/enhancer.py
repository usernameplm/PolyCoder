# skills/enhancer.py
"""
Skill 增强模块：给 system prompt 动态拼接相关团队规范。
两套架构的基类（SubAgent、SwarmAgent）都调用这个函数。
"""
from skills.searcher import SkillSearcher
from observability.logging import logger

# 全局单例，避免重复加载和计算 IDF
_searcher = SkillSearcher(skills_dir="skills/")


def enhance_system_prompt(base_system: str, context: str, agent_name: str = "") -> str:
    """
    根据任务上下文搜索相关 Skill，拼接到 system prompt 后面。

    参数：
        base_system  原始 system prompt
        context      搜索用的文本（任务描述、文件名、代码片段等拼接）
        agent_name   调用者标识（打日志用）
    """
    matched = _searcher.search(context, top_k=2)
    if not matched:
        return base_system

    skill_section = "\n\n---\n\n".join(
        f"【团队规范 - {s.name}】\n{s.content}" for s in matched
    )
    logger.info(
        "skill_matched",
        agent_id=agent_name or "Skills",
        skills=[s.name for s in matched],
    )
    return f"{base_system}\n\n以下是团队规范，请在工作中遵守：\n\n{skill_section}"