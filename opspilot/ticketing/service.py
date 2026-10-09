"""Mock ticketing backend (SQLite). Swap for ServiceNow / Jira by implementing the same interface."""

from __future__ import annotations

from typing import Any, Protocol

from opspilot.db import Database, utcnow
from opspilot.http import request_with_retry


class TicketBackend(Protocol):
    def create(
        self, *, requester_id: str, title: str, description: str, category: str, priority: str, source: str
    ) -> dict[str, Any]: ...
    def get(self, key: str) -> dict[str, Any] | None: ...
    def list_for(self, requester_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]: ...
    def add_comment(self, key: str, author: str, body: str) -> dict[str, Any] | None: ...
    def set_status(self, key: str, status: str) -> dict[str, Any] | None: ...


VALID_STATUSES = ("open", "in_progress", "waiting_on_user", "resolved", "closed")


class LocalTicketBackend:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        *,
        requester_id: str,
        title: str,
        description: str = "",
        category: str = "general",
        priority: str = "medium",
        source: str = "api",
    ) -> dict[str, Any]:
        now = utcnow()
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO tickets(title, description, category, priority, requester_id, source, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (title, description, category, priority, requester_id, source, now, now),
            )
            tid = cur.lastrowid
            conn.execute("UPDATE tickets SET key=? WHERE id=?", (f"HELP-{1000 + tid}", tid))
        return self.get(f"HELP-{1000 + tid}")  # type: ignore[return-value]

    def get(self, key: str) -> dict[str, Any] | None:
        t = self.db.query_one("SELECT * FROM tickets WHERE key=?", (key,))
        if t:
            t["comments"] = self.db.query(
                "SELECT author, body, created_at FROM ticket_comments WHERE ticket_id=? ORDER BY id", (t["id"],)
            )
        return t

    def list_for(self, requester_id: str | None = None, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM tickets WHERE 1=1", []
        if requester_id:
            sql += " AND requester_id=?"
            params.append(requester_id)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, params)

    def add_comment(self, key: str, author: str, body: str) -> dict[str, Any] | None:
        t = self.db.query_one("SELECT id FROM tickets WHERE key=?", (key,))
        if not t:
            return None
        now = utcnow()
        self.db.execute(
            "INSERT INTO ticket_comments(ticket_id, author, body, created_at) VALUES (?,?,?,?)", (t["id"], author, body, now)
        )
        self.db.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now, t["id"]))
        return self.get(key)

    def set_status(self, key: str, status: str) -> dict[str, Any] | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}")
        self.db.execute("UPDATE tickets SET status=?, updated_at=? WHERE key=?", (status, utcnow(), key))
        return self.get(key)


class HttpTicketBackend:
    """Talks to a remote ticketing API (e.g. the mock one running as a separate service)."""

    def __init__(self, base_url: str, token: str = "", timeout: float = 5.0, retries: int = 3):
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.timeout, self.retries = timeout, retries

    def _call(self, method: str, path: str, **kw):
        return request_with_retry(
            method, f"{self.base}/ticketing/v1{path}", headers=self.headers, timeout=self.timeout, retries=self.retries, **kw
        )

    def create(self, **fields) -> dict[str, Any]:
        return self._call("POST", "/tickets", json=fields).json()

    def get(self, key: str):
        try:
            return self._call("GET", f"/tickets/{key}").json()
        except Exception:
            return None

    def list_for(self, requester_id=None, status=None, limit=50):
        return self._call("GET", "/tickets", params={"requester_id": requester_id, "status": status, "limit": limit}).json()

    def add_comment(self, key, author, body):
        return self._call("POST", f"/tickets/{key}/comments", json={"author": author, "body": body}).json()

    def set_status(self, key, status):
        return self._call("PATCH", f"/tickets/{key}", json={"status": status}).json()
