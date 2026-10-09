"""Outbound notifications to Slack / Microsoft Teams webhooks, with an always-on outbox."""

from __future__ import annotations

import logging

from opspilot.config import Settings
from opspilot.db import Database, utcnow
from opspilot.http import request_with_retry
from opspilot.security.pii import redact

log = logging.getLogger("opspilot.notify")


class Notifier:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings

    def send(self, channel: str, message: str) -> dict:
        """Deliver to every configured webhook; record in the outbox regardless (useful offline)."""
        safe = redact(message).text
        via, delivered = [], False
        s = self.settings
        targets = []
        if s.slack_webhook_url:
            targets.append(("slack", s.slack_webhook_url, {"text": f"[#{channel}] {safe}"}))
        if s.teams_webhook_url:
            targets.append(("teams", s.teams_webhook_url, {"text": f"[{channel}] {safe}"}))
        for name, url, body in targets:
            try:
                request_with_retry("POST", url, json=body, timeout=s.http_timeout_s, retries=s.http_retries)
                via.append(name)
                delivered = True
            except Exception as exc:
                log.error("notification via %s failed: %s", name, exc)
        via_label = "+".join(via) if via else "outbox"
        self.db.execute(
            "INSERT INTO notifications(ts, channel, message, via, delivered) VALUES (?,?,?,?,?)",
            (utcnow(), channel, safe, via_label, int(delivered)),
        )
        return {"channel": channel, "via": via_label, "delivered": delivered}

    def budget_alert(self, team: str, level: str, message: str) -> None:
        self.send("it-oncall" if level == "blocked" else "it-helpdesk", message)
