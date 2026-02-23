"""
Agent 性能评估系统 — 对应 hello-agents 第十二章

评估维度:
1. Tool Calling 准确率 (BFCL 风格)
   - 是否选择了正确的 Tool?
   - 参数是否正确?

2. 任务完成率 (GAIA 风格)
   - 是否完成了预期的所有步骤?
   - 最终结果是否符合预期?

3. 效率指标
   - Token 消耗
   - 迭代次数
   - 运行时间

4. 决策质量 (LLM-as-Judge)
   - 用另一个 LLM 评估 Agent 的决策链
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Metrics Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class ToolCallMetrics:
    """Tool Calling 评估指标"""
    total_calls: int = 0
    correct_calls: int = 0
    incorrect_calls: int = 0
    accuracy: float = 0.0
    details: list[dict] = field(default_factory=list)


@dataclass
class TaskMetrics:
    """任务完成评估指标"""
    total_steps: int = 0
    completed_steps: int = 0
    completion_rate: float = 0.0
    expected_tools: list[str] = field(default_factory=list)
    actual_tools: list[str] = field(default_factory=list)


@dataclass
class EfficiencyMetrics:
    """效率评估指标"""
    total_iterations: int = 0
    total_tool_calls: int = 0
    duration_seconds: float = 0.0
    redundant_calls: int = 0  # 重复/无效的 Tool 调用


@dataclass
class JudgeMetrics:
    """LLM Judge 评估指标"""
    overall_score: float = 0.0  # 0-10
    reasoning_quality: float = 0.0
    tool_selection_quality: float = 0.0
    task_completion_quality: float = 0.0
    feedback: str = ""


@dataclass
class EvaluationReport:
    """完整评估报告"""
    agent_name: str
    task: str
    tool_call_metrics: ToolCallMetrics = field(default_factory=ToolCallMetrics)
    task_metrics: TaskMetrics = field(default_factory=TaskMetrics)
    efficiency_metrics: EfficiencyMetrics = field(default_factory=EfficiencyMetrics)
    judge_metrics: JudgeMetrics = field(default_factory=JudgeMetrics)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "agent_name": self.agent_name,
            "task": self.task,
            "tool_calling": {
                "accuracy": self.tool_call_metrics.accuracy,
                "total": self.tool_call_metrics.total_calls,
                "correct": self.tool_call_metrics.correct_calls,
            },
            "task_completion": {
                "rate": self.task_metrics.completion_rate,
                "completed": self.task_metrics.completed_steps,
                "total": self.task_metrics.total_steps,
            },
            "efficiency": {
                "iterations": self.efficiency_metrics.total_iterations,
                "tool_calls": self.efficiency_metrics.total_tool_calls,
                "duration_s": self.efficiency_metrics.duration_seconds,
                "redundant": self.efficiency_metrics.redundant_calls,
            },
            "judge_score": self.judge_metrics.overall_score,
        }

    def print_report(self):
        """打印评估报告"""
        print(f"\n{'='*60}")
        print(f"  Agent 性能评估报告: {self.agent_name}")
        print(f"{'='*60}")
        print(f"  任务: {self.task}")
        print(f"\n  📊 Tool Calling 准确率: {self.tool_call_metrics.accuracy:.1%}")
        print(f"     总调用: {self.tool_call_metrics.total_calls}")
        print(f"     正确:   {self.tool_call_metrics.correct_calls}")
        print(f"\n  ✅ 任务完成率: {self.task_metrics.completion_rate:.1%}")
        print(f"     完成步骤: {self.task_metrics.completed_steps}/{self.task_metrics.total_steps}")
        print(f"\n  ⚡ 效率指标:")
        print(f"     迭代次数: {self.efficiency_metrics.total_iterations}")
        print(f"     Tool调用: {self.efficiency_metrics.total_tool_calls}")
        print(f"     运行时间: {self.efficiency_metrics.duration_seconds:.1f}s")
        print(f"     冗余调用: {self.efficiency_metrics.redundant_calls}")
        if self.judge_metrics.overall_score > 0:
            print(f"\n  🧑‍⚖️ LLM Judge 评分: {self.judge_metrics.overall_score:.1f}/10")
            print(f"     {self.judge_metrics.feedback}")
        print(f"{'='*60}\n")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test Case Definitions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


@dataclass
class TestCase:
    """评估测试用例"""
    name: str
    task: str
    expected_tool_sequence: list[str]  # 期望的 Tool 调用顺序
    expected_final_state: dict = field(default_factory=dict)
    difficulty: str = "easy"  # easy / medium / hard


# 预定义测试用例
DEFAULT_TEST_CASES = [
    TestCase(
        name="full_pipeline",
        task="执行完整的每日求职流程",
        expected_tool_sequence=["get_db_status", "scrape_thehub", "filter_jobs", "analyze_jobs", "send_notification"],
        difficulty="medium",
    ),
    TestCase(
        name="scout_only",
        task="仅从 The Hub 抓取最新职位",
        expected_tool_sequence=["scrape_thehub"],
        difficulty="easy",
    ),
    TestCase(
        name="filter_and_analyze",
        task="过滤未评分的职位，然后分析相关职位",
        expected_tool_sequence=["get_db_status", "filter_jobs", "analyze_jobs"],
        difficulty="easy",
    ),
    TestCase(
        name="conditional_notify",
        task="检查是否有高分职位，如果有则发送通知",
        expected_tool_sequence=["get_db_status"],
        difficulty="medium",
    ),
]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Evaluator
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class AgentEvaluator:
    """
    Agent 性能评估器

    使用方式:
    1. evaluator = AgentEvaluator(llm_client)
    2. report = evaluator.evaluate(agent_result, test_case)
    3. report.print_report()
    """

    def __init__(self, llm_client=None, model: str = ""):
        self.llm = llm_client
        self.model = model

    def evaluate(
        self,
        result: Any,  # AgentResult
        test_case: TestCase = None,
    ) -> EvaluationReport:
        """完整评估"""
        report = EvaluationReport(
            agent_name=getattr(result, "summary", "")[:50],
            task=test_case.task if test_case else "Unknown",
        )

        # 1. Tool Calling 评估
        if test_case:
            report.tool_call_metrics = self._evaluate_tool_calling(
                result.trajectory, test_case
            )

        # 2. 任务完成评估
        if test_case:
            report.task_metrics = self._evaluate_task_completion(
                result, test_case
            )

        # 3. 效率评估
        report.efficiency_metrics = self._evaluate_efficiency(result)

        # 4. LLM Judge (可选)
        if self.llm and self.model:
            report.judge_metrics = self._evaluate_with_llm_judge(result)

        return report

    def _evaluate_tool_calling(
        self, trajectory: list, test_case: TestCase
    ) -> ToolCallMetrics:
        """
        评估 Tool Calling 准确率 — BFCL 风格

        检查 Agent 是否调用了预期的 Tool。
        """
        from src.agents.base_agent import StepType
        actual_tools = [
            t.tool_name
            for t in trajectory
            if t.step_type == StepType.ACT and t.tool_name
        ]

        expected = set(test_case.expected_tool_sequence)
        actual = set(actual_tools)

        correct = len(expected & actual)
        total = max(len(expected), 1)

        return ToolCallMetrics(
            total_calls=len(actual_tools),
            correct_calls=correct,
            incorrect_calls=len(actual - expected),
            accuracy=correct / total,
            details=[
                {
                    "expected": list(expected),
                    "actual": actual_tools,
                    "missing": list(expected - actual),
                    "extra": list(actual - expected),
                }
            ],
        )

    def _evaluate_task_completion(
        self, result: Any, test_case: TestCase
    ) -> TaskMetrics:
        """评估任务完成率"""
        from src.agents.base_agent import StepType
        actual_tools = [
            t.tool_name
            for t in result.trajectory
            if t.step_type == StepType.ACT and t.tool_name
        ]

        expected_set = set(test_case.expected_tool_sequence)
        completed = sum(1 for t in expected_set if t in actual_tools)

        return TaskMetrics(
            total_steps=len(expected_set),
            completed_steps=completed,
            completion_rate=completed / max(len(expected_set), 1),
            expected_tools=test_case.expected_tool_sequence,
            actual_tools=actual_tools,
        )

    def _evaluate_efficiency(self, result: Any) -> EfficiencyMetrics:
        """评估执行效率"""
        metrics = result.metrics or {}

        # 检测冗余调用 (同一个 Tool 被连续调用两次)
        from src.agents.base_agent import StepType
        tool_sequence = [
            t.tool_name
            for t in result.trajectory
            if t.step_type == StepType.ACT and t.tool_name
        ]
        redundant = sum(
            1
            for i in range(1, len(tool_sequence))
            if tool_sequence[i] == tool_sequence[i - 1]
        )

        return EfficiencyMetrics(
            total_iterations=metrics.get("iterations", 0),
            total_tool_calls=metrics.get("tool_calls", len(tool_sequence)),
            duration_seconds=metrics.get("duration_seconds", 0),
            redundant_calls=redundant,
        )

    def _evaluate_with_llm_judge(self, result: Any) -> JudgeMetrics:
        """
        LLM-as-Judge 评估 — 用 Gemini 评估 Agent 决策质量

        对应 hello-agents 第十二章的 LLM Judge 评估
        """
        from src.agents.base_agent import StepType

        trajectory_text = "\n".join(
            f"[{t.step_type.value}] {t.content}"
            for t in result.trajectory[:20]
        )

        prompt = f"""请评估以下 Agent 的执行质量，给出 0-10 分的评分。

Agent 执行轨迹:
{trajectory_text}

最终结果: {result.summary[:500]}

请从以下维度评估:
1. reasoning_quality (推理质量): Agent 的思考过程是否合理?
2. tool_selection_quality (工具选择): 是否选择了最合适的工具?
3. task_completion_quality (任务完成): 是否完成了任务目标?

请以 JSON 格式返回:
{{
    "overall_score": 7.5,
    "reasoning_quality": 8.0,
    "tool_selection_quality": 7.0,
    "task_completion_quality": 8.0,
    "feedback": "简短评价"
}}"""

        try:
            from google.genai import types
            response = self.llm.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text)
            return JudgeMetrics(
                overall_score=data.get("overall_score", 0),
                reasoning_quality=data.get("reasoning_quality", 0),
                tool_selection_quality=data.get("tool_selection_quality", 0),
                task_completion_quality=data.get("task_completion_quality", 0),
                feedback=data.get("feedback", ""),
            )
        except Exception as e:
            logger.warning(f"LLM Judge 评估失败: {e}")
            return JudgeMetrics(feedback=f"Evaluation failed: {e}")


def run_evaluation(llm_client=None, model: str = ""):
    """运行预定义的评估测试"""
    evaluator = AgentEvaluator(llm_client, model)

    print("\n🧪 Agent 评估系统")
    print(f"  预定义测试用例: {len(DEFAULT_TEST_CASES)} 个")
    print("  注意: 完整评估需要运行 Agent (会调用实际 API)")
    print("  使用 pytest tests/test_evaluation.py 运行单元测试\n")

    # 打印测试用例列表
    for i, tc in enumerate(DEFAULT_TEST_CASES):
        print(f"  {i + 1}. [{tc.difficulty}] {tc.name}: {tc.task}")
        print(f"     期望 Tools: {' → '.join(tc.expected_tool_sequence)}")
    print()
