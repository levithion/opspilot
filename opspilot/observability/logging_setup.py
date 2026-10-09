"""Structured JSON logging with a request-id context and a PII-redacting filter."""

from __future__ import annotations

import contextvars
import json
import logging
import sys
import time

from opspilot.security.pii import redact

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "request_id": request_id_var.get(),
            "msg": redact(record.getMessage(), keep_emails=False).text,
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = redact(v).text if isinstance(v, str) else v
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info)).text
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)  # stderr: stdout is reserved for MCP stdio framing
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpx2", "httpcore", "qdrant_client", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
