import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from opspilot.config import Settings
from opspilot.security.auth import AuthError, TokenVerifier, mint_dev_token, principal_from_claims


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def oidc_settings(tmp_path):
    return Settings(
        env="test",
        auth_mode="oidc",
        oidc_tenant_id="tenant-1",
        oidc_audience="api://opspilot",
        db_path=":memory:",
        data_dir=tmp_path,
    )


def entra_token(key, **over):
    now = int(time.time())
    claims = {
        "iss": "https://login.microsoftonline.com/tenant-1/v2.0",
        "aud": "api://opspilot",
        "sub": "abc",
        "oid": "oid-123",
        "preferred_username": "zoe@opspilot.example",
        "name": "Zoe Zed",
        "roles": ["IT.Admin"],
        "department": "IT",
        "iat": now,
        "exp": now + 600,
        **over,
    }
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def test_oidc_token_accepted_and_mapped(svc, tmp_path, rsa_key):
    s = oidc_settings(tmp_path)
    v = TokenVerifier(s, key_resolver=lambda tok: rsa_key.public_key())
    claims = v.verify(entra_token(rsa_key))
    p = principal_from_claims(claims, s, svc.db)
    assert p.email == "zoe@opspilot.example" and p.is_admin and p.team == "IT" and p.user_id == "oid-123"
    assert (
        svc.db.query_one("SELECT role FROM users WHERE email='zoe@opspilot.example'")["role"] == "employee"
    )  # JIT = least privilege


@pytest.mark.parametrize(
    "override", [{"aud": "api://other"}, {"iss": "https://evil.example/v2.0"}, {"exp": int(time.time()) - 5}]
)
def test_oidc_rejects_bad_claims(tmp_path, rsa_key, override):
    v = TokenVerifier(oidc_settings(tmp_path), key_resolver=lambda tok: rsa_key.public_key())
    with pytest.raises(AuthError):
        v.verify(entra_token(rsa_key, **override))


def test_oidc_rejects_wrong_signature(tmp_path, rsa_key):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    v = TokenVerifier(oidc_settings(tmp_path), key_resolver=lambda tok: rsa_key.public_key())
    with pytest.raises(AuthError):
        v.verify(entra_token(other))


def test_oidc_rejects_hs256_downgrade(tmp_path, rsa_key):
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "https://login.microsoftonline.com/tenant-1/v2.0",
            "aud": "api://opspilot",
            "sub": "x",
            "exp": now + 60,
            "email": "a@b.c",
        },
        "any-secret-any-secret-any-secret-12",
        algorithm="HS256",
    )
    v = TokenVerifier(oidc_settings(tmp_path), key_resolver=lambda tok: rsa_key.public_key())
    with pytest.raises(AuthError):
        v.verify(forged)


def test_oidc_requires_configuration(tmp_path):
    s = Settings(env="test", auth_mode="oidc", db_path=":memory:", data_dir=tmp_path)
    with pytest.raises(AuthError):
        TokenVerifier(s, key_resolver=lambda t: None).verify("a.b.c")


def test_dev_token_round_trip_and_tamper(svc, settings):
    user = svc.db.query_one("SELECT * FROM users WHERE email='alice@opspilot.example'")
    tok = mint_dev_token(settings, user)
    v = TokenVerifier(settings)
    assert v.verify(tok)["email"] == "alice@opspilot.example"
    with pytest.raises(AuthError):
        v.verify(tok[:-3] + "abc")


def test_app_only_token_maps_to_service_principal(svc, tmp_path, rsa_key):
    """Client-credentials tokens (n8n / Power Automate) carry no email; they need an automation app role."""
    s = oidc_settings(tmp_path)
    v = TokenVerifier(s, key_resolver=lambda tok: rsa_key.public_key())
    tok = entra_token(
        rsa_key,
        preferred_username=None,
        name=None,
        department=None,
        roles=["automation"],
        appid="1111-2222",
        idtyp="app",
        oid="sp-oid-9",
    )
    claims = {k: val for k, val in v.verify(tok).items() if val is not None}
    p = principal_from_claims(claims, s, svc.db, client_id="n8n")
    assert p.roles == frozenset({"automation"}) and p.team == "Automation" and p.client_id == "n8n"
    assert p.has_scope("tickets:create:on_behalf") and not p.has_scope("audit:read")
