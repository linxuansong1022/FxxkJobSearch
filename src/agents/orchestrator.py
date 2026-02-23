"""
OrchestratorAgent — 多 Agent 编排器

基于 Plan-and-Solve 范式:
1. Plan: 根据数据库状态制定执行计划
2. Solve: 按计划依次分派任务给子 Agent
3. Adjust: 根据子 Agent 结果动态调整后续计划

对应 hello-agents 第四章 Plan-and-Solve 范式
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import types

from src.agents.base_agent import (
    BaseAgent,
    AgentResult,
    TrajectoryStep,
    StepType,
)
from src.agents.scout_agent import ScoutAgent
from src.agents.filter_agent import FilterAgent
from src.agents.analyst_agent import AnalystAgent
from src.agents.notifier_agent import NotifierAgent

logger = logging.getLogger(__name__)


@dataclass
class PlanStep:
    """执行计划的单步"""
    agent_name: str
    task: str
    priority: int = 0
    completed: bool = False
    result: str = ""


@dataclass
class ExecutionPlan:
    """完整执行计划"""
    goal: str
    steps: list[PlanStep] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)


class OrchestratorAgent:
    """
    编排器 Agent — Plan-and-Solve 范式

    不继承 BaseAgent, 因为 Orchestrator 的循环逻辑不同:
    它不通过 Gemini function calling 调用 Tools,
    而是直接调度子 Agent。
    """

    def __init__(
        self,
        llm_client: genai.Client,
        model: str,
        sub_agents: dict[str, BaseAgent],
        memory=None,
    ):
        self.llm = llm_client
        self.model = model
        self.sub_agents = sub_agents
        self.memory = memory
        self._trajectory: list[TrajectoryStep] = []

    @classmethod
    def create(cls, llm_client, model, memory=None) -> OrchestratorAgent:
        """
        工厂方法: 创建 Orchestrator 及其所有子 Agent

        Returns:
            完整的多 Agent 系统, 以 Orchestrator 为入口
        """
        sub_agents = {
            "scout": ScoutAgent.create(llm_client, model, memory),
            "filter": FilterAgent.create(llm_client, model, memory),
            "analyst": AnalystAgent.create(llm_client, model, memory),
            "notifier": NotifierAgent.create(llm_client, model, memory),
        }
        return cls(
            llm_client=llm_client,
            model=model,
            sub_agents=sub_agents,
            memory=memory,
        )

    async def run(self, task: str = "", db=None) -> AgentResult:
        """
        运行多 Agent 系统 (Async)

        1. Plan: 让 LLM 制定执行计划
        2. Execute: 逐步执行计划, 每步分派给对应子 Agent
        3. Reflect: 每步执行后评估是否需要调整计划
        """
        start_time = time.time()
        self._trajectory = []

        if not task:
            task = "请开始今日的求职自动化流程。首先检查数据库状态，然后根据情况决定需要执行哪些操作。"

        logger.info(f"[Orchestrator] 启动, 目标: {task}")

        # ── Phase 1: Plan ──
        plan = await self._create_plan(task)
        self._trajectory.append(
            TrajectoryStep(
                step_type=StepType.THINK,
                content=f"制定执行计划: {len(plan.steps)} 步",
            )
        )

        logger.info(f"[Orchestrator] 计划步骤: {len(plan.steps)}")
        for i, step in enumerate(plan.steps):
            logger.info(f"  {i + 1}. [{step.agent_name}] {step.task}")

        # ── Phase 2: Solve ──
        for i, step in enumerate(plan.steps):
            agent = self.sub_agents.get(step.agent_name)
            if not agent:
                logger.warning(f"未知 Agent: {step.agent_name}, 跳过")
                step.result = "Skipped: unknown agent"
                continue

            logger.info(
                f"[Orchestrator] 执行步骤 {i + 1}/{len(plan.steps)}: "
                f"[{step.agent_name}] {step.task}"
            )

            # 分派给子 Agent
            self._trajectory.append(
                TrajectoryStep(
                    step_type=StepType.ACT,
                    content=f"分派给 {step.agent_name}: {step.task}",
                    tool_name=step.agent_name,
                )
            )

            try:
                result = await agent.run(task=step.task, db=db)
                step.completed = True
                step.result = result.summary

                self._trajectory.append(
                    TrajectoryStep(
                        step_type=StepType.OBSERVE,
                        content=f"{step.agent_name} 完成: {result.summary[:300]}",
                        tool_name=step.agent_name,
                        tool_result=result.metrics,
                    )
                )

                # 合并子 Agent trajectory
                self._trajectory.extend(result.trajectory)

                logger.info(
                    f"[Orchestrator] {step.agent_name} 完成: "
                    f"{result.metrics}"
                )

            except Exception as e:
                logger.error(f"[Orchestrator] {step.agent_name} 失败: {e}")
                step.result = f"Failed: {e}"
                self._trajectory.append(
                    TrajectoryStep(
                        step_type=StepType.OBSERVE,
                        content=f"{step.agent_name} 失败: {e}",
                        tool_name=step.agent_name,
                    )
                )
                # 不中断, 继续执行后续步骤

            # ── Reflect after each step ──
            if i < len(plan.steps) - 1:
                adjusted_plan = await self._maybe_adjust_plan(plan, i)
                if adjusted_plan:
                    plan = adjusted_plan
                    logger.info(
                        f"[Orchestrator] 计划已调整, 剩余 "
                        f"{len([s for s in plan.steps if not s.completed])} 步"
                    )

        # ── Final Summary ──
        duration = time.time() - start_time
        summary = self._generate_summary(plan)

        # Store in memory
        if self.memory:
            self.memory.commit_run(
                agent_name="Orchestrator",
                task=task,
                summary=summary,
                trajectory=self._trajectory,
            )

        return AgentResult(
            success=all(s.completed for s in plan.steps),
            summary=summary,
            trajectory=self._trajectory,
            metrics={
                "total_steps": len(plan.steps),
                "completed_steps": sum(1 for s in plan.steps if s.completed),
                "duration_seconds": round(duration, 2),
                "sub_agent_runs": {
                    s.agent_name: s.completed for s in plan.steps
                },
            },
        )

    async def _create_plan(self, task: str) -> ExecutionPlan:
        """
        Phase 1: Plan — 使用 LLM 制定执行计划 (Async)

        让 Gemini 根据任务目标生成执行步骤。
        """
        prompt = f"""你是一个求职自动化系统的编排器。请根据任务目标制定执行计划。

可用的子 Agent:
1. scout — 数据采集 Agent, 从 LinkedIn/TheHub/Jobindex 等平台抓取职位
2. filter — 智能筛选 Agent, 对新职位做相关性过滤 (Rule + LLM)
3. analyst — 深度分析 Agent, 用 Gemini 分析 JD 并评估匹配度
4. notifier — 通知 Agent, 发送 Telegram 日报

任务目标: {task}

请以 JSON 数组格式输出执行计划, 每一步包含:
- agent_name: 使用哪个子 Agent (scout/filter/analyst/notifier)
- task: 给子 Agent 的任务描述
- priority: 优先级 (1=最高)

示例:
[
    {{"agent_name": "scout", "task": "从 The Hub 和 Jobindex 抓取最新职位", "priority": 1}},
    {{"agent_name": "filter", "task": "对所有未评分的新职位进行过滤", "priority": 2}}
]

只返回 JSON 数组, 不要其他文字。"""

        try:
            response = await self.llm.aio.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    response_mime_type="application/json",
                ),
            )
            steps_data = json.loads(response.text)
            if not isinstance(steps_data, list):
                steps_data = [steps_data]

            steps = [
                PlanStep(
                    agent_name=s.get("agent_name", ""),
                    task=s.get("task", ""),
                    priority=s.get("priority", 99),
                )
                for s in steps_data
                if s.get("agent_name") in self.sub_agents
            ]

            # 按优先级排序
            steps.sort(key=lambda s: s.priority)

            return ExecutionPlan(goal=task, steps=steps)

        except Exception as e:
            logger.warning(f"[Orchestrator] LLM 计划生成失败: {e}, 使用默认计划")
            return self._default_plan(task)

    def _default_plan(self, task: str) -> ExecutionPlan:
        """默认执行计划 (当 LLM 计划生成失败时使用)"""
        return ExecutionPlan(
            goal=task,
            steps=[
                PlanStep(
                    agent_name="scout",
                    task="从所有可用平台抓取最新职位",
                    priority=1,
                ),
                PlanStep(
                    agent_name="filter",
                    task="对所有未评分的职位进行智能过滤",
                    priority=2,
                ),
                PlanStep(
                    agent_name="analyst",
                    task="深度分析所有相关职位的 JD",
                    priority=3,
                ),
                PlanStep(
                    agent_name="notifier",
                    task="如果有高匹配度职位, 发送 Telegram 通知",
                    priority=4,
                ),
            ],
        )

    async def _maybe_adjust_plan(
        self, plan: ExecutionPlan, completed_index: int
    ) -> ExecutionPlan | None:
        """
        Phase 3: Adjust — 根据执行结果动态调整后续计划

        例如: 如果 ScoutAgent 没有抓到新职位, 则跳过 FilterAgent。
        """
        completed_step = plan.steps[completed_index]
        remaining = plan.steps[completed_index + 1:]

        # 简单规则调整 (不调用 LLM, 节省 token)
        if completed_step.agent_name == "scout" and "new_jobs: 0" in completed_step.result.lower():
            # 没有新职位, 但仍然可能有待处理的旧职位, 所以不跳过 filter
            logger.info("[Orchestrator] Scout 未抓到新职位, 但继续执行后续步骤")

        return None  # 暂不调整

    def _generate_summary(self, plan: ExecutionPlan) -> str:
        """生成执行摘要"""
        lines = [f"执行计划完成: {plan.goal}"]
        for i, step in enumerate(plan.steps):
            status = "✅" if step.completed else "❌"
            lines.append(f"  {status} [{step.agent_name}] {step.result[:100]}")
        return "\n".join(lines)
