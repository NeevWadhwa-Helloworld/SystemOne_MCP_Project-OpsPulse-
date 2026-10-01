"""Allow-listed, outbound HTTP integrations. No shell or arbitrary command execution."""

from __future__ import annotations

import ipaddress
import ssl
import socket
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx


def _public_addresses(hostname: str) -> list[str]:
    try:
        addresses = {
            info[4][0]
            for info in socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError("Resource hostname cannot be resolved.") from exc
    if not addresses:
        raise ValueError("Resource hostname cannot be resolved.")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            raise ValueError("Private or local network targets are not allowed.")
    return sorted(addresses)


def validate_url(value: str) -> str:
    value = value.strip()
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        raise ValueError("Only authenticated http(s) URLs are allowed.")
    _public_addresses(parsed.hostname)
    return value.rstrip("/") or value


def validate_hostname(value: str) -> str:
    hostname = value.strip().rstrip(".").lower()
    if not hostname or len(hostname) > 253 or "://" in hostname or "/" in hostname:
        raise ValueError("A valid public DNS hostname is required.")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("DNS diagnostics require a hostname, not an IP address.")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
           for label in hostname.split(".")):
        raise ValueError("A valid public DNS hostname is required.")
    _public_addresses(hostname)
    return hostname


async def check_ssl_cert(url: str, timeout: float = 10.0) -> dict:
    """Validate a public HTTPS certificate without following redirects."""
    if not 1 <= timeout <= 30:
        raise ValueError("timeout must be between 1 and 30 seconds.")
    parsed = urlparse(validate_url(url))
    if parsed.scheme != "https":
        raise ValueError("SSL certificate checks require an https URL.")
    host = parsed.hostname
    assert host is not None
    port = parsed.port or 443
    started = time.perf_counter()
    result: dict = {"ok": False, "hostname": host, "port": port}
    try:
        _public_addresses(host)
        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as connection:
            with context.wrap_socket(connection, server_hostname=host) as tls:
                certificate = tls.getpeercert()
                result.update({
                    "ok": True,
                    "subject": dict(item[0] for item in certificate.get("subject", ())),
                    "issuer": dict(item[0] for item in certificate.get("issuer", ())),
                    "serial_number": certificate.get("serialNumber"),
                    "protocol": tls.version(),
                })
                not_after = certificate.get("notAfter")
                if not_after:
                    expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(
                        tzinfo=timezone.utc
                    )
                    result["expires_at"] = expires.isoformat()
                    result["days_remaining"] = (expires - datetime.now(timezone.utc)).total_seconds() / 86400
    except (OSError, ssl.SSLError, ValueError) as exc:
        result["error"] = str(exc)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
    return result


def check_dns_records(hostname: str, record_types: list[str] | None = None) -> dict:
    """Resolve public A, AAAA, and CNAME records using the system resolver."""
    hostname = validate_hostname(hostname)
    requested = [item.upper() for item in (record_types or ["A", "AAAA", "CNAME"])]
    if not requested or len(requested) > 3 or any(item not in {"A", "AAAA", "CNAME"} for item in requested):
        raise ValueError("record_types may contain only A, AAAA, and CNAME (up to 3).")
    records: dict[str, list[str]] = {}
    if "A" in requested:
        try:
            records["A"] = sorted({
                info[4][0] for info in socket.getaddrinfo(
                    hostname, None, socket.AF_INET, socket.SOCK_STREAM
                )
            })
        except socket.gaierror:
            records["A"] = []
    if "AAAA" in requested:
        try:
            records["AAAA"] = sorted({
                info[4][0] for info in socket.getaddrinfo(
                    hostname, None, socket.AF_INET6, socket.SOCK_STREAM
                )
            })
        except socket.gaierror:
            records["AAAA"] = []
    if "CNAME" in requested:
        canonical = socket.getfqdn(hostname).rstrip(".").lower()
        records["CNAME"] = [] if canonical == hostname else [canonical]
    return {"ok": any(records.values()), "hostname": hostname, "records": records}


async def health_check(url: str, method: str = "GET", timeout: float = 10.0,
                       retries: int = 0) -> dict:
    url = validate_url(url)
    method = method.upper()
    if method not in {"GET", "HEAD"}:
        raise ValueError("Health checks support only GET and HEAD.")
    last_error = None
    started = time.perf_counter()
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
                response = await client.request(method, url)
            return {"ok": 200 <= response.status_code < 400,
                    "status_code": response.status_code,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2)}
        except (httpx.HTTPError, OSError) as exc:
            last_error = str(exc)
            if attempt < retries:
                continue
    return {"ok": False, "status_code": None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": last_error or "request failed"}


async def send_webhook(url: str, secret: str | None, payload: dict, timeout: float = 10.0) -> dict:
    url = validate_url(url)
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        response = await client.post(url, json=payload, headers=headers)
    return {"ok": 200 <= response.status_code < 300, "status_code": response.status_code}
