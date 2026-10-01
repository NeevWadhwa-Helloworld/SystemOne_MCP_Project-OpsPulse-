import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from laya import Router
from litellm import completion


load_dotenv()
laya_router = Router()


def select_tool_with_laya(
    user_query: str, available_tools: list[dict[str, Any]]
) -> tuple[str, float]:
    """Select an MCP tool and return the selection latency in milliseconds."""
    start = time.perf_counter()
    criteria = {
        tool["name"]: tool.get("description", tool["name"]) for tool in available_tools
    }
    result = laya_router.predict(
        user_query,
        {
            "selected_tool": {
                "type": "choice",
                "instructions": "Which monitoring tool best handles this query?",
                "criteria": criteria,
            }
        },
    )
    selected = result["answers"]["selected_tool"]["choice"]
    return selected, round((time.perf_counter() - start) * 1000, 2)


def _local_arguments(query: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Extract common values locally so development does not require a paid API."""
    arguments: dict[str, Any] = {}
    lowered = query.lower()
    for name, definition in schema.get("properties", {}).items():
        value: Any = None
        value_type = definition.get("type")
        if value_type == "integer":
            match = re.search(r"\b\d+\b", query)
            value = int(match.group()) if match else None
        elif value_type == "number":
            match = re.search(r"\b\d+(?:\.\d+)?\b", query)
            value = float(match.group()) if match else None
        elif value_type == "string":
            enum = definition.get("enum", [])
            value = next((item for item in enum if str(item).lower() in lowered), None)
            if value is None and name.lower() in {"url", "endpoint"}:
                match = re.search(r"https?://[^\s,]+", query, re.I)
                value = match.group(0).rstrip(").,") if match else None
            if value is None and name.lower() in {"hostname", "host"}:
                match = re.search(
                    r"\b(?:hostname|host|domain)\s+(?:of\s+)?([A-Za-z0-9][A-Za-z0-9.-]+)",
                    query,
                    re.I,
                )
                value = match.group(1).rstrip(".") if match else None
            if value is None and "email" in name.lower():
                match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", query)
                value = match.group() if match else None
            if value is None and name.lower() in {"node_id", "user_id", "cache_name"}:
                if name.lower() == "node_id":
                    match = re.search(
                        r"\b(?:compute\s+)?node(?:\s+id)?\s+([A-Za-z0-9_.-]+)\b",
                        query,
                        re.I,
                    )
                    value = match.group(1) if match else None
                else:
                    match = re.search(
                        r"\b(?:prod|primary|usr|cache)[\w.-]*\b", query, re.I
                    )
                    value = match.group() if match else None
            if value is None and name.lower() == "channel":
                match = re.search(r"(?:channel|to)\s+[#]?([\w-]+)", query, re.I)
                value = match.group(1) if match else None
            if value is None and name.lower() == "alert_message":
                match = re.search(r"(?:about|saying|message)\s+(.+)$", query, re.I)
                value = match.group(1).strip() if match else None
            if value is None and name.lower() == "reason":
                match = re.search(r"(?:for|reason)\s+(.+)$", query, re.I)
                value = match.group(1).strip() if match else None
            if value is None and name.lower() == "environment":
                value = next(
                    (item for item in ("production", "staging", "development") if item in lowered),
                    None,
                )
        if value is not None:
            arguments[name] = value
    return arguments


def extract_tool_arguments(
    user_query: str, selected_tool_schema: dict[str, Any]
) -> tuple[dict[str, Any], float]:
    """Extract tool arguments using Gemini when configured, otherwise locally."""
    start = time.perf_counter()
    schema = (
        selected_tool_schema.get("inputSchema")
        or selected_tool_schema.get("input_schema")
        or selected_tool_schema.get("parameters")
        or {}
    )
    if not schema.get("properties"):
        return {}, 0.0

    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        arguments = _local_arguments(user_query, schema)
    else:
        prompt = (
            "Return only a JSON object containing values extracted from this user "
            "query, matching the supplied JSON schema. "
            f"Schema: {json.dumps(schema)} Query: {user_query}"
        )
        response = completion(
            model=os.getenv("GEMINI_MODEL", "gemini/gemini-2.0-flash"),
            response_format={"type": "json_object"},
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        arguments = json.loads(content) if isinstance(content, str) else {}

    missing = [name for name in schema.get("required", []) if name not in arguments]
    if missing:
        raise ValueError(f"Could not extract required arguments: {', '.join(missing)}")
    return arguments, round((time.perf_counter() - start) * 1000, 2)


def _fallback_explanation(result: Any) -> str:
    """Provide a useful local explanation when Gemini is not configured."""
    if isinstance(result, dict):
        if result.get("ok") is True:
            status = result.get("status") or "successful"
            details = []
            if result.get("status_code") is not None:
                details.append(f"HTTP status {result['status_code']}")
            if result.get("latency_ms") is not None:
                details.append(f"latency {result['latency_ms']} ms")
            suffix = f" ({', '.join(details)})" if details else ""
            return f"The operation completed successfully with status: {status}{suffix}."
        if result.get("ok") is False:
            detail = result.get("error") or result.get("detail") or "the target reported a failure"
            return f"The operation was not successful because {detail}."
    return "The operation completed. The structured response below contains the exact API result."


def explain_result(result: Any) -> str:
    """Ask Gemini to explain an API result without changing the exact payload."""
    fallback = _fallback_explanation(result)
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        return fallback

    prompt = (
        "Explain the following OpsPulse API response in concise, natural language for "
        "an operations user. State what happened, whether it succeeded or failed, and "
        "include only facts present in the response. Do not output JSON, markdown, "
        "headings, or recommendations. Keep it to one or two sentences.\n\n"
        f"API response:\n{json.dumps(result, ensure_ascii=True, default=str)}"
    )
    try:
        response = completion(
            model=os.getenv("GEMINI_MODEL", "gemini/gemini-2.0-flash"),
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        return fallback
    return fallback


def generate_monitor_report(
    resource_name: str,
    summary: dict[str, Any],
    history: list[dict[str, Any]],
) -> str:
    """Generate a concise monitoring report from persisted read-only data."""
    payload = {
        "resource_name": resource_name,
        "summary": summary,
        "recent_history": history,
    }
    fallback = (
        f"Monitoring report for {resource_name}: {summary.get('total_checks', 0)} "
        f"checks recorded, {summary.get('uptime_percent', 0)}% uptime, and an "
        f"average latency of {summary.get('avg_latency_ms') or 0} ms. "
        f"The report includes the {len(history)} most recent checks."
    )
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        return fallback

    prompt = (
        "Write a concise natural-language monitoring report for an operations user "
        "using only the supplied data. Mention uptime, check count, average latency, "
        "the latest status, and any recent failures. Do not invent causes or facts. "
        "Return plain text in two or three short paragraphs; do not return JSON.\n\n"
        f"Monitoring data:\n{json.dumps(payload, ensure_ascii=True, default=str)}"
    )
    try:
        response = completion(
            model=os.getenv("GEMINI_MODEL", "gemini/gemini-2.0-flash"),
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if isinstance(content, str) and content.strip():
            return content.strip()
    except Exception:
        return fallback
    return fallback
