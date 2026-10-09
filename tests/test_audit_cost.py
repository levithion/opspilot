import sqlite3

import pytest

from opspilot.cost.pricing import compute_cost
from opspilot.cost.tracker import BudgetExceeded


def test_audit_chain_verifies_and_redacts(svc):
    svc.audit.record(actor="u1", action="x", args={"note": "mail me at a@b.com"})
    svc.audit.record(actor="u2", action="y", args={})
    ok, broken = svc.audit.verify()
    assert ok and broken is None
    assert "a@b.com" not in svc.audit.recent(1)[-1]["args"] + svc.audit.recent(2)[-1]["args"]


def test_audit_is_append_only(svc):
    svc.audit.record(actor="u1", action="x")
    with pytest.raises(sqlite3.DatabaseError):
        svc.db.execute("UPDATE audit_log SET actor='evil'")
    with pytest.raises(sqlite3.DatabaseError):
        svc.db.execute("DELETE FROM audit_log")


def test_audit_detects_tampering(svc):
    svc.audit.record(actor="u1", action="x")
    svc.audit.record(actor="u1", action="y")
    # bypass the triggers as a DBA with file access would
    svc.db.execute("DROP TRIGGER audit_no_update")
    svc.db.execute("UPDATE audit_log SET outcome='tampered' WHERE seq=1")
    ok, broken = svc.audit.verify()
    assert not ok and broken == 1


def test_cost_math():
    assert compute_cost("claude-sonnet-5-5", 1_000_000, 1_000_000) == 18.0
    assert compute_cost("unknown-model", 1_000_000, 0) == 3.0


def test_budget_warn_then_block_with_single_alerts(svc):
    svc.cost.set_budget("Finance", 1.0)
    alerts = []
    svc.cost.alert_sink = lambda team, level, msg: alerts.append((team, level))

    def rec(tokens):
        return svc.cost.record(
            team="Finance",
            user_id="u",
            client_id="web",
            provider="anthropic",
            model="claude-sonnet-5-5",
            input_tokens=tokens,
            output_tokens=0,
        )

    rec(100_000)  # $0.30 -> ok
    assert svc.cost.status("Finance").state == "ok"
    rec(200_000)  # $0.90 -> warn (>=80%)
    rec(10_000)  # still warn: no duplicate alert
    assert svc.cost.status("Finance").state == "warn"
    assert alerts == [("Finance", "warn")]
    svc.cost.enforce("Finance")  # warn does not block
    rec(100_000)  # >= $1.00
    with pytest.raises(BudgetExceeded):
        svc.cost.enforce("Finance")
    assert alerts == [("Finance", "warn"), ("Finance", "blocked")]


def test_shadow_ai_flags_unregistered_clients(svc):
    for client in ("web", "mystery-script"):
        svc.cost.record(
            team="IT", user_id="u", client_id=client, provider="openai", model="gpt-4o-mini", input_tokens=1000, output_tokens=100
        )
    rows = {r["client_id"]: r for r in svc.cost.shadow_ai()}
    assert rows["web"]["registered"] and not rows["mystery-script"]["registered"]
    assert rows["mystery-script"]["flag"] == "unregistered client"
