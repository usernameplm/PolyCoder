# sub_agents/knowledge_agent.py
"""
知识库检索子 Agent（14.11：RAG 作为一个 Skill 接入 Coordinator）。

定位：编码任务的"领域知识供给方"——查出内部 API 规范、编码规范、错误码
等信息，作为前置任务的产出，供后续 code_writer / code_reviewer 当上下文使用。
检索策略不写死在 system_prompt，而是放在 skills/knowledge-base-rag.md 里，
由 LLM 在循环中用 get_skill_guide 按需加载（和团队规范 Skill 同一套机制）。
"""
from .base import SubAgent
from tools.builtin.knowledge_base import KnowledgeBaseTool
from core.config import settings


class KnowledgeAgent(SubAgent):

    def __init__(self, kb_dir: str = "knowledge_base",
                 qdrant_url: str | None = None):
        # kb_dir / qdrant_url 可注入，方便测试时换成 tests/fixtures 下的测试知识库、
        # 或走 ":memory:" 免 Docker（15.10/15.11）。
        # 默认读 settings.qdrant_url（.env / .env.docker 里配的），本地是 localhost、
        # Docker 部署是服务名 qdrant——和 tools/registry.py 注册工具时的口径一致。
        self._kb_dir = kb_dir
        # ⚠️ 关键：工具实例只建一次并缓存。
        # 如果把 tools 写成每次访问都 new 一个 KnowledgeBaseTool，
        # SubAgent.run() 的工具循环里会多次读 .tools，于是反复新建工具、
        # 重连 Qdrant、从头 _load_all()+_build_index()（重编码全部 chunk、recreate 清库），
        # 懒加载缓存完全失效。放到 __init__ 里建一次即可。
        self._kb_tool = KnowledgeBaseTool(
            kb_dir=kb_dir,
            qdrant_url=qdrant_url or settings.qdrant_url,
        )

    @property
    def name(self) -> str:
        return "knowledge_agent"

    @property
    def system_prompt(self) -> str:
        # 瘦身：不再把检索策略写死在这里，改为引导 LLM 先加载 knowledge-base-rag 技能，
        # 按其中的决策树操作。检索规范的"单一事实来源"是那份 SKILL.md，不是这段 prompt。
        return (
            "你是团队内部技术知识库检索专家，服务于编码类子任务——"
            "为 code_writer / code_reviewer / debugger 供给团队的内部 API 规范、"
            "编码规范、错误码约定、架构约定。\n"
            "当需要检索知识库时，**先调用 get_skill_guide('knowledge-base-rag') 获取检索操作指南**，"
            "再严格按指南里的决策树驱动 search_knowledge_base 工具（评估→改写→重试→带来源归因）。"
            "绝不凭空编造 API 或规范；知识库里没有的，如实说明。"
        )

    @property
    def tools(self):
        # 只需挂 search_knowledge_base；get_skill_guide 由 SubAgent.run() 恒定注入（见 9.5）。
        # 返回 __init__ 里缓存的同一个实例，不要在这里 new（否则索引每次重建）。
        return [self._kb_tool]
