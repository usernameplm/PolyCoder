# skills/searcher.py
"""
基于 TF-IDF 的 Skill 相关性搜索。

TF-IDF 是一种简单的文本相关性算法：
  - TF（词频）：词在查询中出现的频率
  - IDF（逆文档频率）：词在所有 Skill 中的"稀有程度"（越稀有越有区分度）

不需要安装额外依赖，用纯 Python 实现。
"""
import math
from .loader import Skill, load_skills


class SkillSearcher:

    def __init__(self, skills: list[Skill] | None = None, skills_dir: str = "skills/"):
        self.skills = skills if skills is not None else load_skills(skills_dir)
        self._idf = self._compute_idf()

    def _compute_idf(self) -> dict[str, float]:
        """计算每个词的 IDF 值（预计算，提升搜索速度）。"""
        n = len(self.skills)
        if n == 0:
            return {}

        # 统计每个词出现在几个 Skill 里
        doc_freq: dict[str, int] = {}
        for skill in self.skills:
            words = self._tokenize(f"{skill.description} {' '.join(skill.triggers)} {skill.content[:500]}")
            for word in set(words):
                doc_freq[word] = doc_freq.get(word, 0) + 1

        # IDF = log(总文档数 / 出现该词的文档数)
        return {word: math.log(n / freq) for word, freq in doc_freq.items()}

    def _tokenize(self, text: str) -> list[str]:
        """简单分词：按空格和标点切分，转小写。"""
        import re
        tokens = re.findall(r'[\w一-鿿]+', text.lower())
        return tokens

    def search(self, query: str, top_k: int = 3) -> list[Skill]:
        """
        搜索与 query 最相关的 top_k 个 Skill。

        搜索优先级：
        1. 精确关键词匹配（triggers）→ 高权重
        2. TF-IDF 相关性评分
        """
        if not self.skills:
            return []

        query_lower = query.lower()
        scores: list[tuple[float, Skill]] = []

        for skill in self.skills:
            score = 0.0

            # 1. 触发词精确匹配（最高权重）
            for trigger in skill.triggers:
                if trigger.lower() in query_lower:
                    score += 10.0
                    break

            # 2. TF-IDF 评分
            query_words = self._tokenize(query)
            skill_words = self._tokenize(f"{skill.description} {skill.content[:500]}")
            skill_word_set = set(skill_words)

            for word in query_words:
                if word in skill_word_set:
                    tf = skill_words.count(word) / (len(skill_words) + 1)
                    idf = self._idf.get(word, 0.0)
                    score += tf * idf

            scores.append((score, skill))

        # 按分数降序，取 top_k
        scores.sort(key=lambda x: x[0], reverse=True)
        return [skill for score, skill in scores[:top_k] if score > 0]