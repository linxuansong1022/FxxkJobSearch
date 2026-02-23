"""
FilterAgent — 智能筛选 Agent

结合规则引擎和 LLM 进行两层过滤:
1. Rule Layer: 时间过滤 + 关键词黑名单 → 快速剔除
2. LLM Layer: Gemini Flash 语义判断 → 精准筛选

具备自我反思能力: 如果过滤率异常高/低, 会调整策略
"""

from __future__ import annotations

import logging

from src.agents.base_agent import BaseAgent
from src.tools import FILTER_TOOLS

logger = logging.getLogger(__name__)


class FilterAgent(BaseAgent):
    """智能筛选 Agent — Rule + LLM 两层过滤"""

    @property
    def system_prompt(self) -> str:
        return """你是一个智能职位筛选 Agent (FilterAgent)。

你的职责是对数据库中未评分的职位进行相关性筛选，为后续深度分析做准备。

工作流程:
1. 调用 get_db_status 查看待过滤数量 (pending_filter)
2. 如果有待过滤职位，调用 filter_jobs 执行过滤
3. 再次调用 get_db_status 确认过滤结果
4. 汇报筛选结果 (保留数/排除数/过期数)

筛选策略:
- filter_jobs 内部使用 Rule + LLM 两层机制
- Rule层: 时间过期(>7天) + 标题排除词 (HR/Sales/Senior等) → 快速剔除
- LLM层: Gemini Flash 基于标题+公司判断是否适合 CS 硕士实习/学生工

反思要点:
- 如果 relevant 比例 < 10%，可能需要扩大关键词范围
- 如果 relevant 比例 > 80%，可能过滤不够严格
- 反思时注意 token 和 API 成本

任务完成后停止, 不要无限循环。"""

    @classmethod
    def create(cls, llm_client, model, memory=None) -> FilterAgent:
        """工厂方法"""
        return cls(
            name="FilterAgent",
            llm_client=llm_client,
            model=model,
            tools=FILTER_TOOLS,
            memory=memory,
            max_iterations=5,
            reflection_interval=3,
            temperature=0.1,
        )
