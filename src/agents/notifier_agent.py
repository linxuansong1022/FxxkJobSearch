"""
NotifierAgent — 智能通知推送 Agent

决定何时发送通知 + 通知内容的质量把控:
- 只有当存在高匹配度 (>0.6) 职位时才发送
- 自动汇总当日分析结果
- 避免重复发送已通知的职位
"""

from __future__ import annotations

import logging

from src.agents.base_agent import BaseAgent
from src.tools import NOTIFIER_TOOLS

logger = logging.getLogger(__name__)


class NotifierAgent(BaseAgent):
    """智能通知 Agent — 决定通知时机和内容"""

    @property
    def system_prompt(self) -> str:
        return """你是一个智能通知 Agent (NotifierAgent)。

你的职责是决定是否需要发送 Telegram 通知, 以及控制通知质量。

工作流程:
1. 调用 get_db_status 查看当前状态
2. 判断是否有值得通知的内容:
   - status=analyzed 的职位中是否有 match_score > 0.6 的?
   - 今天是否已经发过通知了?
3. 如果值得发送, 调用 send_notification
4. 汇报发送结果

决策原则:
- 只有当有 match_score > 0.6 的职位时才发送
- 每天最多发送 1 次通知 (避免骚扰)
- 如果没有高质量职位, 回复"今日无需通知"即可
- 如果 send_notification 失败, 记录错误但不重试

完成后立即停止。"""

    @classmethod
    def create(cls, llm_client, model, memory=None) -> NotifierAgent:
        """工厂方法"""
        return cls(
            name="NotifierAgent",
            llm_client=llm_client,
            model=model,
            tools=NOTIFIER_TOOLS,
            memory=memory,
            max_iterations=4,
            reflection_interval=5,  # 基本不需要反思
            temperature=0.1,
        )
