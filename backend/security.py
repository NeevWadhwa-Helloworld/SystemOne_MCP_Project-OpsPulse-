"""Local authentication, encrypted secrets, and audit persistence for OpsPulse."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

ROLES = {"admin", "operator", "viewer"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Password must be between 12 and 256 characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        _, salt, expected = encoded.split("$")
        actual = hashlib.scrypt(
            password.encode(), salt=base64.urlsafe_b64decode(salt), n=2**14, r=8, p=1
        )
        return hmac.compare_digest(
            actual, base64.urlsafe_b64decode(expected)
        )
    except (ValueError, TypeError):
        return False


class TursoHttpCursorWrapper:
    def __init__(self, rows: list[dict[str, Any]], columns: list[str], lastrowid: int | None, rowcount: int):
        self._rows = rows
        self.columns = columns
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._index = 0

    def fetchone(self) -> dict[str, Any] | None:
        if self._index < len(self._rows):
            r = self._rows[self._index]
            self._index += 1
            return r
        return None

    def fetchall(self) -> list[dict[str, Any]]:
        r = self._rows[self._index:]
        self._index = len(self._rows)
        return r

    def __iter__(self):
        return iter(self._rows)


class TursoHttpClient:
    def __init__(self, url: str, token: str):
        self.url = url.replace("libsql://", "https://").rstrip("/")
        self.token = token.strip()
        self.endpoint = f"{self.url}/v2/pipeline"
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def _convert_val(self, v: Any) -> dict[str, Any]:
        if v is None:
            return {"type": "null"}
        if isinstance(v, bool):
            return {"type": "integer", "value": "1" if v else "0"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        if isinstance(v, bytes):
            return {"type": "blob", "base64": base64.b64encode(v).decode()}
        return {"type": "text", "value": str(v)}

    def _parse_val(self, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        t = v.get("type")
        if t == "null":
            return None
        if t in ("integer", "text"):
            val = v.get("value")
            return int(val) if t == "integer" and val is not None else val
        if t == "float":
            return v.get("value")
        if t == "blob":
            return base64.b64decode(v.get("base64", ""))
        return v.get("value")

    def execute(self, sql: str, params: tuple | list = ()):
        import httpx
        args = [self._convert_val(p) for p in params]
        body = {"requests": [{"type": "execute", "stmt": {"sql": sql, "args": args}}, {"type": "close"}]}
        with httpx.Client(timeout=15.0) as client:
            res = client.post(self.endpoint, json=body, headers=self.headers)
            res.raise_for_status()
            data = res.json()
            results = data.get("results", [])
            if not results:
                raise RuntimeError("No result from Turso DB.")
            first = results[0]
            if first.get("type") == "error":
                raise RuntimeError(first.get("error", {}).get("message", "Turso query failed."))
            stmt_res = first.get("response", {}).get("result", {})
            cols = [c.get("name") for c in stmt_res.get("cols", [])]
            raw_rows = stmt_res.get("rows", [])
            rows = []
            for r in raw_rows:
                row_dict = {}
                for col_name, cell in zip(cols, r):
                    row_dict[col_name] = self._parse_val(cell)
                rows.append(row_dict)
            last_id = stmt_res.get("last_insert_rowid")
            lastrowid = int(last_id) if last_id is not None else None
            affected = stmt_res.get("affected_row_count", 0)
            return TursoHttpCursorWrapper(rows, cols, lastrowid, affected)

    def executescript(self, script: str):
        statements = [s.strip() for s in script.split(";") if s.strip()]
        for stmt in statements:
            self.execute(stmt)


class Store:
    def __init__(self, path: str | Path | None = None, master_key: str | None = None):
        self.path = str(path or os.getenv("OPSPULSE_DB_PATH", "opspulse.db"))
        self.turso_url = os.getenv("TURSO_DATABASE_URL", "") or os.getenv("LIBSQL_DATABASE_URL", "")
        self.turso_auth_token = os.getenv("TURSO_AUTH_TOKEN", "") or os.getenv("LIBSQL_AUTH_TOKEN", "")
        if not self.turso_url and self.path.startswith(("libsql://", "https://", "http://")):
            self.turso_url = self.path
        self._memory_uri = "file:opspulse_shared?mode=memory&cache=shared" if self.path == ":memory:" else None
        key = master_key or os.getenv("APP_MASTER_KEY", "")
        if not key:
            raise RuntimeError("APP_MASTER_KEY must be configured.")
        try:
            self.fernet = Fernet(key.encode())
        except Exception as exc:
            raise RuntimeError("APP_MASTER_KEY must be a valid Fernet key.") from exc
        self._anchor = sqlite3.connect(self._memory_uri, uri=True) if self._memory_uri and not self.turso_url else None
        if not self.turso_url and self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def connection(self) -> Any:
        if self.turso_url:
            return TursoHttpClient(self.turso_url, self.turso_auth_token)
        conn = sqlite3.connect(self._memory_uri, uri=True) if self._memory_uri else sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


    def _init(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                  password_hash TEXT NOT NULL, role TEXT NOT NULL,
                  disabled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
                  expires_at REAL NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resources (
                  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
                  owner_user_id INTEGER,
                  kind TEXT NOT NULL, url TEXT NOT NULL, secret BLOB,
                  method TEXT NOT NULL DEFAULT 'GET',
                  interval_minutes INTEGER NOT NULL DEFAULT 5,
                  timeout_seconds REAL NOT NULL DEFAULT 10,
                  retries INTEGER NOT NULL DEFAULT 0,
                  alert_webhook_id INTEGER,
                  enabled INTEGER NOT NULL DEFAULT 1,
                  failure_threshold INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS check_history (
                  id INTEGER PRIMARY KEY, resource_id INTEGER NOT NULL,
                  status TEXT NOT NULL, status_code INTEGER, latency_ms REAL,
                  error TEXT, checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_logs (
                  id INTEGER PRIMARY KEY, actor TEXT, request_id TEXT,
                  actor_role TEXT, metadata TEXT,
                  action TEXT NOT NULL, status TEXT NOT NULL,
                  detail TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                  id INTEGER PRIMARY KEY, action TEXT NOT NULL, resource_id INTEGER NOT NULL,
                  requested_by TEXT NOT NULL, requested_role TEXT NOT NULL,
                  status TEXT NOT NULL DEFAULT 'pending', reason TEXT,
                  created_at TEXT NOT NULL, expires_at REAL NOT NULL,
                  decided_by TEXT, decided_at TEXT
                );
                """
            )
            columns = {r["name"] for r in db.execute("PRAGMA table_info(resources)")}
            migrations = {
                "owner_user_id": "ALTER TABLE resources ADD COLUMN owner_user_id INTEGER",
                "method": "ALTER TABLE resources ADD COLUMN method TEXT NOT NULL DEFAULT 'GET'",
                "interval_minutes": "ALTER TABLE resources ADD COLUMN interval_minutes INTEGER NOT NULL DEFAULT 5",
                "timeout_seconds": "ALTER TABLE resources ADD COLUMN timeout_seconds REAL NOT NULL DEFAULT 10",
                "retries": "ALTER TABLE resources ADD COLUMN retries INTEGER NOT NULL DEFAULT 0",
                "alert_webhook_id": "ALTER TABLE resources ADD COLUMN alert_webhook_id INTEGER",
                "enabled": "ALTER TABLE resources ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1",
                "failure_threshold": "ALTER TABLE resources ADD COLUMN failure_threshold INTEGER NOT NULL DEFAULT 1",
            }
            for name, sql in migrations.items():
                if name not in columns:
                    db.execute(sql)
            audit_columns = {r["name"] for r in db.execute("PRAGMA table_info(audit_logs)")}
            for name, sql in {
                "actor_role": "ALTER TABLE audit_logs ADD COLUMN actor_role TEXT",
                "metadata": "ALTER TABLE audit_logs ADD COLUMN metadata TEXT",
            }.items():
                if name not in audit_columns:
                    db.execute(sql)

    def bootstrap(self, username: str | None, password: str | None) -> None:
        if not username or not password:
            return
        with self.connection() as db:
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is None:
                db.execute(
                    "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                    (username.strip(), hash_password(password), "admin", _now()),
                )
            admin = db.execute("SELECT id FROM users WHERE username=?", (username.strip(),)).fetchone()
            if admin:
                # Legacy rows are private until claimed by the configured bootstrap admin.
                db.execute("UPDATE resources SET owner_user_id=? WHERE owner_user_id IS NULL", (admin["id"],))

    def register_user(self, username: str, password: str) -> dict[str, Any]:
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("Username is required.")
        if not 3 <= len(normalized_username) <= 128:
            raise ValueError("Username must be between 3 and 128 characters.")
        if not all(char.isalnum() or char in "._-@" for char in normalized_username):
            raise ValueError("Username may contain letters, numbers, ., _, -, and @ only.")
        password_hash = hash_password(password)
        try:
            with self.connection() as db:
                cursor = db.execute(
                    "INSERT INTO users(username,password_hash,role,created_at) VALUES(?,?,?,?)",
                    (normalized_username, password_hash, "viewer", _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("An account with that username already exists.") from exc
        return {"id": int(cursor.lastrowid), "username": normalized_username, "role": "viewer"}

    def authenticate(self, username: str, password: str) -> tuple[str, dict[str, Any]] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT * FROM users WHERE username=? AND disabled=0", (username.strip(),)
            ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return None
        token = secrets.token_urlsafe(32)
        with self.connection() as db:
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + 43200, _now()),
            )
        return token, {"id": row["id"], "username": row["username"], "role": row["role"]}

    def user_for_token(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with self.connection() as db:
            row = db.execute(
                """SELECT u.id,u.username,u.role FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>? AND u.disabled=0""",
                (hashlib.sha256(token.encode()).hexdigest(), time.time()),
            ).fetchone()
        return dict(row) if row else None

    def user_by_identity(self, identity: str) -> dict[str, Any] | None:
        with self.connection() as db:
            row = db.execute(
                "SELECT id,username,role FROM users WHERE (username=? OR CAST(id AS TEXT)=?) AND disabled=0",
                (identity.strip(), identity.strip()),
            ).fetchone()
        return dict(row) if row else None

    def audit(self, actor: str | None, request_id: str, action: str, status: str,
              detail: str = "", actor_role: str | None = None,
              metadata: dict[str, Any] | None = None) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT INTO audit_logs(actor,request_id,actor_role,metadata,action,status,detail,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (actor, request_id, actor_role,
                 __import__("json").dumps(metadata or {}, separators=(",", ":"))[:2000],
                 action, status, detail[:1000], _now()),
            )

    def put_resource(self, name: str, kind: str, url: str, secret: str | None,
                     method: str = "GET", interval_minutes: int = 5,
                     timeout_seconds: float = 10, retries: int = 0,
                     alert_webhook_id: int | None = None, enabled: bool = True,
                     failure_threshold: int = 1, owner_user_id: int | None = None) -> int:
        encrypted = self.fernet.encrypt(secret.encode()) if secret else None
        with self.connection() as db:
            cur = db.execute(
                """INSERT INTO resources(name,owner_user_id,kind,url,secret,method,interval_minutes,
                   timeout_seconds,retries,alert_webhook_id,enabled,failure_threshold,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (name, owner_user_id, kind, url, encrypted, method, interval_minutes, timeout_seconds,
                 retries, alert_webhook_id, int(enabled), failure_threshold, _now()),
            )
            return int(cur.lastrowid)

    def resources(self, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            query = "SELECT id,name,owner_user_id,kind,url,method,interval_minutes,timeout_seconds,retries,alert_webhook_id,enabled,failure_threshold,created_at FROM resources"
            params: tuple[Any, ...] = ()
            if user and user.get("role") != "admin":
                query += " WHERE owner_user_id=?"; params = (user["id"],)
            query += " ORDER BY id"
            return [dict(row) for row in db.execute(query, params)]

    def resources_by_kind(self, kind: str, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            query = "SELECT id,name,owner_user_id,kind,url,method,interval_minutes,timeout_seconds,retries,alert_webhook_id,enabled,failure_threshold,created_at FROM resources WHERE kind=?"
            params: tuple[Any, ...] = (kind,)
            if user and user.get("role") != "admin":
                query += " AND owner_user_id=?"; params += (user["id"],)
            query += " ORDER BY id"
            return [dict(row) for row in db.execute(query, params)]

    def update_resource(self, resource_id: int, name: str, kind: str, url: str, secret: str | None,
                        method: str = "GET", interval_minutes: int = 5,
                        timeout_seconds: float = 10, retries: int = 0,
                        alert_webhook_id: int | None = None, enabled: bool = True,
                        failure_threshold: int = 1, owner_user_id: int | None = None) -> bool:
        encrypted = self.fernet.encrypt(secret.encode()) if secret else None
        with self.connection() as db:
            if secret:
                cur = db.execute(
                    "UPDATE resources SET name=?,kind=?,url=?,secret=?,method=?,interval_minutes=?,timeout_seconds=?,retries=?,alert_webhook_id=?,enabled=?,failure_threshold=? WHERE id=? AND (? IS NULL OR owner_user_id=?)",
                    (name, kind, url, encrypted, method, interval_minutes, timeout_seconds, retries, alert_webhook_id, int(enabled), failure_threshold, resource_id, owner_user_id, owner_user_id),
                )
            else:
                cur = db.execute(
                    "UPDATE resources SET name=?,kind=?,url=?,method=?,interval_minutes=?,timeout_seconds=?,retries=?,alert_webhook_id=?,enabled=?,failure_threshold=? WHERE id=? AND (? IS NULL OR owner_user_id=?)",
                    (name, kind, url, method, interval_minutes, timeout_seconds, retries, alert_webhook_id, int(enabled), failure_threshold, resource_id, owner_user_id, owner_user_id),
                )
            return cur.rowcount == 1

    def delete_resource(self, resource_id: int, owner_user_id: int | None = None) -> bool:
        with self.connection() as db:
            cur = db.execute("DELETE FROM resources WHERE id=? AND (? IS NULL OR owner_user_id=?)", (resource_id, owner_user_id, owner_user_id))
            return cur.rowcount == 1

    def resource(self, resource_id: int, user: dict[str, Any] | None = None) -> tuple[dict[str, Any], str | None] | None:
        with self.connection() as db:
            query = "SELECT * FROM resources WHERE id=?"; params: tuple[Any, ...] = (resource_id,)
            if user and user.get("role") != "admin":
                query += " AND owner_user_id=?"; params += (user["id"],)
            row = db.execute(query, params).fetchone()
        if not row:
            return None
        secret = None
        if row["secret"]:
            try:
                secret = self.fernet.decrypt(row["secret"]).decode()
            except InvalidToken as exc:
                raise RuntimeError("Stored resource secret cannot be decrypted.") from exc
        return dict(row), secret

    def record_check(self, resource_id: int, result: dict[str, Any]) -> int:
        status = "up" if result.get("ok") else "down"
        with self.connection() as db:
            cur = db.execute(
                "INSERT INTO check_history(resource_id,status,status_code,latency_ms,error,checked_at) VALUES(?,?,?,?,?,?)",
                (resource_id, status, result.get("status_code"), result.get("latency_ms"),
                 result.get("error"), _now()),
            )
            return int(cur.lastrowid)

    def history(self, resource_id: int, limit: int = 100, user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.connection() as db:
            owner_sql = ""
            params: tuple[Any, ...] = (resource_id, min(limit, 500))
            if user and user.get("role") != "admin":
                owner_sql = " AND r.owner_user_id=?"; params = (resource_id, user["id"], min(limit, 500))
            return [dict(r) for r in db.execute(
                f"SELECT h.id,h.resource_id,h.status,h.status_code,h.latency_ms,h.error,h.checked_at FROM check_history h JOIN resources r ON r.id=h.resource_id WHERE h.resource_id=?{owner_sql} ORDER BY h.id DESC LIMIT ?",
                params)]

    def latest_check(self, resource_id: int, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        rows = self.history(resource_id, 1, user)
        return rows[0] if rows else None

    def summary(self, resource_id: int, user: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.connection() as db:
            owner_sql = ""
            params: tuple[Any, ...] = (resource_id,)
            if user and user.get("role") != "admin":
                owner_sql = " AND r.owner_user_id=?"; params = (resource_id, user["id"])
            row = db.execute(
                f"SELECT COUNT(*) total, SUM(h.status='up') up, AVG(h.latency_ms) latency FROM check_history h JOIN resources r ON r.id=h.resource_id WHERE h.resource_id=?{owner_sql}",
                params).fetchone()
        total = row["total"] or 0
        return {"total_checks": total, "up_checks": row["up"] or 0,
                "uptime_percent": round((row["up"] or 0) * 100 / total, 2) if total else None,
                "avg_latency_ms": row["latency"]}

    def create_approval(self, action: str, resource_id: int, username: str,
                        role: str, reason: str = "", ttl_seconds: int = 900) -> dict[str, Any]:
        created = _now()
        expires = time.time() + ttl_seconds
        with self.connection() as db:
            cur = db.execute(
                """INSERT INTO approvals(action,resource_id,requested_by,requested_role,status,
                   reason,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)""",
                (action, resource_id, username, role, "pending", reason[:500], created, expires),
            )
            return {"id": int(cur.lastrowid), "action": action, "resource_id": resource_id,
                    "status": "pending", "created_at": created, "expires_at": expires}

    def approval(self, approval_id: int, user: dict[str, Any] | None = None) -> dict[str, Any] | None:
        with self.connection() as db:
            query = "SELECT a.* FROM approvals a JOIN resources r ON r.id=a.resource_id WHERE a.id=?"; params: tuple[Any, ...] = (approval_id,)
            if user and user.get("role") != "admin":
                query += " AND r.owner_user_id=?"; params += (user["id"],)
            row = db.execute(query, params).fetchone()
        return dict(row) if row else None

    def decide_approval(self, approval_id: int, status: str, username: str) -> bool:
        with self.connection() as db:
            cur = db.execute(
                """UPDATE approvals SET status=?,decided_by=?,decided_at=?
                   WHERE id=? AND status='pending' AND expires_at>?""",
                (status, username, _now(), approval_id, time.time()),
            )
            return cur.rowcount == 1

    def consume_approval(self, approval_id: int, action: str, resource_id: int,
                         username: str) -> bool:
        with self.connection() as db:
            cur = db.execute(
                """UPDATE approvals SET status='consumed'
                   WHERE id=? AND action=? AND resource_id=? AND requested_by=?
                   AND status='approved' AND expires_at>?""",
                (approval_id, action, resource_id, username, time.time()),
            )
            return cur.rowcount == 1

    def approvals(self, status: str | None = "pending", user: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        query = "SELECT a.* FROM approvals a JOIN resources r ON r.id=a.resource_id"
        params: tuple[Any, ...] = ()
        if user and user.get("role") != "admin":
            query += " WHERE r.owner_user_id=?"; params = (user["id"],)
        if status:
            query += " AND " if " WHERE " in query else " WHERE "
            query += "a.status=?"; params += (status,)
        query += " ORDER BY id DESC LIMIT 100"
        with self.connection() as db:
            return [dict(row) for row in db.execute(query, params)]
