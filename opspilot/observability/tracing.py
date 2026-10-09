"""Request tracing. Spans are always stored locally (SQLite) and mirrored to Langfuse when configured."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any

from opspilot.config import Settings
from opspilot.db import Database, dumps, utcnow
from opspilot.security.pii import redact_obj

log = logging.getLogger("opspilot.tracing")


class Tracer:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self._langfuse = None
        if settings.langfuse_public_key and settings.langfuse_secret_key:
            try:
                from langfuse import Langfuse  # optional extra: pip install '.[tracing]'

                self._langfuse = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host or None,
                )
                log.info("Langfuse tracing enabled")
            except Exception as exc:  # missing package or bad config must never break requests
                log.warning("Langfuse unavailable, using local traces only: %s", exc)

    @contextmanager
    def span(self, request_id: str | None, name: str, **attrs: Any):
        start_ts, t0 = utcnow(), time.perf_counter()
        holder: dict[str, Any] = dict(attrs)
        status = "ok"
        try:
            yield holder
        except Exception as exc:
            status = "error"
            holder["error"] = type(exc).__name__
            raise
        finally:
            ms = (time.perf_counter() - t0) * 1000
            safe = redact_obj(holder)
            self.db.execute(
                "INSERT INTO spans(request_id, name, start_ts, duration_ms, status, attrs) VALUES (?,?,?,?,?,?)",
                (request_id, name, start_ts, round(ms, 3), status, dumps(safe)),
            )
            self._mirror(request_id, name, ms, status, safe)

    def _mirror(self, request_id, name, ms, status, attrs) -> None:
        if not self._langfuse:
            return
        try:
            self._langfuse.trace(id=request_id, name="opspilot-request").span(
                name=name, metadata={"duration_ms": ms, "status": status, **attrs}
            )
        except Exception as exc:
            log.debug("langfuse mirror failed: %s", exc)

    def trace_for(self, request_id: str) -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM spans WHERE request_id=? ORDER BY id", (request_id,))
