"""Authentication: Microsoft Entra ID (OIDC / OAuth2 bearer tokens) or locally minted dev tokens."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import jwt
from jwt import PyJWKClient

from opspilot.config import Settings
from opspilot.db import Database
from opspilot.security.identity import Principal, normalise_roles

DEV_ISSUER = "opspilot-dev"
DEV_AUDIENCE = "opspilot"


class AuthError(Exception):
    pass


def mint_dev_token(settings: Settings, user: dict[str, Any]) -> str:
    now = int(time.time())
    claims = {
        "iss": DEV_ISSUER,
        "aud": DEV_AUDIENCE,
        "sub": user["user_id"],
        "email": user["email"],
        "name": user["name"],
        "department": user["team"],
        "roles": [user["role"]],
        "iat": now,
        "exp": now + settings.dev_jwt_ttl_s,
    }
    return jwt.encode(claims, settings.dev_jwt_secret, algorithm="HS256")


class TokenVerifier:
    """Validates signature, issuer, audience and expiry. Key lookup is injectable for tests."""

    def __init__(self, settings: Settings, key_resolver: Callable[[str], Any] | None = None):
        self.settings = settings
        self._jwk_client: PyJWKClient | None = None
        self._key_resolver = key_resolver

    def _oidc_key(self, token: str):
        if self._key_resolver:
            return self._key_resolver(token)
        if self._jwk_client is None:
            self._jwk_client = PyJWKClient(self.settings.oidc_jwks_uri, cache_keys=True, lifespan=3600)
        return self._jwk_client.get_signing_key_from_jwt(token).key

    def verify(self, token: str) -> dict[str, Any]:
        s = self.settings
        try:
            if s.auth_mode == "dev":
                return jwt.decode(
                    token,
                    s.dev_jwt_secret,
                    algorithms=["HS256"],
                    audience=DEV_AUDIENCE,
                    issuer=DEV_ISSUER,
                    options={"require": ["exp", "sub"]},
                )
            if not s.oidc_audience or not (s.oidc_tenant_id or s.oidc_issuer):
                raise AuthError("OIDC mode requires OPSPILOT_OIDC_TENANT_ID and OPSPILOT_OIDC_AUDIENCE")
            return jwt.decode(
                token,
                self._oidc_key(token),
                algorithms=["RS256"],
                audience=s.oidc_audience,
                issuer=s.oidc_issuer_url,
                options={"require": ["exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"invalid token: {exc}") from exc


def principal_from_claims(claims: dict[str, Any], settings: Settings, db: Database, client_id: str = "web") -> Principal:
    """Map token claims to a Principal; JIT-provision unknown users with least privilege."""
    email = (claims.get("email") or claims.get("preferred_username") or claims.get("upn") or "").lower()
    app_id = claims.get("appid") or claims.get("azp")
    if not email and app_id and (claims.get("idtyp") == "app" or "scp" not in claims):
        # App-only token (client-credentials flow), e.g. n8n / Power Automate: no human claims, so synthesise a service identity.
        email = f"app-{app_id}@service.opspilot.local".lower()
        claims = {
            **claims,
            "name": claims.get("app_displayname") or f"service {app_id}",
            settings.oidc_team_claim: claims.get(settings.oidc_team_claim) or "Automation",
        }
    if not email:
        raise AuthError("token has no email / preferred_username claim")
    user_id = str(claims.get("oid") or claims.get("sub"))
    roles = normalise_roles(claims.get(settings.oidc_role_claim) or claims.get("roles"))
    row = db.query_one("SELECT * FROM users WHERE email=?", (email,))
    team = claims.get(settings.oidc_team_claim) or (row["team"] if row else "unassigned")
    name = claims.get("name") or (row["name"] if row else email.split("@")[0])
    if row is None:
        db.execute(
            "INSERT OR IGNORE INTO users(user_id, email, name, team, role) VALUES (?,?,?,?,?)",
            (user_id, email, name, team, "employee"),
        )
    else:
        user_id = row["user_id"]
    return Principal(user_id=user_id, email=email, name=name, team=team, roles=roles, client_id=client_id)
