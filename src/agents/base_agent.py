"""
Agent 基类 — ReAct + Reflection 范式实现

对应 hello-agents:
- 第四章: 经典范式 (ReAct / Plan-and-Solve / Reflection)
- 第七章: 构建你的 Agent 框架

ReAct Loop: Think → Act → Observe → (Reflect)
Agent 在每一步中：
1. Think: 基于当前上下文推理下一步行动
2. Act:  选择并执行一个 Tool
3. Observe: 收集执行结果
4. Reflect: 周期性自我评估 (每 N 步)
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class StepType(str, Enum):
    THINK = "think"
    ACT = "act"
    OBSERVE = "observe"
    REFLECT = "reflect"


@dataclass
class ToolSpec:
    """Tool 规范 — 与 MCP 兼容的 Tool 描述"""
    name: str
    description: str
    parameters: dict  # JSON Schema
    handler: Any = None  # callable(db, **kwargs) -> dict

    def to_function_declaration(self) -> types.FunctionDeclaration:
        """转换为 Gemini Function Declaration"""
        properties = {}
        required = []
        for param_name, param_info in self.parameters.get("properties", {}).items():
            prop_type = param_info.get("type", "string").upper()
            type_map = {
                "STRING": types.Type.STRING,
                "INTEGER": types.Type.INTEGER,
                "NUMBER": types.Type.NUMBER,
                "BOOLEAN": types.Type.BOOLEAN,
                "ARRAY": types.Type.ARRAY,
                "OBJECT": types.Type.OBJECT,
            }
            schema_kwargs = {
                "type": type_map.get(prop_type, types.Type.STRING),
                "description": param_info.get("description", ""),
            }
            # Handle array items
            if prop_type == "ARRAY" and "items" in param_info:
                item_type = param_info["items"].get("type", "string").upper()
                schema_kwargs["items"] = types.Schema(
                    type=type_map.get(item_type, types.Type.STRING)
                )
            properties[param_name] = types.Schema(**schema_kwargs)

        for param_name in self.parameters.get("required", []):
            required.append(param_name)

        return types.FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties=properties,
                required=required if required else None,
            ),
        )


@dataclass
class AgentAction:
    """Agent 的动作 — 对应一次 Tool 调用"""
    tool_name: str
    tool_args: dict = field(default_factory=dict)
    raw_thought: str = ""  # LLM 的思考过程


@dataclass
class AgentObservation:
    """Tool 执行后的观察结果"""
    tool_name: str
    result: dict = field(default_factory=dict)
    is_terminal: bool = False  # 是否终止循环
    error: Optional[str] = None


@dataclass
class TrajectoryStep:
    """Agent 轨迹的单步记录"""
    step_type: StepType
    content: str
    tool_name: Optional[str] = None
    tool_args: Optional[dict] = None
    tool_result: Optional[dict] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ReflectionResult:
    """反思结果"""
    assessment: str  # 当前执行质量评估
    should_adjust: bool = False  # 是否需要调整策略
    adjusted_strategy: str = ""  # 调整后的策略
    confidence: float = 0.8  # 自信度 (0-1)


@dataclass
class AgentResult:
    """Agent 运行的最终结果"""
    success: bool
    summary: str
    trajectory: list[TrajectoryStep] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)  # token_count, duration, etc.


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Base Agent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class BaseAgent(ABC):
    """
    Agent 基类 — 实现 ReAct + Reflection 范式

    子类需要实现:
    - system_prompt:  Agent 的系统提示词
    - tools:          Agent 可用的工具列表

    可选覆写:
    - _should_terminate(): 自定义终止条件
    - _on_reflection():    自定义反思逻辑
    """

    def __init__(
        self,
        name: str,
        llm_client: genai.Client,
        model: str,
        tools: list[ToolSpec],
        memory=None,
        max_iterations: int = 10,
        reflection_interval: int = 3,
        temperature: float = 0.3,
    ):
        self.name = name
        self.llm = llm_client
        self.model = model
        self.tool_registry: dict[str, ToolSpec] = {t.name: t for t in tools}
        self.memory = memory
        self.max_iterations = max_iterations
        self.reflection_interval = reflection_interval
        self.temperature = temperature

        # Runtime state
        self._trajectory: list[TrajectoryStep] = []
        self._messages: list[types.Content] = []
        self._start_time: float = 0

    # ── Abstract / Override Points ──────────────────────────

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """Agent 的 System Prompt — 定义角色和行为"""
        ...

    # ── Public API ──────────────────────────────────────────

    async def run(self, task: str, db=None) -> AgentResult:
        """
        Agent 主循环 — ReAct Pattern (Async)

        Think → Act → Observe → (Reflect) → Think → ...

        Args:
            task: 任务描述
            db:   数据库实例 (注入到 Tool 调用中)

        Returns:
            AgentResult: 包含执行轨迹和指标
        """
        self._start_time = time.time()
        self._trajectory = []
        self._messages = []

        logger.info(f"[{self.name}] 启动, 任务: {task}")

        # 注入记忆上下文 (如果有)
        memory_context = ""
        if self.memory:
            relevant_memories = self.memory.recall(task, k=3)
            if relevant_memories:
                memory_context = "\n\n[相关历史经验]\n" + "\n".join(
                    f"- {m}" for m in relevant_memories
                )

        # 构建初始消息
        self._messages.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=task + memory_context)],
            )
        )

        # ── ReAct Loop ──
        final_text = ""
        for iteration in range(1, self.max_iterations + 1):
            logger.info(f"[{self.name}] 迭代 #{iteration}")

            # ─ Think + Act (Gemini 一步生成) ─
            response = await self._call_llm()

            # 记录 Think 步骤
            self._trajectory.append(
                TrajectoryStep(
                    step_type=StepType.THINK,
                    content=f"Iteration {iteration}",
                )
            )

            # 追加 model 响应到消息历史
            self._messages.append(
                types.Content(
                    role="model",
                    parts=response.candidates[0].content.parts,
                )
            )

            # 提取 function calls
            tool_calls = [
                part
                for part in response.candidates[0].content.parts
                if hasattr(part, "function_call") and part.function_call
            ]

            if not tool_calls:
                # 没有 tool call → Agent 认为任务完成
                final_text = response.text or ""
                logger.info(f"[{self.name}] 完成: {final_text[:200]}")
                break

            # ─ Act + Observe: 执行所有 tool calls ─
            tool_results_parts = []
            for part in tool_calls:
                fc = part.function_call
                tool_name = fc.name
                tool_args = dict(fc.args) if fc.args else {}

                # 记录 Act 步骤
                self._trajectory.append(
                    TrajectoryStep(
                        step_type=StepType.ACT,
                        content=f"调用 {tool_name}",
                        tool_name=tool_name,
                        tool_args=tool_args,
                    )
                )

                logger.info(f"[{self.name}]   Tool: {tool_name}({tool_args})")

                # 执行 Tool
                observation = await self._execute_tool(tool_name, tool_args, db)

                # 记录 Observe 步骤
                self._trajectory.append(
                    TrajectoryStep(
                        step_type=StepType.OBSERVE,
                        content=f"结果: {json.dumps(observation.result, ensure_ascii=False)[:500]}",
                        tool_name=tool_name,
                        tool_result=observation.result,
                    )
                )

                logger.info(f"[{self.name}]   结果: {observation.result}")

                tool_results_parts.append(
                    types.Part.from_function_response(
                        name=tool_name,
                        response=observation.result,
                    )
                )

            # 追加 Tool 结果到消息历史
            self._messages.append(
                types.Content(role="user", parts=tool_results_parts)
            )

            # ─ Reflect (周期性) ─
            if iteration % self.reflection_interval == 0:
                reflection = await self._reflect()
                self._trajectory.append(
                    TrajectoryStep(
                        step_type=StepType.REFLECT,
                        content=reflection.assessment,
                    )
                )
                logger.info(
                    f"[{self.name}] 反思: {reflection.assessment[:200]}"
                )

        # ── 构建最终结果 ──
        duration = time.time() - self._start_time

        # 持久化记忆
        if self.memory:
            self.memory.commit_run(
                agent_name=self.name,
                task=task,
                summary=final_text,
                trajectory=self._trajectory,
            )

        return AgentResult(
            success=True,
            summary=final_text,
            trajectory=self._trajectory,
            metrics={
                "iterations": len(
                    [t for t in self._trajectory if t.step_type == StepType.THINK]
                ),
                "tool_calls": len(
                    [t for t in self._trajectory if t.step_type == StepType.ACT]
                ),
                "duration_seconds": round(duration, 2),
            },
        )

    # ── Internal Methods ────────────────────────────────────

    async def _call_llm(self) -> Any:
        """调用 Gemini API (Async)"""
        # Build tool declarations
        tool_declarations = [
            spec.to_function_declaration()
            for spec in self.tool_registry.values()
        ]
        gemini_tools = [types.Tool(function_declarations=tool_declarations)]

        # 使用 aio 客户端进行异步生成
        return await self.llm.aio.models.generate_content(
            model=self.model,
            contents=self._messages,
            config=types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=gemini_tools,
                temperature=self.temperature,
            ),
        )

    async def _execute_tool(
        self, tool_name: str, tool_args: dict, db=None
    ) -> AgentObservation:
        """执行单个 Tool (支持 Async/Sync Handler)"""
        tool_spec = self.tool_registry.get(tool_name)
        if not tool_spec or not tool_spec.handler:
            return AgentObservation(
                tool_name=tool_name,
                result={"status": "error", "message": f"Unknown tool: {tool_name}"},
                error=f"Unknown tool: {tool_name}",
            )

        import asyncio

        try:
            # 判断 handler 是否为异步函数
            if asyncio.iscoroutinefunction(tool_spec.handler):
                result = await tool_spec.handler(db, **tool_args)
            else:
                result = tool_spec.handler(db, **tool_args)

            return AgentObservation(
                tool_name=tool_name,
                result=result if isinstance(result, dict) else {"result": result},
            )
        except Exception as e:
            logger.error(f"[{self.name}] Tool {tool_name} 失败: {e}")
            return AgentObservation(
                tool_name=tool_name,
                result={"status": "error", "message": str(e)},
                error=str(e),
            )

    async def _reflect(self) -> ReflectionResult:
        """
        自我反思 (Async)

        让 LLM 评估当前执行轨迹——是否高效？是否有遗漏？
        """
        trajectory_text = self._format_trajectory_for_reflection()
        prompt = f"""请评估以下 Agent 的执行轨迹，判断是否需要调整策略。

执行轨迹:
{trajectory_text}

请用 JSON 格式回答:
{{
    "assessment": "对执行质量的评估",
    "should_adjust": false,
    "adjusted_strategy": "如果需要调整，给出新策略",
    "confidence": 0.8
}}"""

        try:
            response = await self.llm.aio.models.generate_content(
                model=self.model,
                contents=[prompt],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                ),
            )
            data = json.loads(response.text)
            return ReflectionResult(
                assessment=data.get("assessment", ""),
                should_adjust=data.get("should_adjust", False),
                adjusted_strategy=data.get("adjusted_strategy", ""),
                confidence=data.get("confidence", 0.8),
            )
        except Exception as e:
            logger.warning(f"[{self.name}] 反思失败: {e}")
            return ReflectionResult(assessment=f"Reflection failed: {e}")

    def _format_trajectory_for_reflection(self) -> str:
        """将执行轨迹格式化为文本, 用于反思"""
        lines = []
        for step in self._trajectory[-10:]:  # 最近 10 步
            if step.step_type == StepType.ACT:
                lines.append(
                    f"[ACT] {step.tool_name}({json.dumps(step.tool_args, ensure_ascii=False)})"
                )
            elif step.step_type == StepType.OBSERVE:
                result_str = json.dumps(step.tool_result, ensure_ascii=False)[:300]
                lines.append(f"[OBSERVE] {step.tool_name} → {result_str}")
        return "\n".join(lines)
