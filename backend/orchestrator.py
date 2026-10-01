import time
from pathlib import Path
from typing import Any

from fastmcp import Client

try:
    from .router import extract_tool_arguments, select_tool_with_laya
except ImportError:
    from router import extract_tool_arguments, select_tool_with_laya


async def execute_pipeline(user_prompt: str) -> dict[str, Any]:
    """Route, extract, execute, and report telemetry for one administrative command."""
    total_start = time.perf_counter()
    server_path = Path(__file__).with_name("mcp_server.py")

    async with Client(server_path) as client:
        tools = [tool.model_dump() for tool in await client.list_tools()]
        tool_name, route_latency = select_tool_with_laya(user_prompt, tools)
        tool_schema = next(
            (tool for tool in tools if tool["name"] == tool_name), None
        )
        if tool_schema is None:
            raise ValueError(f"Router selected unknown tool: {tool_name}")
        arguments, extraction_latency = extract_tool_arguments(user_prompt, tool_schema)

        mcp_start = time.perf_counter()
        response = await client.call_tool(tool_name, arguments=arguments)
        mcp_latency = round((time.perf_counter() - mcp_start) * 1000, 2)
        result = response.data if getattr(response, "data", None) is not None else str(response)

    return {
        "prompt": user_prompt,
        "tool_selected": tool_name,
        "arguments": arguments,
        "result": result,
        "telemetry": {
            "route_latency_ms": route_latency,
            "extraction_latency_ms": extraction_latency,
            "mcp_latency_ms": mcp_latency,
            "total_latency_ms": round((time.perf_counter() - total_start) * 1000, 2),
        },
    }
