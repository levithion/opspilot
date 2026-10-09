"""Tamper-evident, append-only audit log (hash chain + DB triggers that reject UPDATE/DELETE)."""

from __future__ import annotations

import hashlib
from typing import Any

from opspilot.db import Database, dumps, utcnow
from opspilot.security.pii import redact_obj

GENESIS = "0" * 64


def _digest(
    prev: str,
    ts: str,
    request_id: str | None,
    actor: str,
    client_id: str | None,
    action: str,
    args: str,
    outcome: str,
    approved_by: str | None,
) -> str:
    payload = "|".join([prev, ts, request_id or "", actor, client_id or "", action, args, outcome, approved_by or ""])
    return hashlib.sha256(payload.encode()).hexdigest()


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        actor: str,
        action: str,
        args: dict[str, Any] | None = None,
        outcome: str = "ok",
        request_id: str | None = None,
        client_id: str | None = None,
        approved_by: str | None = None,
    ) -> int:
        """Append one entry. Arguments are PII-redacted before being persisted."""
        safe_args = dumps(redact_obj(args or {}))
        ts = utcnow()
        with self.db.transaction() as conn:
            row = conn.execute("SELECT hash FROM audit_log ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row["hash"] if row else GENESIS
            h = _digest(prev, ts, request_id, actor, client_id, action, safe_args, outcome, approved_by)
            cur = conn.execute(
                "INSERT INTO audit_log(ts, request_id, actor, client_id, action, args, outcome, approved_by, prev_hash, hash)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ts, request_id, actor, client_id, action, safe_args, outcome, approved_by, prev, h),
            )
            return int(cur.lastrowid)

    def recent(self, limit: int = 100, actor: str | None = None) -> list[dict[str, Any]]:
        if actor:
            return self.db.query("SELECT * FROM audit_log WHERE actor=? ORDER BY seq DESC LIMIT ?", (actor, limit))
        return self.db.query("SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (limit,))

    def verify(self) -> tuple[bool, int | None]:
        """Recompute the chain. Returns (ok, first_broken_seq)."""
        prev = GENESIS
        for r in self.db.query("SELECT * FROM audit_log ORDER BY seq ASC"):
            expected = _digest(
                prev, r["ts"], r["request_id"], r["actor"], r["client_id"], r["action"], r["args"], r["outcome"], r["approved_by"]
            )
            if r["prev_hash"] != prev or r["hash"] != expected:
                return False, r["seq"]
            prev = r["hash"]
        return True, None
