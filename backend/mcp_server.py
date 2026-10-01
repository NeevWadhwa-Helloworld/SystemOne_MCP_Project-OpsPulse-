"""Generic monitoring tools exposed through the OpsPulse MCP server."""

from fastmcp import FastMCP

try:
    from .integrations import check_dns_records, check_ssl_cert, health_check, send_webhook
    from .router import generate_monitor_report
    from .security import Store
except ImportError:  # Allows `python backend/mcp_server.py`.
    from integrations import check_dns_records, check_ssl_cert, health_check, send_webhook
    from router import generate_monitor_report
    from security import Store

mcp = FastMCP("OpsPulse Platform Engine")


def _caller(store: Store, caller_identity: str | None) -> dict:
    if not caller_identity:
        raise ValueError("caller_identity is required for managed-resource MCP tools.")
    user = store.user_by_identity(caller_identity)
    if not user:
        raise ValueError("caller_identity is invalid or disabled.")
    return user


@mcp.tool(name="check_ssl_cert")
async def check_ssl_cert_resource(url: str, timeout: float = 10.0) -> dict:
    """Inspect and validate the TLS certificate for a public HTTPS endpoint."""
    return await check_ssl_cert(url, timeout)


@mcp.tool(name="check_dns_records")
def check_dns_records_resource(
    hostname: str, record_types: list[str] | None = None
) -> dict:
    """Resolve public A, AAAA, and CNAME records for a hostname."""
    return check_dns_records(hostname, record_types)


@mcp.tool(name="health_check")
async def health_check_resource(
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    retries: int = 0,
) -> dict:
    """Check whether a public HTTP resource is reachable and measure its latency."""
    return await health_check(url, method, timeout, retries)


@mcp.tool(name="send_alert")
async def send_alert(url: str, payload: dict, secret: str | None = None) -> dict:
    """Send a JSON monitoring alert to a configured webhook."""
    return await send_webhook(url, secret, payload)


@mcp.tool()
async def monitor_health_check(resource_id: int, caller_identity: str | None = None) -> dict:
    """Run and record a health check for a managed HTTP resource."""
    store = Store()
    user = _caller(store, caller_identity)
    record = store.resource(resource_id, user)
    if not record or record[0]["kind"] != "health_check":
        raise ValueError("Managed health-check resource not found.")
    metadata, _ = record
    result = await health_check(
        metadata["url"],
        metadata.get("method", "GET"),
        metadata.get("timeout_seconds", 10),
        metadata.get("retries", 0),
    )
    history_id = store.record_check(resource_id, result)
    return {**result, "resource_id": resource_id, "history_id": history_id}


@mcp.tool()
async def monitor_status(resource_id: int, caller_identity: str | None = None) -> dict:
    """Return the latest status and check summary for a managed resource."""
    store = Store()
    user = _caller(store, caller_identity)
    record = store.resource(resource_id, user)
    if not record:
        raise ValueError("Managed monitoring resource not found.")
    latest = store.latest_check(resource_id, user)
    return {
        "resource_id": resource_id,
        "resource_name": record[0]["name"],
        "latest": latest,
        "summary": store.summary(resource_id, user),
    }


@mcp.tool()
async def monitor_history(resource_id: int, limit: int = 50, caller_identity: str | None = None) -> dict:
    """Return recent recorded checks for a managed monitoring resource."""
    store = Store()
    user = _caller(store, caller_identity)
    if not store.resource(resource_id, user):
        raise ValueError("Managed monitoring resource not found.")
    return {"resource_id": resource_id, "history": store.history(resource_id, limit, user)}


@mcp.tool()
async def monitor_report(resource_id: int, limit: int = 50, caller_identity: str | None = None) -> dict:
    """Generate a read-only natural-language report from monitor history."""
    store = Store()
    user = _caller(store, caller_identity)
    record = store.resource(resource_id, user)
    if not record or record[0]["kind"] != "health_check":
        raise ValueError("Managed health-check resource not found.")
    metadata, _ = record
    summary = store.summary(resource_id, user)
    history = store.history(resource_id, limit, user)
    return {
        "resource_id": resource_id,
        "resource_name": metadata["name"],
        "summary": summary,
        "report": generate_monitor_report(metadata["name"], summary, history),
        "history": history,
    }


@mcp.tool()
async def enable_monitor(resource_id: int, caller_identity: str | None = None) -> dict:
    """Enable scheduled checks for a managed health resource."""
    return _set_monitor_enabled(resource_id, True, caller_identity)


@mcp.tool()
async def disable_monitor(resource_id: int, caller_identity: str | None = None) -> dict:
    """Disable scheduled checks without changing or taking down the target website."""
    return _set_monitor_enabled(resource_id, False, caller_identity)


def _set_monitor_enabled(resource_id: int, enabled: bool, caller_identity: str | None) -> dict:
    store = Store()
    user = _caller(store, caller_identity)
    record = store.resource(resource_id, user)
    if not record or record[0]["kind"] != "health_check":
        raise ValueError("Managed health-check resource not found.")
    metadata, secret = record
    if not store.update_resource(
        resource_id,
        metadata["name"],
        metadata["kind"],
        metadata["url"],
        secret,
        metadata.get("method", "GET"),
        metadata.get("interval_minutes", 5),
        metadata.get("timeout_seconds", 10),
        metadata.get("retries", 0),
        metadata.get("alert_webhook_id"),
        enabled,
        metadata.get("failure_threshold", 1),
        None if user["role"] == "admin" else user["id"],
    ):
        raise RuntimeError("Monitor state could not be updated.")
    return {"resource_id": resource_id, "enabled": enabled}


if __name__ == "__main__":
    mcp.run(transport="stdio")
