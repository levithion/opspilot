"""SQLite persistence: one small thread-safe wrapper plus the schema for the whole platform."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any


def utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def month_key(ts: str | None = None) -> str:
    return (ts or utcnow())[:7]


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str, separators=(",", ":"), sort_keys=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  team TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'employee',
  vpn_enabled INTEGER NOT NULL DEFAULT 1,
  mfa_enrolled INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS access_grants (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  resource TEXT NOT NULL,
  level TEXT NOT NULL,
  granted_by TEXT,
  granted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_grants_user ON access_grants(user_id);

CREATE TABLE IF NOT EXISTS licenses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product TEXT NOT NULL,
  owner_user_id TEXT REFERENCES users(user_id),
  seat_status TEXT NOT NULL DEFAULT 'assigned',
  renewal_date TEXT,
  cost_center TEXT
);

CREATE TABLE IF NOT EXISTS tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE,
  title TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT 'general',
  priority TEXT NOT NULL DEFAULT 'medium',
  status TEXT NOT NULL DEFAULT 'open',
  requester_id TEXT NOT NULL,
  assignee TEXT,
  source TEXT NOT NULL DEFAULT 'api',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_requester ON tickets(requester_id);

CREATE TABLE IF NOT EXISTS ticket_comments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INTEGER NOT NULL REFERENCES tickets(id),
  author TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  action TEXT NOT NULL,
  params TEXT NOT NULL,
  requested_by TEXT NOT NULL,
  requester_team TEXT,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  decided_by TEXT,
  decided_at TEXT,
  decision_note TEXT,
  result TEXT,
  request_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  request_id TEXT,
  actor TEXT NOT NULL,
  client_id TEXT,
  action TEXT NOT NULL,
  args TEXT NOT NULL,
  outcome TEXT NOT NULL,
  approved_by TEXT,
  prev_hash TEXT NOT NULL,
  hash TEXT NOT NULL
);
-- The audit log is append-only: any UPDATE/DELETE is rejected at the database level.
-- Erasure requests are handled by pseudonymising actors at write time, never by editing history.
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

CREATE TABLE IF NOT EXISTS usage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  request_id TEXT,
  team TEXT NOT NULL,
  user_id TEXT,
  client_id TEXT NOT NULL DEFAULT 'unknown',
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  cost_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_team_ts ON usage_events(team, ts);

CREATE TABLE IF NOT EXISTS budgets (
  team TEXT PRIMARY KEY,
  monthly_usd REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_alerts (
  team TEXT NOT NULL,
  month TEXT NOT NULL,
  level TEXT NOT NULL,
  ts TEXT NOT NULL,
  PRIMARY KEY (team, month, level)
);

CREATE TABLE IF NOT EXISTS requests (
  request_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  user_id TEXT,
  team TEXT,
  client_id TEXT,
  channel TEXT NOT NULL DEFAULT 'chat',
  status TEXT NOT NULL,
  category TEXT,
  guardrail_blocked INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL NOT NULL DEFAULT 0,
  cost_usd REAL NOT NULL DEFAULT 0,
  tool_calls INTEGER NOT NULL DEFAULT 0,
  detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts);

CREATE TABLE IF NOT EXISTS spans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id TEXT,
  name TEXT NOT NULL,
  start_ts TEXT NOT NULL,
  duration_ms REAL NOT NULL,
  status TEXT NOT NULL,
  attrs TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_spans_request ON spans(request_id);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL,
  channel TEXT NOT NULL,
  message TEXT NOT NULL,
  via TEXT NOT NULL,
  delivered INTEGER NOT NULL DEFAULT 0
);
"""


class Database:
    """Thread-safe SQLite wrapper. A single connection guarded by an RLock is plenty here."""

    def __init__(self, path: str = ":memory:"):
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.execute("PRAGMA busy_timeout=30000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_with_retry()

    def _init_with_retry(self, attempts: int = 60) -> None:
        """Several processes (API, MCP clients that launch two copies) can start on the same file at once.

        Switching to WAL and creating the schema need locks that SQLite does not always wait for, so retry.
        """
        for attempt in range(attempts):
            try:
                if self.path != ":memory:":
                    mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if str(mode).lower() != "wal":
                        self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.executescript(SCHEMA)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) and "busy" not in str(exc):
                    raise
                if attempt == attempts - 1:
                    raise
                time.sleep(0.05 + 0.02 * attempt)

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Sequence[Any] = ()) -> Any:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
            return row[0] if row else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
