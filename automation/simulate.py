"""Simulate the email -> classify -> ticket -> notify automation on a labelled test set.

    python automation/simulate.py [--manual-minutes 6] [--json]

Measures (a) classification accuracy against the labels in emails.jsonl, (b) tasks automated per run and
(c) automated handling time. The *manual baseline is an assumption you set* (--manual-minutes): minutes a
human spends to read, categorise, key in a ticket and ping the channel. Report the assumption with the result.
With OPSPILOT_LLM_PROVIDER=mock, accuracy measures the offline rule-based mock, not a real model.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from opspilot.api.app import create_app  # noqa: E402
from opspilot.config import Settings  # noqa: E402
from opspilot.container import build_services  # noqa: E402


def run(manual_minutes: float) -> dict:
    settings = Settings(env="test", db_path=":memory:", data_dir=Path(tempfile.mkdtemp()), rate_limit_per_minute=10_000)
    # env="test" keeps the run hermetic, but still honours OPSPILOT_LLM_PROVIDER / API keys from the environment
    emails = [json.loads(line) for line in (Path(__file__).parent / "emails.jsonl").read_text().splitlines() if line]
    svc = build_services(settings)
    cat_ok = prio_ok = 0
    latencies, notified, confusions = [], 0, []
    with TestClient(create_app(svc)) as c:
        tok = c.post("/auth/dev-token", json={"email": "automation@opspilot.example"}).json()["access_token"]
        headers = {"Authorization": f"Bearer {tok}", "X-Client-Id": "n8n"}
        for e in emails:
            t0 = time.perf_counter()
            r = c.post(
                "/api/automation/email-triage",
                headers=headers,
                json={"sender": e["sender"], "subject": e["subject"], "body": e["body"]},
            )
            latencies.append((time.perf_counter() - t0) * 1000)
            r.raise_for_status()
            got = r.json()["triage"]
            cat_ok += got["category"] == e["category"]
            prio_ok += got["priority"] == e["priority"]
            if got["category"] != e["category"] or got["priority"] != e["priority"]:
                confusions.append(
                    {
                        "subject": e["subject"],
                        "expected": [e["category"], e["priority"]],
                        "got": [got["category"], got["priority"]],
                    }
                )
        notified = svc.db.scalar("SELECT COUNT(*) FROM notifications")
        tickets = svc.db.scalar("SELECT COUNT(*) FROM tickets")
        cost = svc.db.scalar("SELECT COALESCE(SUM(cost_usd),0) FROM usage_events")
    n = len(emails)
    avg_auto_s = sum(latencies) / n / 1000
    manual_s = manual_minutes * 60
    return {
        "emails": n,
        "tickets_created": tickets,
        "notifications_sent": notified,
        "category_accuracy": round(cat_ok / n, 4),
        "priority_accuracy": round(prio_ok / n, 4),
        # classify + create ticket for every email, plus a channel notification for high/urgent tickets
        "tasks_automated_total": n * 2 + notified,
        "tasks_automated_per_email": round((n * 2 + notified) / n, 2),
        "avg_automated_handling_seconds": round(avg_auto_s, 3),
        "assumed_manual_handling_seconds": manual_s,
        "handling_time_reduction_pct": round(100 * (1 - avg_auto_s / manual_s), 2),
        "llm_cost_total_usd": round(cost, 6),
        "llm_cost_per_email_usd": round(cost / n, 6),
        "llm_provider": settings.llm_provider,
        "caveat": (
            "Offline mock provider: latency and accuracy are NOT representative of a real model. Re-run with "
            "OPSPILOT_LLM_PROVIDER=ollama (free, local) before quoting numbers."
            if settings.llm_provider == "mock"
            else "Manual baseline is an assumption (--manual-minutes), not a measurement."
        ),
        "mismatches": confusions,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manual-minutes", type=float, default=6.0, help="assumed human minutes per email (default 6)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = run(args.manual_minutes)
    if args.json:
        print(json.dumps(rep, indent=2))
        return
    for k, v in rep.items():
        if k != "mismatches":
            print(f"{k:<34} {v}")
    for m in rep["mismatches"]:
        print(f"  mismatch: {m['subject']!r} expected {m['expected']} got {m['got']}")


if __name__ == "__main__":
    main()
