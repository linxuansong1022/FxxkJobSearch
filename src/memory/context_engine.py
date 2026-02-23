"""
上下文工程 — 对应 hello-agents 第九章

核心功能:
1. 上下文压缩 (Context Compression): 减少 token 消耗
2. 动态裁剪 (Dynamic Pruning): 移除不相关信息
3. 重要性排序 (Priority Ranking): 保留最重要的上下文

为 Agent 构建最优的上下文窗口, 确保:
- 不超过模型的上下文限制
- 保留最相关的信息
- 按优先级排序
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ContextBlock:
    """上下文块 — 单个信息片段"""
    content: str
    priority: int  # 1=最高, 数字越大越低
    category: str  # "system", "memory", "task", "tool_result"
    token_estimate: int = 0

    def __post_init__(self):
        if not self.token_estimate:
            self.token_estimate = len(self.content) // 3


class ContextEngine:
    """
    上下文工程引擎

    为 Agent 构建最优上下文，确保:
    1. 总 token 不超过限制
    2. 高优先级信息优先保留
    3. 自动压缩冗长的 tool 结果
    """

    def __init__(self, max_tokens: int = 8000):
        self.max_tokens = max_tokens

    def build_context(
        self,
        system_info: str = "",
        task: str = "",
        memory_items: list[str] = None,
        tool_results: list[dict] = None,
    ) -> str:
        """
        构建优化后的上下文

        Priority:
        1. System info (角色定义)
        2. Task description (当前任务)
        3. Memory items (相关历史)
        4. Tool results (工具结果, 按时间逆序)
        """
        blocks: list[ContextBlock] = []

        # System info (最高优先级)
        if system_info:
            blocks.append(
                ContextBlock(
                    content=system_info,
                    priority=1,
                    category="system",
                )
            )

        # Task
        if task:
            blocks.append(
                ContextBlock(
                    content=f"[当前任务] {task}",
                    priority=2,
                    category="task",
                )
            )

        # Memory
        for i, mem in enumerate(memory_items or []):
            blocks.append(
                ContextBlock(
                    content=f"[历史经验 {i + 1}] {mem}",
                    priority=3,
                    category="memory",
                )
            )

        # Tool results (最低优先级, 但最新的更重要)
        for i, result in enumerate(reversed(tool_results or [])):
            result_str = json.dumps(result, ensure_ascii=False)
            # 压缩过长的 tool 结果
            if len(result_str) > 500:
                result_str = self._compress_tool_result(result_str)
            blocks.append(
                ContextBlock(
                    content=f"[工具结果 {i + 1}] {result_str}",
                    priority=4 + i,  # 越旧优先级越低
                    category="tool_result",
                )
            )

        # 按优先级排序, 逐步裁剪
        blocks.sort(key=lambda b: b.priority)
        selected = self._select_within_budget(blocks)

        return "\n\n".join(b.content for b in selected)

    def _select_within_budget(
        self, blocks: list[ContextBlock]
    ) -> list[ContextBlock]:
        """在 token 预算内选择最大化信息量的上下文块"""
        selected = []
        total_tokens = 0

        for block in blocks:
            if total_tokens + block.token_estimate <= self.max_tokens:
                selected.append(block)
                total_tokens += block.token_estimate
            else:
                # 尝试压缩后加入
                compressed = self._compress_block(
                    block, self.max_tokens - total_tokens
                )
                if compressed:
                    selected.append(compressed)
                    total_tokens += compressed.token_estimate
                break  # 预算用完

        return selected

    def _compress_block(
        self, block: ContextBlock, remaining_tokens: int
    ) -> Optional[ContextBlock]:
        """压缩单个上下文块以适应剩余 token"""
        if remaining_tokens < 50:
            return None

        max_chars = remaining_tokens * 3  # 粗略换算
        if len(block.content) <= max_chars:
            return block

        compressed_content = block.content[:max_chars] + "...[truncated]"
        return ContextBlock(
            content=compressed_content,
            priority=block.priority,
            category=block.category,
            token_estimate=remaining_tokens,
        )

    @staticmethod
    def _compress_tool_result(result_str: str) -> str:
        """压缩工具结果 — 保留关键信息, 移除冗余"""
        try:
            data = json.loads(result_str)
            # 只保留 status + 关键数值
            compressed = {}
            for key in ["status", "new_jobs", "analyzed", "relevant",
                        "irrelevant", "pending_filter", "pending_analyze",
                        "message"]:
                if key in data:
                    compressed[key] = data[key]
            if compressed:
                return json.dumps(compressed, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            pass
        # Fallback: 截断
        return result_str[:500] + "..."
