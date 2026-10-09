"""FastAPI dependencies: authentication, RBAC helpers and a per-user rate limiter."""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, Header, HTTPException, Request

from opspilot.container import Services
from opspilot.security.auth import AuthError, principal_from_claims
from opspilot.security.identity import Principal


def get_services(request: Request) -> Services:
    return request.app.state.svc


def get_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    x_client_id: str | None = Header(default=None),
    svc: Services = Depends(get_services),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = svc.verifier.verify(authorization.split(" ", 1)[1].strip())
        principal = principal_from_claims(claims, svc.settings, svc.db, client_id=(x_client_id or "web")[:40])
    except AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
    row = svc.db.query_one("SELECT status FROM users WHERE user_id=?", (principal.user_id,))
    if row and row["status"] != "active":
        raise HTTPException(403, "account disabled")
    request.state.principal = principal
    return principal


def require(scope: str):
    """Dependency factory: `Depends(require("audit:read"))`."""

    def dep(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has_scope(scope):
            raise HTTPException(403, f"missing scope '{scope}'")
        return principal

    return dep


class RateLimiter:
    """Sliding-window limiter, per user. In-memory: use Redis/APIM when running more than one replica."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": "30"})
            q.append(now)


def rate_limited(request: Request, principal: Principal = Depends(get_principal)) -> Principal:
    request.app.state.limiter.check(principal.user_id)
    return principal
