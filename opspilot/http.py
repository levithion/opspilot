"""HTTP helper with timeouts and exponential-backoff retries for outbound integrations."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

log = logging.getLogger("opspilot.http")


def request_with_retry(
    method: str,
    url: str,
    *,
    json: Any = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
    retries: int = 3,
    backoff: float = 0.4,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """Retry on network errors and 5xx/429; never retry other 4xx."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            if client is not None:
                resp = client.request(method, url, json=json, headers=headers, params=params, timeout=timeout)
            else:
                resp = httpx.request(method, url, json=json, headers=headers, params=params, timeout=timeout)
            if resp.status_code < 500 and resp.status_code != 429:
                resp.raise_for_status()
                return resp
            last = httpx.HTTPStatusError(f"{resp.status_code}", request=resp.request, response=resp)
        except httpx.HTTPStatusError:
            raise
        except httpx.HTTPError as exc:
            last = exc
        log.warning("outbound %s %s failed (attempt %d/%d): %s", method, url, attempt + 1, retries, last)
        if attempt < retries - 1:
            time.sleep(backoff * (2**attempt))
    assert last is not None
    raise last
