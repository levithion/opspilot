"""Service container: builds and wires every component once (no module-level globals)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opspilot.config import Settings, get_settings
from opspilot.cost.tracker import CostTracker
from opspilot.db import Database
from opspilot.notify import Notifier
from opspilot.observability.logging_setup import configure_logging
from opspilot.observability.metrics import Metrics
from opspilot.observability.tracing import Tracer
from opspilot.rag.embeddings import build_embedder
from opspilot.rag.retriever import Retriever
from opspilot.rag.store import KnowledgeStore
from opspilot.security.audit import AuditLog
from opspilot.security.auth import TokenVerifier
from opspilot.seed import seed_demo_data
from opspilot.ticketing.service import HttpTicketBackend, LocalTicketBackend
from opspilot.tools.approvals import ApprovalService
from opspilot.tools.definitions import build_registry
from opspilot.tools.registry import ToolContext, ToolRegistry


@dataclass
class Services:
    settings: Settings
    db: Database
    audit: AuditLog
    tracer: Tracer
    notifier: Notifier
    cost: CostTracker
    store: KnowledgeStore
    retriever: Retriever
    tickets: Any
    tools: ToolRegistry
    approvals: ApprovalService
    metrics: Metrics
    verifier: TokenVerifier

    def close(self) -> None:
        self.db.close()


def build_services(settings: Settings | None = None, *, ingest: bool = True) -> Services:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    db = Database(settings.resolved_db_path)
    if settings.seed_demo_data and settings.env != "prod":
        seed_demo_data(db)
    audit = AuditLog(db)
    tracer = Tracer(db, settings)
    notifier = Notifier(db, settings)
    cost = CostTracker(db, settings, alert_sink=notifier.budget_alert)
    store = KnowledgeStore(settings, build_embedder(settings))
    if ingest:
        store.ingest()
    retriever = Retriever(store, settings)
    tickets = (
        HttpTicketBackend(settings.ticket_api_url, settings.ticket_api_token)
        if settings.ticket_api_url
        else LocalTicketBackend(db)
    )
    ctx = ToolContext(
        settings=settings, db=db, retriever=retriever, tickets=tickets, notifier=notifier, cost=cost, audit=audit, tracer=tracer
    )
    return Services(
        settings=settings,
        db=db,
        audit=audit,
        tracer=tracer,
        notifier=notifier,
        cost=cost,
        store=store,
        retriever=retriever,
        tickets=tickets,
        tools=build_registry(ctx),
        approvals=ApprovalService(db, audit, tickets, notifier),
        metrics=Metrics(db),
        verifier=TokenVerifier(settings),
    )
