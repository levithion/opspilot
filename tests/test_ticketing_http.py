import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from opspilot.db import Database
from opspilot.http import request_with_retry
from opspilot.ticketing.api import build_router
from opspilot.ticketing.service import HttpTicketBackend, LocalTicketBackend


def make_app(token="s3cret"):
    app = FastAPI()
    app.include_router(build_router(LocalTicketBackend(Database(":memory:")), token))
    return app


def test_service_token_enforced():
    c = TestClient(make_app())
    body = {"requester_id": "u1", "title": "Printer jam"}
    assert c.post("/ticketing/v1/tickets", json=body).status_code == 401
    assert c.post("/ticketing/v1/tickets", json=body, headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = c.post("/ticketing/v1/tickets", json=body, headers={"Authorization": "Bearer s3cret"})
    assert r.status_code == 201 and r.json()["key"] == "HELP-1001"


def test_http_backend_round_trip(monkeypatch):
    c = TestClient(make_app())

    def fake_request(method, url, *, json=None, headers=None, params=None, timeout=5, retries=3, **_):
        path = url.split("://", 1)[1].split("/", 1)[1]
        r = c.request(
            method, "/" + path, json=json, headers=headers, params={k: v for k, v in (params or {}).items() if v is not None}
        )
        r.raise_for_status()
        return r

    monkeypatch.setattr("opspilot.ticketing.service.request_with_retry", fake_request)
    b = HttpTicketBackend("http://tickets.internal", "s3cret")
    t = b.create(requester_id="u1", title="VPN down", description="d", category="vpn", priority="high", source="web")
    assert b.get(t["key"])["title"] == "VPN down" and b.get("HELP-9999") is None
    assert b.add_comment(t["key"], "carol", "looking")["comments"][0]["author"] == "carol"
    assert b.set_status(t["key"], "resolved")["status"] == "resolved"
    assert [x["key"] for x in b.list_for("u1")] == [t["key"]]
    with pytest.raises(ValueError):
        LocalTicketBackend(Database(":memory:")).set_status("HELP-1", "bogus")


def test_retry_on_5xx_then_success(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503 if calls["n"] < 3 else 200, json={"ok": True})

    monkeypatch.setattr("time.sleep", lambda s: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert request_with_retry("POST", "http://x.test/hook", json={}, client=client, retries=3).json() == {"ok": True}
    assert calls["n"] == 3


def test_no_retry_on_4xx(monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400)

    monkeypatch.setattr("time.sleep", lambda s: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.HTTPStatusError):
        request_with_retry("POST", "http://x.test/hook", client=client, retries=3)
    assert calls["n"] == 1


def test_notifier_delivers_to_webhooks_and_falls_back(svc, monkeypatch):
    sent = []

    def fake(method, url, **kw):
        if "teams" in url:
            raise httpx.ConnectError("down")
        sent.append((url, kw["json"]))

    monkeypatch.setattr("opspilot.notify.request_with_retry", fake)
    svc.notifier.settings = svc.settings.model_copy(
        update={"slack_webhook_url": "https://slack.test/h", "teams_webhook_url": "https://teams.test/h"}
    )
    res = svc.notifier.send("it-helpdesk", "Disk full on jane@corp.com laptop")
    assert res["delivered"] and res["via"] == "slack"
    assert "jane@corp.com" not in sent[0][1]["text"]
    svc.notifier.settings = svc.settings.model_copy(update={"slack_webhook_url": "", "teams_webhook_url": "https://teams.test/h"})
    assert svc.notifier.send("it-helpdesk", "hello")["via"] == "outbox"  # delivery failure never breaks the caller
