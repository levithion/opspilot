from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from opspilot.agent.template import load_template
from opspilot.agent.workflow import HelpdeskAgent
from opspilot.api.app import create_app
from opspilot.config import Settings
from opspilot.container import Services, build_services
from opspilot.security.identity import Principal, normalise_roles


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        env="test",
        db_path=":memory:",
        data_dir=tmp_path,
        llm_provider="mock",
        auth_mode="dev",
        default_team_budget_usd=50.0,
        rate_limit_per_minute=1000,
    )


@pytest.fixture
def svc(settings) -> Services:
    s = build_services(settings)
    yield s
    s.close()


def make_principal(svc: Services, email: str, client_id: str = "web") -> Principal:
    row = svc.db.query_one("SELECT * FROM users WHERE email=?", (email,))
    return Principal(row["user_id"], row["email"], row["name"], row["team"], normalise_roles([row["role"]]), client_id)


@pytest.fixture
def alice(svc):
    return make_principal(svc, "alice@opspilot.example")


@pytest.fixture
def bob(svc):
    return make_principal(svc, "bob@opspilot.example")  # team lead


@pytest.fixture
def carol(svc):
    return make_principal(svc, "carol@opspilot.example")  # IT admin


@pytest.fixture
def dave(svc):
    return make_principal(svc, "dave@opspilot.example")  # Finance employee


@pytest.fixture
def agent(svc, settings) -> HelpdeskAgent:
    return HelpdeskAgent(load_template("helpdesk", settings.template_dir), svc)


@pytest.fixture
def client(svc):
    with TestClient(create_app(svc)) as c:
        yield c


def login(client: TestClient, email: str) -> dict[str, str]:
    tok = client.post("/auth/dev-token", json={"email": email}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}
