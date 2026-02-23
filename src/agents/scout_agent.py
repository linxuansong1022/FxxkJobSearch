"""
ScoutAgent — 数据采集专职 Agent

负责从多个平台采集职位数据:
- LinkedIn / Indeed (via JobSpy)
- The Hub (Denmark startups)
- Jobindex (Denmark local)
- Company Career Pages (Playwright)

决策逻辑: 根据数据库状态动态选择抓取平台和策略
"""

from __future__ import annotations

import logging

from src.agents.base_agent import BaseAgent
from src.tools import SCOUT_TOOLS, GET_DB_STATUS

logger = logging.getLogger(__name__)


class ScoutAgent(BaseAgent):
    """数据采集 Agent — 自主决定抓取策略"""

    @property
    def system_prompt(self) -> str:
        return """你是一个专业的职位数据采集 Agent (ScoutAgent)。

你的职责是从多个平台采集求职数据，确保数据库中有足够的新鲜职位。

可用平台 (按稳定性排序):
1. scrape_thehub — The Hub 丹麦创业公司职位，API 稳定，优先使用
2. scrape_jobindex — Jobindex 丹麦本地职位，覆盖面广
3. scrape_linkedin — LinkedIn + Indeed，数据质量高但可能被限流
4. scrape_company_careers — 公司官网 Career 页面，需要指定公司

工作流程:
1. 先调用 get_db_status (如果 Orchestrator 没提供) 了解当前数据量
2. 选择 1-2 个最合适的平台进行抓取
3. 如果某个平台返回 0 条新职位，尝试其他平台
4. 汇报抓取结果

决策原则:
- 如果数据库中新职位(status=new)不足 10 条，多抓几个平台
- LinkedIn 每次只抓一次，避免触发反爬
- 每次运行不超过 3 个平台
- 出错不要停止，记录后继续"""

    @classmethod
    def create(cls, llm_client, model, memory=None) -> ScoutAgent:
        """工厂方法: 创建 ScoutAgent 实例"""
        tools = SCOUT_TOOLS + [GET_DB_STATUS]
        return cls(
            name="ScoutAgent",
            llm_client=llm_client,
            model=model,
            tools=tools,
            memory=memory,
            max_iterations=8,
            reflection_interval=4,
            temperature=0.2,
        )
