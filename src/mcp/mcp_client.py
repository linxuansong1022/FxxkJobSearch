"""
MCP Client — Agent 侧的 MCP Tool 消费者

负责:
1. 连接 MCP Server
2. 动态发现可用 Tool
3. 将 MCP Tool 转换为 Agent 可用的 ToolSpec
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.agents.base_agent import ToolSpec

logger = logging.getLogger(__name__)


class MCPToolManager:
    """
    MCP Client — 通过 MCP 协议动态发现和调用 Tool

    工作流:
    1. connect() → 连接到 MCP Server
    2. discover_tools() → 获取可用 Tool 列表
    3. call_tool() → 调用指定 Tool

    支持两种模式:
    - 直连: 直接引用 MCPServer 实例 (进程内)
    - stdio: 通过 subprocess 连接外部 MCP Server
    """

    def __init__(self):
        self._server = None
        self._tools_cache: dict[str, dict] = {}

    def connect_direct(self, server):
        """直连模式: 直接引用 MCPServer 实例"""
        self._server = server
        self._refresh_tools()
        logger.info(f"MCP Client 直连成功, 发现 {len(self._tools_cache)} 个 Tool")

    def _refresh_tools(self):
        """从 Server 刷新 Tool 列表"""
        if self._server:
            tools = self._server.list_tools()
            self._tools_cache = {t["name"]: t for t in tools}

    def discover_tools(self) -> list[dict]:
        """动态发现可用 Tool — MCP 核心能力"""
        self._refresh_tools()
        return list(self._tools_cache.values())

    def call_tool(self, name: str, arguments: dict) -> dict:
        """通过 MCP 协议调用 Tool"""
        if not self._server:
            return {"status": "error", "message": "Not connected to MCP Server"}

        result = self._server.call_tool(name, arguments)

        if result.get("isError"):
            error_text = result.get("content", [{}])[0].get("text", "Unknown error")
            return {"status": "error", "message": error_text}

        # 解析 MCP 响应内容
        content = result.get("content", [])
        if content and content[0].get("type") == "text":
            try:
                return json.loads(content[0]["text"])
            except json.JSONDecodeError:
                return {"status": "success", "result": content[0]["text"]}

        return {"status": "success", "result": str(content)}

    def to_tool_specs(self) -> list[ToolSpec]:
        """
        将 MCP Tool 转换为 Agent 可用的 ToolSpec

        这使得 Agent 可以通过 MCP 协议动态获取工具,
        而不是硬编码 Tool 列表。
        """
        specs = []
        for name, tool_info in self._tools_cache.items():
            # 创建 MCP 代理 handler
            def make_handler(tool_name):
                def handler(db=None, **kwargs):
                    return self.call_tool(tool_name, kwargs)
                return handler

            spec = ToolSpec(
                name=name,
                description=tool_info.get("description", ""),
                parameters=tool_info.get("inputSchema", {"type": "object", "properties": {}}),
                handler=make_handler(name),
            )
            specs.append(spec)

        return specs
