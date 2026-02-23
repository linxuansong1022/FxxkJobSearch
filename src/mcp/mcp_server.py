"""
MCP Server — 标准化 Tool 接口

对应 hello-agents 第十章 (智能体通信协议):
- MCP (Model Context Protocol): Anthropic 主导的 Tool 调用标准
- 本模块将 FxxkJobSearch 的所有 Tool 封装为 MCP Server
- 支持 stdio 和 SSE 两种传输方式

核心优势 (vs 直接 Function Calling):
1. 动态发现: Agent 不需要硬编码 Tool schema
2. 可复用: 其他 Agent 系统可连接同一个 Server
3. 标准化: 行业通用协议
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# MCP Server 实现
# 注意: 需要 pip install mcp 才能使用完整功能
# 这里提供了一个轻量级的兼容实现


class MCPToolDefinition:
    """MCP Tool 定义 — 兼容 MCP 协议的 JSON Schema"""

    def __init__(self, name: str, description: str, input_schema: dict, handler: Any):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler

    def to_dict(self) -> dict:
        """转换为 MCP 协议格式"""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class MCPServer:
    """
    FxxkJobSearch MCP Server

    将所有 Tool 封装为 MCP 协议兼容的接口。
    支持:
    - list_tools(): 列出所有可用 Tool
    - call_tool():  调用指定 Tool

    传输方式:
    - stdio: 本地进程间通信 (开发/测试)
    - SSE:   HTTP Server-Sent Events (生产部署)
    """

    def __init__(self, name: str = "fxxkjobsearch-mcp"):
        self.name = name
        self._tools: dict[str, MCPToolDefinition] = {}
        self._db = None

    def set_db(self, db):
        """注入数据库实例"""
        self._db = db

    def register_tool(self, tool_def: MCPToolDefinition):
        """注册一个 Tool"""
        self._tools[tool_def.name] = tool_def
        logger.debug(f"MCP: 注册 Tool: {tool_def.name}")

    def list_tools(self) -> list[dict]:
        """列出所有可用 Tool — MCP list_tools 协议"""
        return [tool.to_dict() for tool in self._tools.values()]

    def call_tool(self, name: str, arguments: dict) -> dict:
        """调用指定 Tool — MCP call_tool 协议"""
        tool = self._tools.get(name)
        if not tool:
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown tool: {name}"}],
            }

        try:
            result = tool.handler(self._db, **arguments)
            return {
                "isError": False,
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False),
                    }
                ],
            }
        except Exception as e:
            logger.error(f"MCP Tool 执行失败: {name} → {e}")
            return {
                "isError": True,
                "content": [{"type": "text", "text": f"Error: {str(e)}"}],
            }

    def get_server_info(self) -> dict:
        """MCP Server 信息"""
        return {
            "name": self.name,
            "version": "1.0.0",
            "capabilities": {
                "tools": {"listChanged": False},
            },
        }


def create_mcp_server() -> MCPServer:
    """
    创建并配置 MCP Server

    将 src/tools 中的所有 Tool 自动注册到 MCP Server。
    """
    from src.tools import ALL_TOOLS

    server = MCPServer()

    for tool_spec in ALL_TOOLS:
        mcp_tool = MCPToolDefinition(
            name=tool_spec.name,
            description=tool_spec.description,
            input_schema={
                "type": "object",
                "properties": tool_spec.parameters.get("properties", {}),
                "required": tool_spec.parameters.get("required", []),
            },
            handler=tool_spec.handler,
        )
        server.register_tool(mcp_tool)

    logger.info(f"MCP Server 创建完成, 注册了 {len(server._tools)} 个 Tool")
    return server


def run_mcp_stdio_server(db=None):
    """
    以 stdio 模式运行 MCP Server

    用于本地开发/测试: 从 stdin 读取 JSON-RPC 请求, 写入 stdout。
    """
    import sys

    server = create_mcp_server()
    if db:
        server.set_db(db)

    print(json.dumps({"jsonrpc": "2.0", "result": server.get_server_info()}))
    sys.stdout.flush()

    logger.info("MCP stdio server 已启动, 等待请求...")

    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            params = request.get("params", {})
            req_id = request.get("id")

            if method == "tools/list":
                result = server.list_tools()
            elif method == "tools/call":
                result = server.call_tool(
                    params.get("name", ""),
                    params.get("arguments", {}),
                )
            else:
                result = {"error": f"Unknown method: {method}"}

            response = {"jsonrpc": "2.0", "id": req_id, "result": result}
            print(json.dumps(response, ensure_ascii=False))
            sys.stdout.flush()

        except json.JSONDecodeError:
            pass
        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32000, "message": str(e)},
            }
            print(json.dumps(error_response))
            sys.stdout.flush()
