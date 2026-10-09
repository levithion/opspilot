"""The helpdesk tools. Each one declares its scope and risk level; handlers do the actual work."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from opspilot.db import dumps, utcnow
from opspilot.security.guardrails import (
    NOTIFY_CHANNELS,
    RESET_SYSTEMS,
    TICKET_CATEGORIES,
)
from opspilot.security.identity import Principal
from opspilot.tools.registry import (
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    ToolContext,
    ToolError,
    ToolRegistry,
    ToolSpec,
)

Priority = Literal["low", "medium", "high", "urgent"]
Category = Literal["access", "vpn", "password", "hardware", "software", "network", "security", "licensing", "general"]
ResetSystem = Literal["vpn", "password", "mfa", "sharepoint_access", "software_license"]
Channel = Literal["it-helpdesk", "it-security", "it-oncall"]

assert set(TICKET_CATEGORIES) == set(Category.__args__)  # type: ignore[attr-defined]
assert set(RESET_SYSTEMS) == set(ResetSystem.__args__)  # type: ignore[attr-defined]
assert set(NOTIFY_CHANNELS) == set(Channel.__args__)  # type: ignore[attr-defined]


# ------------------------------------------------------------------ inputs
class SearchKB(BaseModel):
    query: str = Field(min_length=2, max_length=500, description="Natural-language question about IT policy or how-to.")
    top_k: int = Field(default=4, ge=1, le=8)


class CreateTicket(BaseModel):
    title: str = Field(min_length=5, max_length=140)
    description: str = Field(default="", max_length=4000)
    category: Category = "general"
    priority: Priority = "medium"
    on_behalf_of: str | None = Field(default=None, description="Requester email. Only IT admins and automation may set this.")
    source: str | None = Field(default=None, max_length=40, description="Channel label, e.g. email.")


class GetTicket(BaseModel):
    ticket_id: str = Field(pattern=r"^HELP-\d{4,8}$", description="Ticket key such as HELP-1001")


class ListMyTickets(BaseModel):
    status: Literal["open", "in_progress", "waiting_on_user", "resolved", "closed"] | None = None
    limit: int = Field(default=10, ge=1, le=50)


class GetUserAccess(BaseModel):
    user: str | None = Field(default=None, description="Email of the user to look up. Omit to look up yourself.")


class NotifyChannel(BaseModel):
    channel: Channel
    message: str = Field(min_length=3, max_length=1000)


class GetAISpendReport(BaseModel):
    team: str | None = Field(default=None, description="Team name. Omit for your own team.")


class ResetRequest(BaseModel):
    system: ResetSystem
    user: str | None = Field(default=None, description="Email of the account to reset. Omit for yourself.")
    reason: str = Field(min_length=5, max_length=500)


class GetApprovalStatus(BaseModel):
    approval_id: str = Field(pattern=r"^APR-[0-9A-F]{8}$")


# ------------------------------------------------------------------ helpers
def _lookup_user(ctx: ToolContext, email: str | None, principal: Principal) -> dict[str, Any]:
    target = (email or principal.email).lower()
    row = ctx.db.query_one("SELECT * FROM users WHERE email=?", (target,))
    if not row:
        raise ToolError("not_found", "No such user in the directory.")
    return row


def _assert_self_or_admin(principal: Principal, target: dict[str, Any], any_scope: str) -> None:
    if target["user_id"] != principal.user_id and not principal.has_scope(any_scope):
        raise ToolError("forbidden", "You can only do this for your own account.")


# ------------------------------------------------------------------ handlers
def search_kb(p: Principal, a: SearchKB, ctx: ToolContext) -> dict[str, Any]:
    hits = ctx.retriever.search(a.query, p, k=a.top_k)
    return {
        "results": [
            {"citation": f"[{i}]", "doc_id": h.doc_id, "title": h.title, "section": h.section, "score": h.score, "text": h.text}
            for i, h in enumerate(hits, 1)
        ],
        "count": len(hits),
    }


def create_ticket(p: Principal, a: CreateTicket, ctx: ToolContext) -> dict[str, Any]:
    from opspilot.security.pii import redact

    requester_id = p.user_id
    if a.on_behalf_of:
        if not p.has_scope("tickets:create:on_behalf"):
            raise ToolError("forbidden", "You can only create tickets for yourself.")
        row = ctx.db.query_one("SELECT user_id FROM users WHERE email=?", (a.on_behalf_of.lower(),))
        # Unknown senders (e.g. inbound email) get a pseudonymous id, never the raw address.
        requester_id = row["user_id"] if row else "external:" + hashlib.sha1(a.on_behalf_of.lower().encode()).hexdigest()[:10]
    t = ctx.tickets.create(
        requester_id=requester_id,
        title=redact(a.title).text,
        description=redact(a.description).text,  # data minimisation: no raw PII/secrets in tickets
        category=a.category,
        priority=a.priority,
        source=a.source or p.client_id,
    )
    if a.priority in ("high", "urgent"):
        channel = "it-security" if a.category == "security" else "it-helpdesk"
        ctx.notifier.send(channel, f"New {a.priority} ticket {t['key']} ({a.category}): {t['title']}")
    return {"ticket": {k: t[k] for k in ("key", "title", "category", "priority", "status", "created_at")}}


def get_ticket(p: Principal, a: GetTicket, ctx: ToolContext) -> dict[str, Any]:
    t = ctx.tickets.get(a.ticket_id)
    if not t or (t["requester_id"] != p.user_id and not p.has_scope("tickets:read:any")):
        raise ToolError("not_found", f"Ticket {a.ticket_id} was not found.")  # same answer: no existence leak
    return {
        "ticket": {
            k: t[k]
            for k in (
                "key",
                "title",
                "description",
                "category",
                "priority",
                "status",
                "assignee",
                "created_at",
                "updated_at",
                "comments",
            )
        }
    }


def list_my_tickets(p: Principal, a: ListMyTickets, ctx: ToolContext) -> dict[str, Any]:
    rows = ctx.tickets.list_for(requester_id=p.user_id, status=a.status, limit=a.limit)
    return {"tickets": [{k: t[k] for k in ("key", "title", "category", "priority", "status", "created_at")} for t in rows]}


def get_user_access(p: Principal, a: GetUserAccess, ctx: ToolContext) -> dict[str, Any]:
    target = _lookup_user(ctx, a.user, p)
    _assert_self_or_admin(p, target, "access:read:any")
    grants = ctx.db.query(
        "SELECT resource, level, granted_at FROM access_grants WHERE user_id=? ORDER BY resource", (target["user_id"],)
    )
    licenses = ctx.db.query(
        "SELECT product, seat_status, renewal_date, cost_center FROM licenses WHERE owner_user_id=? ORDER BY product",
        (target["user_id"],),
    )
    return {
        "user": {"name": target["name"], "team": target["team"], "status": target["status"]},
        "vpn_enabled": bool(target["vpn_enabled"]),
        "mfa_enrolled": bool(target["mfa_enrolled"]),
        "access_grants": grants,
        "licenses": licenses,
    }


def notify_channel(p: Principal, a: NotifyChannel, ctx: ToolContext) -> dict[str, Any]:
    if a.channel != "it-helpdesk" and not p.has_scope("notify:send:any"):
        raise ToolError("forbidden", f"You may only post to #it-helpdesk, not #{a.channel}.")
    res = ctx.notifier.send(a.channel, f"{a.message} (from {p.name})")
    return {"sent": True, **res}


def get_ai_spend_report(p: Principal, a: GetAISpendReport, ctx: ToolContext) -> dict[str, Any]:
    team = a.team or p.team
    if team != p.team and not p.has_scope("spend:read:any"):
        raise ToolError("forbidden", "You can only view the spend report for your own team.")
    return ctx.cost.team_report(team)


def reset_request(p: Principal, a: ResetRequest, ctx: ToolContext) -> dict[str, Any]:
    """Approval-gated: this only *requests* the reset. An IT admin must approve it (never the requester)."""
    target = _lookup_user(ctx, a.user, p)
    _assert_self_or_admin(p, target, "access:read:any")
    from opspilot.security.pii import redact

    params = {"system": a.system, "target_user_id": target["user_id"], "target_email": target["email"]}
    for row in ctx.db.query(
        "SELECT id, params FROM approvals WHERE status='pending' AND requested_by=? AND action='reset'", (p.user_id,)
    ):
        if json.loads(row["params"]) == params:
            return {
                "status": "pending_approval",
                "approval_id": row["id"],
                "duplicate": True,
                "message": "An identical request is already awaiting approval.",
                "_outcome": "ok:duplicate",
            }
    from uuid import uuid4

    approval_id = f"APR-{uuid4().hex[:8].upper()}"
    ctx.db.execute(
        "INSERT INTO approvals(id, action, params, requested_by, requester_team, reason, status, request_id, created_at)"
        " VALUES (?,?,?,?,?,?, 'pending', ?, ?)",
        (approval_id, "reset", dumps(params), p.user_id, p.team, redact(a.reason).text, ctx.request_id, utcnow()),
    )
    ctx.notifier.send("it-helpdesk", f"Approval needed: {a.system} reset for {target['name']} ({approval_id}).")
    return {
        "status": "pending_approval",
        "approval_id": approval_id,
        "message": f"A {a.system} reset for {target['name']} needs IT approval before it runs.",
        "_outcome": "pending_approval",
    }


def get_approval_status(p: Principal, a: GetApprovalStatus, ctx: ToolContext) -> dict[str, Any]:
    row = ctx.db.query_one("SELECT * FROM approvals WHERE id=?", (a.approval_id,))
    if not row or (row["requested_by"] != p.user_id and not p.has_scope("approvals:decide")):
        raise ToolError("not_found", f"Approval {a.approval_id} was not found.")
    return {
        "approval": {k: row[k] for k in ("id", "action", "status", "decided_at", "decision_note", "created_at")},
        "params": json.loads(row["params"]),
    }


# ------------------------------------------------------------------ registry
TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        "search_kb",
        "Search the IT knowledge base (policies, how-tos). Returns cited passages the caller is allowed to see.",
        SearchKB,
        "kb:read",
        RISK_LOW,
        search_kb,
    ),
    ToolSpec(
        "create_ticket",
        "Create a helpdesk ticket for the current user.",
        CreateTicket,
        "tickets:create",
        RISK_MEDIUM,
        create_ticket,
        read_only=False,
    ),
    ToolSpec("get_ticket", "Get one of your tickets by key (HELP-xxxx).", GetTicket, "tickets:read:own", RISK_LOW, get_ticket),
    ToolSpec(
        "list_my_tickets",
        "List your recent tickets, optionally filtered by status.",
        ListMyTickets,
        "tickets:read:own",
        RISK_LOW,
        list_my_tickets,
    ),
    ToolSpec(
        "get_user_access",
        "Show a user's access grants, VPN/MFA state and software licences. Admins can look up anyone.",
        GetUserAccess,
        "access:read:self",
        RISK_LOW,
        get_user_access,
    ),
    ToolSpec(
        "notify_channel",
        "Post a message to an IT channel (Slack/Teams). Employees may only use it-helpdesk.",
        NotifyChannel,
        "notify:send",
        RISK_MEDIUM,
        notify_channel,
        read_only=False,
    ),
    ToolSpec(
        "get_ai_spend_report",
        "Monthly GenAI token spend and budget status for a team.",
        GetAISpendReport,
        "spend:read:team",
        RISK_LOW,
        get_ai_spend_report,
    ),
    ToolSpec(
        "reset_request",
        "Request a VPN, password, MFA, SharePoint access or licence reset. APPROVAL-GATED: nothing "
        "changes until an IT admin approves.",
        ResetRequest,
        "reset:request",
        RISK_HIGH,
        reset_request,
        read_only=False,
    ),
    ToolSpec(
        "get_approval_status",
        "Check whether a reset request has been approved.",
        GetApprovalStatus,
        "reset:request",
        RISK_LOW,
        get_approval_status,
    ),
]


def build_registry(ctx: ToolContext) -> ToolRegistry:
    reg = ToolRegistry(ctx)
    for spec in TOOL_SPECS:
        reg.register(spec)
    return reg
