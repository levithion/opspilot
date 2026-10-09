"""OpsPilot HTTP API: chat, tool gateway (for n8n / Power Automate), approvals, admin, metrics."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from opspilot import __version__
from opspilot.agent.template import load_template
from opspilot.agent.workflow import HelpdeskAgent
from opspilot.api.deps import RateLimiter, get_principal, get_services, rate_limited, require
from opspilot.config import Settings, get_settings
from opspilot.container import Services, build_services
from opspilot.observability.logging_setup import request_id_var
from opspilot.security.auth import mint_dev_token
from opspilot.security.identity import Principal
from opspilot.ticketing.api import build_router as build_ticketing_router
from opspilot.ticketing.service import LocalTicketBackend
from opspilot.tools.approvals import ApprovalError

log = logging.getLogger("opspilot.api")
STATIC = Path(__file__).parent / "static"


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    agent: str = "helpdesk"


class DevTokenIn(BaseModel):
    email: str


class DecisionIn(BaseModel):
    approve: bool
    note: str = Field(default="", max_length=500)


class BudgetIn(BaseModel):
    team: str = Field(min_length=1, max_length=60)
    monthly_usd: float = Field(gt=0, le=1_000_000)


class ClassifyIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class EmailIn(BaseModel):
    sender: str = Field(description="From address of the inbound email")
    subject: str = Field(max_length=300)
    body: str = Field(max_length=8000)


def create_app(services: Services | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (services.settings if services else get_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.svc = services or build_services(settings)
        app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
        app.state.agents = {}
        if settings.env == "prod":
            if settings.auth_mode != "oidc":
                raise RuntimeError("OPSPILOT_AUTH_MODE must be 'oidc' in prod")
            if settings.dev_jwt_secret.startswith("change-me"):
                log.warning("dev JWT secret is the default; it is unused in oidc mode")
        yield
        if services is None:
            app.state.svc.close()

    app = FastAPI(title="OpsPilot", version=__version__, lifespan=lifespan, description="Enterprise IT helpdesk agent platform")
    app.state.svc = services
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.agents = {}

    class _StateTickets(LocalTicketBackend):
        """Resolves the DB from app state, because the services may be built later in the lifespan."""

        def __init__(self):  # noqa: D107
            pass

        @property
        def db(self):  # type: ignore[override]
            return app.state.svc.db

    app.include_router(build_ticketing_router(_StateTickets(), settings.ticket_api_token))

    # ---------------------------------------------------------------- middleware
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            try:
                response = await call_next(request)
            except Exception:
                log.exception("unhandled error", extra={"path": request.url.path})
                response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
            ms = (time.perf_counter() - t0) * 1000
            response.headers["x-request-id"] = rid
            response.headers["x-content-type-options"] = "nosniff"
            response.headers["x-frame-options"] = "DENY"
            response.headers["referrer-policy"] = "no-referrer"
            if request.url.path in ("/docs", "/redoc"):
                # Swagger UI / ReDoc load their bundles from a CDN and use an inline bootstrap script.
                response.headers["content-security-policy"] = (
                    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                    "img-src 'self' data: https://fastapi.tiangolo.com; worker-src blob:"
                )
            else:
                response.headers["content-security-policy"] = (
                    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
                )
            if not request.url.path.startswith(("/health", "/static")):
                log.info(
                    "http",
                    extra={
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "ms": round(ms, 1),
                    },
                )
            return response
        finally:
            request_id_var.reset(token)

    # ---------------------------------------------------------------- health + UI
    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/ready", tags=["ops"])
    def ready(svc: Services = Depends(get_services)):
        try:
            svc.db.scalar("SELECT 1")
        except Exception as exc:
            raise HTTPException(503, f"database unavailable: {exc}") from exc
        if svc.store.chunk_count == 0:
            raise HTTPException(503, "knowledge base is empty")
        return {
            "status": "ready",
            "kb_chunks": svc.store.chunk_count,
            "llm_provider": svc.settings.llm_provider,
            "auth_mode": svc.settings.auth_mode,
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def chat_page():
        return FileResponse(STATIC / "chat.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_page():
        return FileResponse(STATIC / "dashboard.html")

    # ---------------------------------------------------------------- auth
    @app.get("/auth/config", tags=["auth"])
    def auth_config(svc: Services = Depends(get_services)):
        s = svc.settings
        return {
            "mode": s.auth_mode,
            "dev_login": s.auth_mode == "dev" and s.env != "prod",
            "tenant_id": s.oidc_tenant_id or None,
            "audience": s.oidc_audience or None,
        }

    def _dev_only(svc: Services) -> None:
        if svc.settings.auth_mode != "dev" or svc.settings.env == "prod":
            raise HTTPException(404, "not found")

    @app.get("/auth/dev-users", tags=["auth"])
    def dev_users(svc: Services = Depends(get_services)):
        _dev_only(svc)
        return svc.db.query("SELECT email, name, team, role FROM users WHERE status='active' ORDER BY user_id")

    @app.post("/auth/dev-token", tags=["auth"])
    def dev_token(body: DevTokenIn, svc: Services = Depends(get_services)):
        _dev_only(svc)
        user = svc.db.query_one("SELECT * FROM users WHERE email=? AND status='active'", (body.email.lower(),))
        if not user:
            raise HTTPException(404, "unknown user")
        return {"access_token": mint_dev_token(svc.settings, user), "token_type": "bearer"}

    @app.get("/api/me", tags=["auth"])
    def me(p: Principal = Depends(get_principal)):
        return {
            "user_id": p.user_id,
            "email": p.email,
            "name": p.name,
            "team": p.team,
            "roles": sorted(p.roles),
            "scopes": sorted(p.scopes),
            "client_id": p.client_id,
        }

    # ---------------------------------------------------------------- chat
    def _agent(request: Request, name: str) -> HelpdeskAgent:
        agents = request.app.state.agents
        if name not in agents:
            svc = request.app.state.svc
            try:
                agents[name] = HelpdeskAgent(load_template(name, svc.settings.template_dir), svc)
            except FileNotFoundError as exc:
                raise HTTPException(404, str(exc)) from exc
        return agents[name]

    @app.post("/api/chat", tags=["agent"])
    def chat(body: ChatIn, request: Request, p: Principal = Depends(rate_limited)):
        result = _agent(request, body.agent).run(p, body.message, channel=p.client_id)
        return result.as_dict()

    @app.get("/api/agents", tags=["agent"])
    def agents(svc: Services = Depends(get_services), p: Principal = Depends(get_principal)):
        out = []
        for path in sorted(svc.settings.template_dir.glob("*.yaml")):
            t = load_template(path.stem, svc.settings.template_dir)
            out.append({"name": t.name, "description": t.description, "tools": t.tools})
        return out

    # ---------------------------------------------------------------- tool gateway
    @app.get("/api/tools", tags=["tools"])
    def list_tools(svc: Services = Depends(get_services), p: Principal = Depends(get_principal)):
        return [
            {"name": s.name, "description": s.description, "risk": s.risk, "scope": s.scope, "input_schema": s.json_schema()}
            for s in svc.tools.specs_for(p)
        ]

    @app.post("/api/tools/{name}", tags=["tools"])
    def call_tool(name: str, args: dict[str, Any], svc: Services = Depends(get_services), p: Principal = Depends(rate_limited)):
        """Generic gateway used by n8n / Power Automate. Same RBAC, validation and audit as MCP and the agent."""
        result = svc.tools.call(name, args, p)
        if not result.get("ok", True):
            code = result["error"]["code"]
            status = {
                "permission_denied": 403,
                "forbidden": 403,
                "tool_not_allowed": 404,
                "not_found": 404,
                "invalid_arguments": 422,
            }.get(code, 400 if code != "internal_error" else 500)
            raise HTTPException(status, detail=result["error"])
        return result

    # ---------------------------------------------------------------- tickets (user facing)
    @app.get("/api/tickets", tags=["tickets"])
    def my_tickets(
        status: str | None = None,
        limit: int = Query(20, le=50),
        svc: Services = Depends(get_services),
        p: Principal = Depends(get_principal),
    ):
        if p.has_scope("tickets:read:any"):
            return svc.tickets.list_for(None, status, limit)
        return svc.tickets.list_for(p.user_id, status, limit)

    @app.get("/api/tickets/{key}", tags=["tickets"])
    def one_ticket(key: str, svc: Services = Depends(get_services), p: Principal = Depends(get_principal)):
        r = svc.tools.call("get_ticket", {"ticket_id": key}, p)
        if not r.get("ok", True):
            raise HTTPException(404, "ticket not found")
        return r["ticket"]

    # ---------------------------------------------------------------- approvals (human in the loop)
    @app.get("/api/approvals", tags=["approvals"])
    def approvals(svc: Services = Depends(get_services), p: Principal = Depends(get_principal)):
        return svc.approvals.list_for(p)

    @app.post("/api/approvals/{approval_id}/decision", tags=["approvals"])
    def decide(approval_id: str, body: DecisionIn, svc: Services = Depends(get_services), p: Principal = Depends(get_principal)):
        try:
            return svc.approvals.decide(approval_id, p, approve=body.approve, note=body.note)
        except ApprovalError as exc:
            status = {"forbidden": 403, "not_found": 404, "conflict": 409, "separation_of_duties": 403}[exc.code]
            raise HTTPException(status, detail={"code": exc.code, "message": exc.message}) from exc

    # ---------------------------------------------------------------- automation (n8n / Power Automate)
    @app.post("/api/automation/classify", tags=["automation"])
    def classify(body: ClassifyIn, request: Request, p: Principal = Depends(require("tickets:create"))):
        return _agent(request, "helpdesk").classify(p, body.text)

    @app.post("/api/automation/email-triage", tags=["automation"])
    def email_triage(
        body: EmailIn, request: Request, svc: Services = Depends(get_services), p: Principal = Depends(require("tickets:create"))
    ):
        """One-call flow for Power Automate: classify -> ticket -> notify. (n8n chains the same steps node by node.)"""
        triage = _agent(request, "helpdesk").classify(p, f"{body.subject}\n{body.body}")
        ticket = svc.tools.call(
            "create_ticket",
            {
                "title": body.subject[:140] or "Email request",
                "description": body.body[:3900],
                "category": triage["category"],
                "priority": triage["priority"],
                "on_behalf_of": body.sender,
                "source": "email",
            },
            p,
        )
        if not ticket.get("ok", True):
            raise HTTPException(400, detail=ticket["error"])
        return {"triage": triage, "ticket": ticket["ticket"]}

    # ---------------------------------------------------------------- admin: audit, metrics, cost
    @app.get("/api/admin/audit", tags=["admin"])
    def audit(
        limit: int = Query(100, le=1000), svc: Services = Depends(get_services), p: Principal = Depends(require("audit:read"))
    ):
        return svc.audit.recent(limit)

    @app.get("/api/admin/audit/verify", tags=["admin"])
    def audit_verify(svc: Services = Depends(get_services), p: Principal = Depends(require("audit:read"))):
        ok, broken = svc.audit.verify()
        return {"intact": ok, "first_broken_seq": broken}

    @app.get("/api/admin/metrics", tags=["admin"])
    def metrics(svc: Services = Depends(get_services), p: Principal = Depends(require("metrics:read"))):
        return {
            "summary": svc.metrics.summary(),
            "daily": svc.metrics.requests_per_day(),
            "by_category": svc.metrics.by_category(),
            "spans": svc.metrics.slowest_spans(),
            "spend_by_team": svc.cost.spend_by_team(),
            "spend_by_model": svc.cost.spend_by_model(),
            "shadow_ai": svc.cost.shadow_ai(),
            "quarantined_chunks": svc.retriever.quarantined,
        }

    @app.get("/api/admin/traces/{request_id}", tags=["admin"])
    def trace(request_id: str, svc: Services = Depends(get_services), p: Principal = Depends(require("metrics:read"))):
        return svc.tracer.trace_for(request_id)

    @app.get("/api/admin/budgets", tags=["admin"])
    def budgets(svc: Services = Depends(get_services), p: Principal = Depends(require("metrics:read"))):
        teams = {r["team"] for r in svc.db.query("SELECT DISTINCT team FROM users")} | {
            r["team"] for r in svc.db.query("SELECT team FROM budgets")
        }
        return [vars(svc.cost.status(t)) for t in sorted(teams)]

    @app.put("/api/admin/budgets", tags=["admin"])
    def set_budget(body: BudgetIn, svc: Services = Depends(get_services), p: Principal = Depends(require("budgets:write"))):
        svc.cost.set_budget(body.team, body.monthly_usd)
        svc.audit.record(actor=p.user_id, client_id=p.client_id, action="budget:set", args=body.model_dump(), outcome="ok")
        return vars(svc.cost.status(body.team))

    @app.exception_handler(ValueError)
    async def value_error(_: Request, exc: ValueError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    return app


def run() -> None:
    import uvicorn

    uvicorn.run(
        "opspilot.api.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=8000,  # noqa: S104
        log_config=None,
        access_log=False,
    )
