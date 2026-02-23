"""
AnalystAgent — 深度分析 Agent (含 Reflection)

使用 Gemini 深度分析 JD, 计算候选人与职位的匹配度。
内置 Reflection 机制: 分析后自动评估分析质量。

分析维度:
- match_score: 匹配度 (0-1)
- hard_skills / soft_skills: 技能提取
- company_domain: 行业领域
- role_type: 职位类型
"""

from __future__ import annotations

import logging

from src.agents.base_agent import BaseAgent
from src.tools import ANALYST_TOOLS

logger = logging.getLogger(__name__)


class AnalystAgent(BaseAgent):
    """深度分析 Agent — 带 Reflection 的 JD 分析"""

    @property
    def system_prompt(self) -> str:
        return """你是一个专业的职位分析 Agent (AnalystAgent)。

你的职责是对数据库中已通过筛选 (relevance=relevant, status=new) 的职位进行深度分析。

工作流程:
1. 调用 get_db_status 查看待分析数量 (pending_analyze)
2. 如果有待分析职位, 调用 analyze_jobs 执行深度 JD 分析
3. 分析过程中, 如果发现某个 JD 太短, 先用 fetch_job_detail 补全
4. 再次调用 get_db_status 确认分析结果
5. 汇报分析结果 (分析数 + 高分职位数)

分析质量要点:
- match_score > 0.7: 高度匹配, 应优先推荐
- match_score 0.4-0.7: 部分匹配, 可考虑
- match_score < 0.4: 匹配度低, 可跳过

反思要点:
- 如果所有职位分数都很高 (>0.8), 可能评估标准太松
- 如果所有职位分数都很低 (<0.3), 可能源头采集就有问题
- JD 太短 (<100字) 的分析结果不可靠, 需要先补全

任务完成后停止。"""

    @classmethod
    def create(cls, llm_client, model, memory=None) -> AnalystAgent:
        """工厂方法"""
        return cls(
            name="AnalystAgent",
            llm_client=llm_client,
            model=model,
            tools=ANALYST_TOOLS,
            memory=memory,
            max_iterations=8,
            reflection_interval=3,
            temperature=0.3,
        )
