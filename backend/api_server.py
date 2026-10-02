from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Literal
from urllib.parse import urlparse

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .integrations import (
    check_dns_records,
    check_ssl_cert,
    health_check,
    send_webhook,
    validate_hostname,
    validate_url,
)
from .security import Store
from .router import explain_result, generate_monitor_report

load_dotenv()
origins = [x.strip() for x in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
try:
    store: Store | None = Store()
except Exception as err:
    print(f"Warning: Store initialization failed: {err}")
    store = None
_scheduler_task: asyncio.Task | None = None
_scheduler_started: float | None = None
_states: dict[int, bool] = {}
_notified_at: dict[int, float] = {}
_last_alert_key: dict[int, str] = {}


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=128)
    password: str = Field(min_length=12, max_length=256)


class ResourceRequest(BaseModel):
    name: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$",
    )
    kind: str = Field(default="health_check", pattern=r"^(health_check|webhook)$")
    url: str = Field(min_length=1, max_length=2048)
    secret: str | None = Field(default=None, max_length=4096)
    method: str = Field(default="GET", pattern=r"^(GET|HEAD|POST)$")
    interval_minutes: int = Field(default=5, validation_alias=AliasChoices("interval_minutes", "interval"))
    timeout_seconds: float = Field(default=10, ge=1, le=120)
    retries: int = Field(default=0, ge=0, le=5)
    alert_webhook_id: int | None = Field(default=None, ge=1)
    enabled: bool = True
    failure_threshold: int = Field(default=1, ge=1, le=10)
    events: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        try:
            return validate_url(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("interval_minutes")
    @classmethod
    def valid_interval(cls, value: int) -> int:
        if value not in {1, 5, 15, 60}:
            raise ValueError("interval must be one of 1, 5, 15, or 60 minutes.")
        return value


class CommandRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    resource_id: int | None = Field(default=None, ge=1)
    resource_name: str | None = Field(default=None, min_length=1, max_length=100)
    confirmed: bool = False
    confirmation_policy: str = Field(default="none", pattern=r"^(none|manual)$")
    chain: list["DiagnosticStep"] = Field(default_factory=list, max_length=3)
    approval_id: int | None = Field(default=None, ge=1)


class DiagnosticStep(BaseModel):
    tool: Literal["health_check", "check_ssl_cert", "check_dns_records"]
    url: str | None = Field(default=None, max_length=2048)
    hostname: str | None = Field(default=None, max_length=253)
    timeout: float = Field(default=10, ge=1, le=30)
    record_types: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("url")
    @classmethod
    def diagnostic_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_url(value)

    @field_validator("hostname")
    @classmethod
    def diagnostic_hostname(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_hostname(value)


def command_operation(prompt: str) -> str | None:
    """Map a monitoring request to one explicit, generic operation."""
    text = prompt.casefold()
    if any(phrase in text for phrase in ("create webhook", "add webhook", "configure webhook")):
        return None
    if any(word in text for word in ("history", "recent checks", "past checks")):
        return "monitor_history"
    if any(word in text for word in ("disable", "pause", "stop monitoring")):
        return "disable_monitor"
    if any(word in text for word in ("enable", "resume", "start monitoring")):
        return "enable_monitor"
    if any(word in text for word in ("alert", "notify", "send webhook")):
        return "send_alert"
    if any(phrase in text for phrase in (
        "generate report",
        "generate a report",
        "create report",
        "create a report",
        "monitoring report",
        "postmortem",
        "post-mortem",
    )):
        return "monitor_report"
    if any(word in text for word in ("health", "availability", "available", "latency", "check")):
        return "health_check"
    if any(word in text for word in ("summary", "uptime", "average latency", "avg latency")):
        return "monitor_status"
    return None


async def run_diagnostic_chain(
    steps: list[DiagnosticStep], resource: tuple[dict[str, Any], str | None] | None
) -> list[dict[str, Any]]:
    """Run only the caller-supplied, bounded read-only diagnostic steps."""
    results: list[dict[str, Any]] = []
    metadata = resource[0] if resource else {}
    for step in steps:
        if step.tool == "health_check":
            if metadata:
                if metadata.get("kind") != "health_check":
                    raise HTTPException(422, "health_check steps require a health-check resource.")
                target = metadata["url"]
                method = metadata.get("method", "GET")
                timeout = float(metadata.get("timeout_seconds", 10))
                retries = int(metadata.get("retries", 0))
            elif step.url:
                target = step.url
                method = "GET"
                timeout = step.timeout
                retries = 0
            else:
                raise HTTPException(422, "health_check requires a managed resource or URL.")
            result = await health_check(target, method, timeout, retries)
        elif step.tool == "check_ssl_cert":
            target = step.url or (metadata.get("url") if metadata else None)
            if not target:
                raise HTTPException(422, "check_ssl_cert requires an HTTPS URL.")
            result = await check_ssl_cert(target, step.timeout)
        else:
            target = step.hostname
            if not target and metadata.get("url"):
                target = urlparse(metadata["url"]).hostname
            if not target:
                raise HTTPException(422, "check_dns_records requires a hostname.")
            result = check_dns_records(target, step.record_types or None)
        results.append({
            "tool": step.tool,
            "status": "passed" if result.get("ok") else "failed",
            "ok": bool(result.get("ok")),
            "result": result,
        })
    return results


def infer_diagnostic_chain(
    prompt: str, resource: tuple[dict[str, Any], str | None] | None
) -> list[DiagnosticStep]:
    """Translate only explicit read-only diagnostic words into a bounded chain."""
    text = prompt.casefold()
    metadata = resource[0] if resource else {}
    url_match = re.search(r"https?://[^\s,]+", prompt, re.IGNORECASE)
    explicit_url = url_match.group(0).rstrip(").,") if url_match else None
    steps: list[DiagnosticStep] = []

    if any(word in text for word in ("health", "availability", "available", "latency")):
        steps.append(DiagnosticStep(tool="health_check", url=explicit_url))
    if any(word in text for word in ("ssl", "tls", "certificate", "cert")):
        steps.append(DiagnosticStep(
            tool="check_ssl_cert",
            url=explicit_url or metadata.get("url"),
        ))
    if any(word in text for word in ("dns", "domain records", "dns records")):
        hostname = None
        host_match = re.search(
            r"\b(?:hostname|host|domain)\s+(?:of\s+)?([A-Za-z0-9][A-Za-z0-9.-]+)",
            prompt,
            re.IGNORECASE,
        )
        if host_match:
            hostname = host_match.group(1).rstrip(".")
        elif explicit_url:
            hostname = urlparse(explicit_url).hostname
        elif metadata.get("url"):
            hostname = urlparse(metadata["url"]).hostname
        steps.append(DiagnosticStep(tool="check_dns_records", hostname=hostname))
    if not any(word in text for word in ("ssl", "tls", "certificate", "cert", "dns", "domain records", "dns records")):
        return []
    return steps[:3]


def current_user(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if store is None:
        raise HTTPException(503, "APP_MASTER_KEY is not configured.")
    token = authorization.removeprefix("Bearer ").strip() if authorization else ""
    user = store.user_for_token(token)
    if not user:
        raise HTTPException(401, "Authentication required.")
    return user


def require(*roles: str):
    def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(403, "Insufficient permissions.")
        return user
    return dependency


def resource_response(row: dict[str, Any], user: dict[str, Any] | None = None) -> dict[str, Any]:
    result = dict(row)
    result["enabled"] = bool(result.get("enabled", 1))
    if result.get("kind") == "health_check" and store is not None:
        latest = store.latest_check(int(result["id"]), user)
        if latest:
            result["last_status"] = latest["status"]
            result["status_code"] = latest["status_code"]
            result["latency_ms"] = latest["latency_ms"]
            result["last_checked_at"] = latest["checked_at"]
    result.pop("created_at", None)
    return result


async def perform_check(resource_id: int, notify: bool = True, user: dict[str, Any] | None = None) -> dict[str, Any]:
    if store is None or not (record := store.resource(resource_id, user)):
        raise HTTPException(404, "Resource not found.")
    metadata, _ = record
    if metadata["kind"] != "health_check":
        raise HTTPException(400, "Only health-check resources can be monitored.")
    result = await health_check(metadata["url"], metadata.get("method", "GET"),
                                float(metadata.get("timeout_seconds", 10)),
                                int(metadata.get("retries", 0)))
    history_id = store.record_check(resource_id, result)
    current_up = bool(result.get("ok"))
    history = store.history(resource_id, 20, user)
    previous = history[1]["status"] == "up" if len(history) > 1 else _states.get(resource_id)
    _states[resource_id] = current_up
    webhook_id = metadata.get("alert_webhook_id")
    failures = 0
    for item in history:
        if item["status"] != "down":
            break
        failures += 1
    prior_failures = 0
    for item in history[1:]:
        if item["status"] != "down":
            break
        prior_failures += 1
    threshold = int(metadata.get("failure_threshold", 1))
    transitioned_down = not current_up and failures >= threshold and prior_failures < threshold
    recovered = current_up and previous is False
    if notify and webhook_id and (transitioned_down or recovered):
        now = time.time()
        cooldown = int(os.getenv("MONITOR_ALERT_COOLDOWN_SECONDS", "300"))
        event = "recovery" if recovered else "failure"
        key = json.dumps({"event": event, "status_code": result.get("status_code"),
                          "error": result.get("error", "")}, sort_keys=True)
        if (now - _notified_at.get(resource_id, 0) >= cooldown
                or _last_alert_key.get(resource_id) != key):
            hook = store.resource(int(webhook_id), user)
            if hook and hook[0]["kind"] == "webhook" and (user is None or hook[0].get("owner_user_id") == metadata.get("owner_user_id") or user.get("role") == "admin"):
                await send_webhook(hook[0]["url"], hook[1], {
                    "resource_id": resource_id, "status": event,
                    "result": result, "history_id": history_id,
                })
                _notified_at[resource_id] = now
                _last_alert_key[resource_id] = key
    return {**result, "history_id": history_id, "resource_id": resource_id}


async def monitor_loop() -> None:
    while True:
        if store:
            for row in store.resources_by_kind("health_check"):
                if row.get("enabled", 1):
                    last = store.latest_check(row["id"])
                    if not last or (time.time() - _iso_epoch(last["checked_at"])) >= row["interval_minutes"] * 60:
                        try:
                            await perform_check(row["id"])
                        except Exception:
                            pass
        await asyncio.sleep(15)


def _iso_epoch(value: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(value).timestamp()


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _scheduler_task, _scheduler_started
    if store:
        store.bootstrap(os.getenv("BOOTSTRAP_ADMIN_USERNAME"), os.getenv("BOOTSTRAP_ADMIN_PASSWORD"))
    _scheduler_started = time.time()
    _scheduler_task = asyncio.create_task(monitor_loop())
    try:
        yield
    finally:
        _scheduler_task.cancel()
        try:
            await _scheduler_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="OpsPulse Engine API", version="3.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=origins,
                   allow_methods=["GET", "POST", "PUT", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
                   allow_credentials=True)


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/auth/login")
async def login(body: LoginRequest, request: Request) -> dict[str, Any]:
    if store is None:
        raise HTTPException(503, "APP_MASTER_KEY is not configured.")
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    result = store.authenticate(body.username, body.password)
    if not result:
        store.audit(body.username, request_id, "login", "failure")
        raise HTTPException(401, "Invalid username or password.")
    token, user = result
    store.audit(user["username"], request_id, "login", "success")
    return {"token": token, "user": user}


@app.post("/api/auth/register", status_code=201)
async def register(body: RegisterRequest, request: Request) -> dict[str, Any]:
    if store is None:
        raise HTTPException(503, "APP_MASTER_KEY is not configured.")
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    try:
        user = store.register_user(body.username, body.password)
    except ValueError as exc:
        store.audit(body.username, request_id, "register", "failure", str(exc))
        raise HTTPException(422, str(exc)) from exc
    store.audit(user["username"], request_id, "register", "success", "viewer account created")
    return {
        "message": "Account created successfully. Sign in to continue.",
        "user": user,
    }


@app.get("/api/auth/me")
async def me(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"user": user}


@app.get("/api/resources")
async def list_resources(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return {"resources": [resource_response(r, user) for r in store.resources(user)] if store else []}


@app.get("/api/health-checks")
async def list_health_checks(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    resources = store.resources(user) if store else []
    return {"items": [resource_response(r, user) for r in resources if r["kind"] == "health_check"]}


@app.get("/api/webhooks")
async def list_webhooks(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    resources = store.resources(user) if store else []
    return {"items": [resource_response(r, user) for r in resources if r["kind"] == "webhook"]}


async def create_resource(body: ResourceRequest, request: Request, user: dict[str, Any], kind: str | None = None):
    if store is None:
        raise HTTPException(503, "Store unavailable.")
    actual_kind = kind or body.kind
    method = "POST" if actual_kind == "webhook" else body.method
    if actual_kind == "health_check" and method == "POST":
        raise HTTPException(422, "Health checks support GET or HEAD only.")
    if body.alert_webhook_id:
        hook = store.resource(body.alert_webhook_id, user)
        if not hook or hook[0]["kind"] != "webhook" or (user["role"] != "admin" and hook[0].get("owner_user_id") != user["id"]):
            raise HTTPException(422, "Alert webhook must belong to the same tenant.")
    try:
        resource_id = store.put_resource(body.name, actual_kind, body.url, body.secret, method,
                                         body.interval_minutes, body.timeout_seconds, body.retries,
                                         body.alert_webhook_id, body.enabled, body.failure_threshold, user["id"])
    except Exception as exc:
        raise HTTPException(409, "Resource could not be created: name may already exist.") from exc
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "resource.create", "success", body.name)
    return {"id": resource_id, "name": body.name, "kind": actual_kind, "url": body.url}


@app.post("/api/resources")
async def create(body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await create_resource(body, request, user)


@app.post("/api/health-checks")
async def create_health(body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await create_resource(body, request, user, "health_check")


@app.post("/api/webhooks")
async def create_hook(body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await create_resource(body, request, user, "webhook")


async def update_resource(resource_id: int, body: ResourceRequest, request: Request, user: dict[str, Any], kind: str | None = None):
    if store is None or not store.resource(resource_id, user):
        raise HTTPException(404, "Resource not found.")
    actual_kind = kind or body.kind
    method = "POST" if actual_kind == "webhook" else body.method
    if actual_kind == "health_check" and method == "POST":
        raise HTTPException(422, "Health checks support GET or HEAD only.")
    if body.alert_webhook_id:
        hook = store.resource(body.alert_webhook_id, user)
        if not hook or hook[0]["kind"] != "webhook" or (user["role"] != "admin" and hook[0].get("owner_user_id") != user["id"]):
            raise HTTPException(422, "Alert webhook must belong to the same tenant.")
    if not store.update_resource(resource_id, body.name, actual_kind, body.url, body.secret, method,
                                 body.interval_minutes, body.timeout_seconds, body.retries,
                                 body.alert_webhook_id, body.enabled, body.failure_threshold,
                                 None if user["role"] == "admin" else user["id"]):
        raise HTTPException(404, "Resource not found.")
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "resource.update", "success", str(resource_id))
    return {"id": resource_id, "name": body.name, "url": body.url, "enabled": body.enabled}


@app.put("/api/resources/{resource_id}")
async def update(resource_id: int, body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await update_resource(resource_id, body, request, user)


@app.put("/api/health-checks/{resource_id}")
async def update_health(resource_id: int, body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await update_resource(resource_id, body, request, user, "health_check")


@app.put("/api/webhooks/{resource_id}")
async def update_hook(resource_id: int, body: ResourceRequest, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    return await update_resource(resource_id, body, request, user, "webhook")


@app.delete("/api/resources/{resource_id}")
@app.delete("/api/health-checks/{resource_id}")
@app.delete("/api/webhooks/{resource_id}")
async def delete(resource_id: int, request: Request, user: dict[str, Any] = Depends(require("admin"))):
    if store is None or not store.delete_resource(resource_id, None if user["role"] == "admin" else user["id"]):
        raise HTTPException(404, "Resource not found.")
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "resource.delete", "success", str(resource_id))
    return {"deleted": True}


@app.post("/api/resources/{resource_id}/run")
async def run_resource(resource_id: int, request: Request, user: dict[str, Any] = Depends(require("admin", "operator"))):
    if store is None or not (record := store.resource(resource_id, user)):
        raise HTTPException(404, "Resource not found.")
    metadata, secret = record
    if metadata["kind"] == "webhook":
        result = await send_webhook(metadata["url"], secret, {"source": "opspulse", "resource_id": resource_id})
    else:
        result = await perform_check(resource_id, notify=False, user=user)
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "resource.run", "success", str(resource_id))
    return {
        "tool_selected": "send_alert" if metadata["kind"] == "webhook" else "health_check",
        "result": result,
        "natural_language": explain_result(result),
    }


@app.get("/api/resources/{resource_id}/history")
async def history(resource_id: int, limit: int = 100, _user: dict[str, Any] = Depends(current_user)):
    if store is None or not store.resource(resource_id, _user):
        raise HTTPException(404, "Resource not found.")
    return {"history": store.history(resource_id, limit, _user)}


@app.get("/api/resources/{resource_id}/summary")
async def summary(resource_id: int, _user: dict[str, Any] = Depends(current_user)):
    if store is None or not store.resource(resource_id, _user):
        raise HTTPException(404, "Resource not found.")
    return store.summary(resource_id, _user)


@app.get("/api/scheduler/status")
async def scheduler_status(_user: dict[str, Any] = Depends(current_user)):
    return {"running": _scheduler_task is not None and not _scheduler_task.done(),
            "started_at": _scheduler_started, "poll_seconds": 15}


@app.get("/api/approvals")
async def list_approvals(_user: dict[str, Any] = Depends(require("admin"))):
    return {"approvals": store.approvals() if store else []}


@app.post("/api/approvals/{approval_id}/approve")
async def approve(approval_id: int, request: Request,
                  user: dict[str, Any] = Depends(require("admin"))):
    if store is None or not store.decide_approval(approval_id, "approved", user["username"]):
        raise HTTPException(409, "Approval is missing, expired, or already decided.")
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())),
                "approval.approve", "success", str(approval_id), user["role"])
    return {"approval_id": approval_id, "status": "approved"}


@app.post("/api/approvals/{approval_id}/reject")
async def reject(approval_id: int, request: Request,
                 user: dict[str, Any] = Depends(require("admin"))):
    if store is None or not store.decide_approval(approval_id, "rejected", user["username"]):
        raise HTTPException(409, "Approval is missing, expired, or already decided.")
    store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())),
                "approval.reject", "success", str(approval_id), user["role"])
    return {"approval_id": approval_id, "status": "rejected"}


@app.post("/api/command")
async def command(body: CommandRequest, request: Request, user: dict[str, Any] = Depends(require("admin", "operator"))):
    if store is None:
        raise HTTPException(503, "Store unavailable.")
    record = store.resource(body.resource_id, user)
    if body.resource_name and not record:
        with store.connection() as db:
            if user["role"] == "admin":
                row = db.execute("SELECT id FROM resources WHERE name=?", (body.resource_name,)).fetchone()
            else:
                row = db.execute("SELECT id FROM resources WHERE name=? AND owner_user_id=?", (body.resource_name, user["id"])).fetchone()
        record = store.resource(row["id"], user) if row else None
    inferred_chain = body.chain or infer_diagnostic_chain(body.prompt, record)
    if inferred_chain:
        try:
            results = await run_diagnostic_chain(inferred_chain, record)
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        store.audit(
            user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())),
            "command.chain", "success", ",".join(step.tool for step in inferred_chain),
        )
        response: dict[str, Any] = {
            "tool_selected": "diagnostic_chain",
            "chain": results,
            "result": results,
            "natural_language": explain_result(results),
            "workflow": {
                "status": "passed" if all(item["result"].get("ok", False) for item in results) else "failed",
                "steps": len(results),
                "completed_steps": sum(1 for item in results if item["result"].get("ok", False)),
                "failed_steps": sum(1 for item in results if not item["result"].get("ok", False)),
            },
        }
        if record:
            response["arguments"] = {
                "resource_id": record[0]["id"],
                "resource_name": record[0]["name"],
            }
        return response
    if not record:
        raise HTTPException(422, "Specify a managed resource by resource_id or resource_name.")
    metadata, secret = record
    operation = command_operation(body.prompt)
    if operation is None:
        raise HTTPException(
            422,
            "This command is not a monitoring operation. Use the Webhooks tab to "
            "create a webhook, or use health check, status, history, enable "
            "monitoring, disable monitoring, generate a report, or send alert.",
        )
    if operation in {"monitor_history", "monitor_status", "monitor_report", "health_check", "enable_monitor", "disable_monitor"} \
            and metadata["kind"] != "health_check":
        raise HTTPException(422, "This monitoring operation requires a health-check resource.")
    alert_record = record
    if operation == "send_alert" and metadata["kind"] == "health_check":
        webhook_id = metadata.get("alert_webhook_id")
        alert_record = store.resource(int(webhook_id), user) if webhook_id else None
        if not alert_record or alert_record[0]["kind"] != "webhook":
            raise HTTPException(
                422,
                "This monitor has no alert webhook configured. Select a webhook "
                "resource or configure one in the Webhooks tab.",
            )
        metadata, secret = alert_record
    if operation == "send_alert" and metadata["kind"] != "webhook":
        raise HTTPException(422, "Alert delivery requires a webhook resource.")
    if operation in {"send_alert", "disable_monitor", "enable_monitor"}:
        consumed = (
            store.consume_approval(body.approval_id, operation, metadata["id"], user["username"])
            if body.approval_id else False
        )
        if not consumed:
            pending = store.create_approval(operation, metadata["id"], user["username"], user["role"], body.prompt)
            store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())),
                        "approval.request", "pending", metadata["name"], user["role"],
                        {"approval_id": pending["id"], "resource_id": metadata["id"]})
            raise HTTPException(409, detail={"approval_required": True, "approval": pending})
    try:
        if operation == "health_check":
            result = await perform_check(metadata["id"], notify=False, user=user)
        elif operation == "monitor_history":
            result = {"resource_id": metadata["id"], "history": store.history(metadata["id"], 50, user)}
        elif operation == "monitor_status":
            result = {
                "resource_id": metadata["id"],
                "latest": store.latest_check(metadata["id"], user),
                "summary": store.summary(metadata["id"], user),
            }
        elif operation == "monitor_report":
            summary = store.summary(metadata["id"], user)
            history = store.history(metadata["id"], 50, user)
            result = {
                "resource_id": metadata["id"],
                "resource_name": metadata["name"],
                "summary": summary,
                "report": generate_monitor_report(metadata["name"], summary, history),
                "history": history,
            }
        elif operation in {"enable_monitor", "disable_monitor"}:
            enabled = operation == "enable_monitor"
            if not store.update_resource(
                metadata["id"], metadata["name"], metadata["kind"], metadata["url"], secret,
                metadata.get("method", "GET"), metadata.get("interval_minutes", 5),
                metadata.get("timeout_seconds", 10), metadata.get("retries", 0),
                metadata.get("alert_webhook_id"), enabled,
                metadata.get("failure_threshold", 1),
                None if user["role"] == "admin" else user["id"],
            ):
                raise RuntimeError("Monitor state could not be updated.")
            result = {"resource_id": metadata["id"], "enabled": enabled}
        else:
            result = await send_webhook(metadata["url"], secret, {"prompt": body.prompt})
        store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "command", "success", metadata["name"], user["role"], {"operation": operation})
        return {
            "tool_selected": operation,
            "arguments": {"resource_id": metadata["id"], "resource_name": metadata["name"]},
            "result": result,
            "natural_language": result.get("report") if operation == "monitor_report" else explain_result(result),
            "history_id": result.get("history_id"),
        }
    except Exception as exc:
        store.audit(user["username"], request.headers.get("X-Request-ID", str(uuid.uuid4())), "command", "failure", str(exc), user["role"], {"operation": operation})
        raise HTTPException(422, "Integration request failed.") from exc


@app.get("/api/logs")
async def logs(_user: dict[str, Any] = Depends(require("admin"))):
    if store is None:
        return {"logs": []}
    with store.connection() as db:
        rows = db.execute("SELECT actor,request_id,actor_role,metadata,action,status,detail,created_at FROM audit_logs ORDER BY id DESC LIMIT 100").fetchall()
    return {"logs": [dict(row) for row in rows]}
