"""Dashboard queries: traffic, latency percentiles, errors, guardrail blocks and cost per resolved request."""

from __future__ import annotations

import math
from typing import Any

from opspilot.db import Database


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, math.ceil(pct / 100 * len(ordered)) - 1)
    return round(ordered[k], 2)


class Metrics:
    def __init__(self, db: Database):
        self.db = db

    def summary(self) -> dict[str, Any]:
        rows = self.db.query("SELECT status, guardrail_blocked, latency_ms, cost_usd FROM requests")
        total = len(rows)
        resolved = [r for r in rows if r["status"] == "resolved"]
        errors = [r for r in rows if r["status"] == "error"]
        blocked = [r for r in rows if r["guardrail_blocked"]]
        total_cost = sum(r["cost_usd"] for r in rows)
        lat = [r["latency_ms"] for r in rows]
        return {
            "total_requests": total,
            "resolved": len(resolved),
            "pending_approval": sum(1 for r in rows if r["status"] == "pending_approval"),
            "errors": len(errors),
            "error_rate": round(len(errors) / total, 4) if total else 0.0,
            "guardrail_blocked": len(blocked),
            "guardrail_block_pct": round(100 * len(blocked) / total, 2) if total else 0.0,
            "latency_p50_ms": percentile(lat, 50),
            "latency_p95_ms": percentile(lat, 95),
            "total_cost_usd": round(total_cost, 6),
            "cost_per_resolved_request_usd": round(total_cost / len(resolved), 6) if resolved else 0.0,
            "deflection_rate": round(len(resolved) / total, 4) if total else 0.0,
        }

    def requests_per_day(self, days: int = 14) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT substr(ts,1,10) day, COUNT(*) requests, SUM(status='error') errors,"
            " SUM(guardrail_blocked) blocked, ROUND(SUM(cost_usd),6) cost_usd"
            " FROM requests GROUP BY day ORDER BY day DESC LIMIT ?",
            (days,),
        )[::-1]

    def by_category(self) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT COALESCE(category,'unknown') category, COUNT(*) requests FROM requests"
            " GROUP BY category ORDER BY requests DESC"
        )

    def slowest_spans(self, limit: int = 10) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT name, COUNT(*) n, ROUND(AVG(duration_ms),2) avg_ms, ROUND(MAX(duration_ms),2) max_ms,"
            " SUM(status='error') errors FROM spans GROUP BY name ORDER BY avg_ms DESC LIMIT ?",
            (limit,),
        )
