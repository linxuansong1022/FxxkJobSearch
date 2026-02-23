"""
Test Suite — Agent 核心框架测试

覆盖:
1. BaseAgent ReAct Loop (mocked LLM)
2. ToolSpec 定义和执行
3. Memory System (三层记忆)
4. MCP Server (注册/调用)
5. Evaluation System (指标计算)
"""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from dataclasses import dataclass

import pytest


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: ToolSpec
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestToolSpec:
    """测试 ToolSpec 定义和转换"""

    def test_create_tool_spec(self):
        from src.agents.base_agent import ToolSpec

        def dummy_handler(db, **kwargs):
            return {"status": "ok"}

        tool = ToolSpec(
            name="test_tool",
            description="A test tool",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    }
                },
                "required": ["query"],
            },
            handler=dummy_handler,
        )

        assert tool.name == "test_tool"
        assert tool.description == "A test tool"
        assert tool.handler is dummy_handler

    def test_tool_spec_to_function_declaration(self):
        from src.agents.base_agent import ToolSpec

        tool = ToolSpec(
            name="search",
            description="Search for items",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                    },
                },
                "required": ["query"],
            },
        )

        fd = tool.to_function_declaration()
        assert fd.name == "search"
        assert fd.description == "Search for items"

    def test_tool_handler_execution(self):
        from src.agents.base_agent import ToolSpec

        def add_handler(db, a: int = 0, b: int = 0):
            return {"result": a + b}

        tool = ToolSpec(
            name="add",
            description="Add two numbers",
            parameters={"type": "object", "properties": {}},
            handler=add_handler,
        )

        result = tool.handler(None, a=3, b=5)
        assert result == {"result": 8}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Data Models
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestDataModels:
    """测试 Agent 数据模型"""

    def test_agent_action(self):
        from src.agents.base_agent import AgentAction

        action = AgentAction(
            tool_name="scrape_linkedin",
            tool_args={"keywords": ["python"]},
            raw_thought="Should scrape LinkedIn first",
        )
        assert action.tool_name == "scrape_linkedin"
        assert action.tool_args["keywords"] == ["python"]

    def test_agent_observation(self):
        from src.agents.base_agent import AgentObservation

        obs = AgentObservation(
            tool_name="scrape_linkedin",
            result={"status": "success", "new_jobs": 5},
        )
        assert not obs.is_terminal
        assert obs.result["new_jobs"] == 5

    def test_trajectory_step(self):
        from src.agents.base_agent import TrajectoryStep, StepType

        step = TrajectoryStep(
            step_type=StepType.ACT,
            content="Called scrape_linkedin",
            tool_name="scrape_linkedin",
            tool_args={"keywords": ["python"]},
        )
        assert step.step_type == StepType.ACT
        assert step.tool_name == "scrape_linkedin"

    def test_agent_result(self):
        from src.agents.base_agent import AgentResult

        result = AgentResult(
            success=True,
            summary="Completed 5 tool calls",
            metrics={"iterations": 3, "tool_calls": 5, "duration_seconds": 12.5},
        )
        assert result.success
        assert result.metrics["tool_calls"] == 5


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Memory System
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestWorkingMemory:
    """测试工作记忆"""

    def test_add_and_get(self):
        from src.memory.memory import WorkingMemory

        wm = WorkingMemory(max_tokens=1000)
        wm.add("First entry")
        wm.add("Second entry")

        context = wm.get_context()
        assert "First entry" in context
        assert "Second entry" in context

    def test_auto_prune(self):
        from src.memory.memory import WorkingMemory

        wm = WorkingMemory(max_tokens=50)
        wm.add("A" * 100)  # This fills the budget
        wm.add("B" * 100)  # This should push A out

        context = wm.get_context()
        # After pruning, only the last entry should remain
        assert "B" * 100 in context

    def test_clear(self):
        from src.memory.memory import WorkingMemory

        wm = WorkingMemory()
        wm.add("test")
        wm.clear()
        assert wm.get_context() == ""


class TestShortTermMemory:
    """测试短期记忆"""

    def test_add_and_get_recent(self):
        from src.memory.memory import ShortTermMemory, MemoryEntry

        stm = ShortTermMemory(max_entries=10)
        stm.add(MemoryEntry(content="Entry 1", source="scout", memory_type="trajectory"))
        stm.add(MemoryEntry(content="Entry 2", source="filter", memory_type="trajectory"))

        recent = stm.get_recent(5)
        assert len(recent) == 2
        assert recent[0].content == "Entry 1"

    def test_get_by_source(self):
        from src.memory.memory import ShortTermMemory, MemoryEntry

        stm = ShortTermMemory()
        stm.add(MemoryEntry(content="Scout ran", source="scout", memory_type="trajectory"))
        stm.add(MemoryEntry(content="Filter ran", source="filter", memory_type="trajectory"))

        scout_memories = stm.get_by_source("scout")
        assert len(scout_memories) == 1
        assert scout_memories[0].content == "Scout ran"

    def test_high_importance_filter(self):
        from src.memory.memory import ShortTermMemory, MemoryEntry

        stm = ShortTermMemory()
        stm.add(MemoryEntry(content="Low", source="a", memory_type="t", importance=0.3))
        stm.add(MemoryEntry(content="High", source="b", memory_type="t", importance=0.9))

        high = stm.get_high_importance(0.7)
        assert len(high) == 1
        assert high[0].content == "High"


class TestLongTermMemory:
    """测试长期记忆 (SQLite)"""

    def test_store_and_recall(self):
        from src.memory.memory import LongTermMemory, MemoryEntry

        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ltm = LongTermMemory(f.name)

            ltm.store(MemoryEntry(
                content="LinkedIn scraping found 10 jobs",
                source="scout",
                memory_type="trajectory",
                importance=0.8,
            ))

            results = ltm.recall("LinkedIn jobs", k=5)
            assert len(results) >= 1
            assert "LinkedIn" in results[0]

            ltm.close()

    def test_store_run(self):
        from src.memory.memory import LongTermMemory, RunRecord

        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ltm = LongTermMemory(f.name)

            ltm.store_run(RunRecord(
                agent_name="ScoutAgent",
                task="Scrape jobs",
                summary="Scraped 10 jobs",
                trajectory_json="[]",
            ))

            runs = ltm.get_recent_runs(5)
            assert len(runs) == 1
            assert runs[0]["agent_name"] == "ScoutAgent"

            ltm.close()


class TestMemorySystem:
    """测试统一记忆系统"""

    def test_commit_and_recall(self):
        from src.memory.memory import MemorySystem

        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ms = MemorySystem(f.name)

            ms.commit_run(
                agent_name="ScoutAgent",
                task="Scrape LinkedIn",
                summary="Found 5 new jobs on LinkedIn",
                trajectory=[],
            )

            results = ms.recall("LinkedIn", k=3)
            assert len(results) >= 1

            ms.close()

    def test_consolidate(self):
        from src.memory.memory import MemorySystem, MemoryEntry

        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            ms = MemorySystem(f.name)

            # Add high-importance short-term memory
            ms.short_term.add(MemoryEntry(
                content="Important discovery: Jobindex has more intern listings",
                source="scout",
                memory_type="insight",
                importance=0.9,
            ))

            ms.consolidate()

            # Should now be in long-term memory
            results = ms.long_term.recall("Jobindex intern", k=5)
            assert len(results) >= 1

            ms.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Context Engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestContextEngine:
    """测试上下文工程"""

    def test_build_context(self):
        from src.memory.context_engine import ContextEngine

        engine = ContextEngine(max_tokens=5000)
        context = engine.build_context(
            system_info="You are a job search agent",
            task="Scrape LinkedIn jobs",
            memory_items=["Previous run found 10 jobs"],
            tool_results=[{"status": "success", "new_jobs": 5}],
        )

        assert "job search agent" in context
        assert "LinkedIn" in context
        assert "Previous run" in context

    def test_token_budget_enforcement(self):
        from src.memory.context_engine import ContextEngine

        engine = ContextEngine(max_tokens=100)
        context = engine.build_context(
            system_info="A" * 500,
            task="B" * 500,
        )

        # Should be truncated to fit budget
        assert len(context) < 500


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: MCP Server
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMCPServer:
    """测试 MCP Server"""

    def test_register_and_list_tools(self):
        from src.mcp.mcp_server import MCPServer, MCPToolDefinition

        server = MCPServer()

        tool = MCPToolDefinition(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            handler=lambda db: {"status": "ok"},
        )
        server.register_tool(tool)

        tools = server.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    def test_call_tool_success(self):
        from src.mcp.mcp_server import MCPServer, MCPToolDefinition

        server = MCPServer()

        def handler(db, **kwargs):
            return {"result": "hello"}

        tool = MCPToolDefinition(
            name="greet",
            description="Greet",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )
        server.register_tool(tool)

        result = server.call_tool("greet", {})
        assert not result["isError"]
        content = json.loads(result["content"][0]["text"])
        assert content["result"] == "hello"

    def test_call_unknown_tool(self):
        from src.mcp.mcp_server import MCPServer

        server = MCPServer()
        result = server.call_tool("nonexistent", {})
        assert result["isError"]

    def test_create_mcp_server_factory(self):
        from src.mcp.mcp_server import create_mcp_server

        server = create_mcp_server()
        tools = server.list_tools()
        # Should have all tools registered
        assert len(tools) >= 8  # We defined 9 tools
        tool_names = [t["name"] for t in tools]
        assert "scrape_linkedin" in tool_names
        assert "get_db_status" in tool_names


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: MCP Client
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestMCPClient:
    """测试 MCP Client"""

    def test_direct_connection(self):
        from src.mcp.mcp_server import MCPServer, MCPToolDefinition
        from src.mcp.mcp_client import MCPToolManager

        server = MCPServer()
        server.register_tool(MCPToolDefinition(
            name="test",
            description="Test",
            input_schema={"type": "object", "properties": {}},
            handler=lambda db, **kw: {"status": "ok"},
        ))

        client = MCPToolManager()
        client.connect_direct(server)

        tools = client.discover_tools()
        assert len(tools) == 1

    def test_to_tool_specs(self):
        from src.mcp.mcp_server import MCPServer, MCPToolDefinition
        from src.mcp.mcp_client import MCPToolManager

        server = MCPServer()
        server.register_tool(MCPToolDefinition(
            name="test",
            description="Test tool",
            input_schema={"type": "object", "properties": {}},
            handler=lambda db, **kw: {"status": "ok"},
        ))

        client = MCPToolManager()
        client.connect_direct(server)

        specs = client.to_tool_specs()
        assert len(specs) == 1
        assert specs[0].name == "test"
        assert specs[0].handler is not None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Evaluation System
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestEvaluation:
    """测试评估系统"""

    def _make_mock_result(self):
        from src.agents.base_agent import AgentResult, TrajectoryStep, StepType

        trajectory = [
            TrajectoryStep(step_type=StepType.THINK, content="Planning"),
            TrajectoryStep(step_type=StepType.ACT, content="Scraping", tool_name="scrape_thehub"),
            TrajectoryStep(step_type=StepType.OBSERVE, content="Done", tool_name="scrape_thehub",
                           tool_result={"new_jobs": 5}),
            TrajectoryStep(step_type=StepType.ACT, content="Filtering", tool_name="filter_jobs"),
            TrajectoryStep(step_type=StepType.OBSERVE, content="Done", tool_name="filter_jobs"),
            TrajectoryStep(step_type=StepType.ACT, content="Analyzing", tool_name="analyze_jobs"),
        ]

        return AgentResult(
            success=True,
            summary="Completed pipeline",
            trajectory=trajectory,
            metrics={"iterations": 3, "tool_calls": 3, "duration_seconds": 15.0},
        )

    def test_tool_calling_evaluation(self):
        from src.evaluation.evaluator import AgentEvaluator, TestCase

        evaluator = AgentEvaluator()
        result = self._make_mock_result()
        test_case = TestCase(
            name="full_pipeline",
            task="Run full pipeline",
            expected_tool_sequence=["scrape_thehub", "filter_jobs", "analyze_jobs"],
        )

        report = evaluator.evaluate(result, test_case)
        assert report.tool_call_metrics.accuracy == 1.0
        assert report.tool_call_metrics.correct_calls == 3

    def test_efficiency_evaluation(self):
        from src.evaluation.evaluator import AgentEvaluator, TestCase

        evaluator = AgentEvaluator()
        result = self._make_mock_result()
        test_case = TestCase(name="test", task="test", expected_tool_sequence=[])

        report = evaluator.evaluate(result, test_case)
        assert report.efficiency_metrics.total_tool_calls == 3
        assert report.efficiency_metrics.redundant_calls == 0

    def test_redundant_call_detection(self):
        from src.agents.base_agent import AgentResult, TrajectoryStep, StepType
        from src.evaluation.evaluator import AgentEvaluator, TestCase

        # Create a trajectory with redundant calls
        trajectory = [
            TrajectoryStep(step_type=StepType.ACT, content="", tool_name="get_db_status"),
            TrajectoryStep(step_type=StepType.ACT, content="", tool_name="get_db_status"),  # redundant!
            TrajectoryStep(step_type=StepType.ACT, content="", tool_name="scrape_thehub"),
        ]

        result = AgentResult(
            success=True, summary="", trajectory=trajectory,
            metrics={"iterations": 2, "tool_calls": 3},
        )

        evaluator = AgentEvaluator()
        report = evaluator.evaluate(result, TestCase(name="t", task="t", expected_tool_sequence=[]))
        assert report.efficiency_metrics.redundant_calls == 1

    def test_report_print(self):
        from src.evaluation.evaluator import EvaluationReport, ToolCallMetrics, TaskMetrics

        report = EvaluationReport(
            agent_name="TestAgent",
            task="Test task",
            tool_call_metrics=ToolCallMetrics(
                total_calls=5, correct_calls=4, accuracy=0.8
            ),
            task_metrics=TaskMetrics(
                total_steps=3, completed_steps=2, completion_rate=0.67
            ),
        )
        # Should not raise
        report.print_report()

    def test_report_to_dict(self):
        from src.evaluation.evaluator import EvaluationReport

        report = EvaluationReport(agent_name="Test", task="Test")
        d = report.to_dict()
        assert "agent_name" in d
        assert "tool_calling" in d
        assert "efficiency" in d


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Test: Tools Layer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestToolsLayer:
    """测试 Tools Layer"""

    def test_tool_groups_defined(self):
        from src.tools import SCOUT_TOOLS, FILTER_TOOLS, ANALYST_TOOLS, NOTIFIER_TOOLS, ALL_TOOLS

        assert len(SCOUT_TOOLS) >= 5
        assert len(FILTER_TOOLS) >= 2
        assert len(ANALYST_TOOLS) >= 3
        assert len(NOTIFIER_TOOLS) >= 2
        assert len(ALL_TOOLS) >= 9

    def test_all_tools_have_handlers(self):
        from src.tools import ALL_TOOLS

        for tool in ALL_TOOLS:
            assert tool.handler is not None, f"Tool {tool.name} missing handler"
            assert tool.name, "Tool missing name"
            assert tool.description, f"Tool {tool.name} missing description"

    def test_get_db_status_handler(self):
        """Test get_db_status with a mock db"""
        from src.tools import handle_get_db_status

        mock_db = MagicMock()
        mock_db.get_status_counts.return_value = {"new": 5, "analyzed": 3}
        mock_db.get_relevance_counts.return_value = {"relevant": 4, "irrelevant": 2}
        mock_db.get_unscored_jobs.return_value = [1, 2, 3]
        mock_db.get_jobs_by_status.return_value = [
            {"relevance": "relevant"},
            {"relevance": "irrelevant"},
        ]

        result = handle_get_db_status(mock_db)
        assert result["total_jobs_in_db"] == 8
        assert result["pending_filter"] == 3
