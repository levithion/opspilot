"""LangGraph agent workflow: intake guardrails -> triage -> resolver loop (LLM <-> tools) -> finalize.

Risky actions never execute inside the loop: `reset_request` only files an approval, which an IT admin
decides out-of-band (see ApprovalService). The graph surfaces that as status `pending_approval`.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from opspilot.agent.template import AgentTemplate
from opspilot.container import Services
from opspilot.cost.tracker import BudgetExceeded
from opspilot.db import dumps, utcnow
from opspilot.llm.providers import build_llm
from opspilot.llm.types import ToolDef
from opspilot.observability.logging_setup import request_id_var
from opspilot.security.guardrails import check_output, check_user_input
from opspilot.security.identity import Principal

log = logging.getLogger("opspilot.agent")


class AgentState(TypedDict, total=False):
    request_id: str
    principal: Principal
    channel: str
    raw_text: str
    clean_text: str
    messages: list[dict[str, Any]]
    triage: dict[str, Any]
    steps: int
    answer: str
    status: str
    blocked_reason: str | None
    guardrail_blocked: bool
    tool_events: list[dict[str, Any]]
    approvals: list[str]
    citations: list[dict[str, str]]
    cost_usd: float
    redactions: dict[str, int]


@dataclass
class AgentResult:
    request_id: str
    answer: str
    status: str  # resolved | blocked | pending_approval | error
    category: str | None = None
    priority: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    approvals: list[str] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    blocked_reason: str | None = None
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class HelpdeskAgent:
    def __init__(self, template: AgentTemplate, services: Services, llm=None):
        self.template, self.svc = template, services
        self.llm = llm or build_llm(services.settings, template.provider)
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        g = StateGraph(AgentState)
        g.add_node("intake", self._intake)
        g.add_node("triage", self._triage)
        g.add_node("resolve", self._resolve)
        g.add_node("act", self._act)
        g.add_node("finalize", self._finalize)
        g.set_entry_point("intake")
        g.add_conditional_edges("intake", lambda s: "finalize" if s.get("status") == "blocked" else "triage")
        g.add_edge("triage", "resolve")
        g.add_conditional_edges("resolve", self._after_resolve, {"act": "act", "finalize": "finalize"})
        g.add_edge("act", "resolve")
        g.add_edge("finalize", END)
        return g.compile()

    @staticmethod
    def _after_resolve(state: AgentState) -> str:
        return "finalize" if state.get("answer") or state.get("status") in ("blocked", "error") else "act"

    # ------------------------------------------------------------------ nodes
    def _intake(self, state: AgentState) -> AgentState:
        s, p = self.svc.settings, state["principal"]
        decision = check_user_input(state["raw_text"], max_chars=s.max_input_chars, threshold=s.injection_block_threshold)
        self.svc.audit.record(
            actor=p.user_id,
            client_id=p.client_id,
            action="agent:request",
            args={
                "channel": state.get("channel"),
                "labels": decision.labels,
                "redactions": decision.redactions,
                "injection_score": decision.injection_score,
            },
            outcome="allowed" if decision.allowed else f"blocked:{decision.reason}",
            request_id=state["request_id"],
        )
        if not decision.allowed:
            return {
                "status": "blocked",
                "guardrail_blocked": True,
                "blocked_reason": decision.reason,
                "answer": f"I can't help with that request ({decision.reason}). If this is a genuine IT issue, "
                "please rephrase it or open a ticket.",
            }
        try:
            self.svc.cost.enforce(p.team)
        except BudgetExceeded as exc:
            return {
                "status": "blocked",
                "blocked_reason": "budget_exhausted",
                "answer": f"AI assistance is paused for your team: {exc} Please contact IT to raise the budget.",
            }
        return {
            "clean_text": decision.text,
            "redactions": decision.redactions,
            "messages": [{"role": "user", "content": decision.text}],
        }

    def _llm(self, state: AgentState, name: str, system: str, messages, tools=None, meta=None, max_tokens=None):
        p = state["principal"]
        self.svc.cost.enforce(p.team)
        with self.svc.tracer.span(state["request_id"], f"llm.{name}", provider=self.llm.provider, model=self.llm.model) as sp:
            resp = self.llm.complete(
                system, messages, tools, max_tokens=max_tokens or self.template.policies.max_output_tokens, meta=meta
            )
            sp.update(
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                tool_calls=[c.name for c in resp.tool_calls],
            )
        cost = self.svc.cost.record(
            team=p.team,
            user_id=p.user_id,
            client_id=p.client_id,
            provider=resp.provider or self.llm.provider,
            model=resp.model or self.llm.model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            request_id=state["request_id"],
        )
        return resp, cost

    def _triage(self, state: AgentState) -> AgentState:
        if not self.template.triage.enabled:
            return {"triage": {"category": "general", "priority": "medium", "needs_human": False}}
        try:
            resp, cost = self._llm(
                state,
                "triage",
                self.template.triage.prompt,
                [{"role": "user", "content": state["clean_text"]}],
                meta={"task": "triage"},
                max_tokens=120,
            )
            raw = resp.text.strip()
            data = json.loads(raw[raw.index("{") : raw.rindex("}") + 1])
            triage = {
                "category": str(data.get("category", "general")),
                "priority": str(data.get("priority", "medium")),
                "needs_human": bool(data.get("needs_human", False)),
            }
        except BudgetExceeded:
            raise
        except Exception as exc:
            log.warning("triage failed, defaulting: %s", exc)
            triage, cost = {"category": "general", "priority": "medium", "needs_human": False}, 0.0
        return {"triage": triage, "cost_usd": state.get("cost_usd", 0.0) + cost}

    def _system_prompt(self, state: AgentState) -> str:
        p = state["principal"]
        return (
            f"{self.template.system_prompt.strip()}\n\n"
            f"Current user: {p.name} (team: {p.team}, roles: {', '.join(sorted(p.roles))}).\n"
            f"Triage: {json.dumps(state.get('triage', {}))}"
        )

    def _tool_defs(self, principal: Principal) -> list[ToolDef]:
        return [ToolDef(s.name, s.description, s.json_schema()) for s in self.svc.tools.specs_for(principal, self.template.tools)]

    def _resolve(self, state: AgentState) -> AgentState:
        steps = state.get("steps", 0)
        if steps >= self.template.policies.max_steps:
            return {
                "answer": "I wasn't able to finish this within my step limit. I've kept what I did so far; "
                "please open a ticket if it's still unresolved.",
                "status": "resolved",
            }
        p = state["principal"]
        try:
            resp, cost = self._llm(
                state,
                "resolve",
                self._system_prompt(state),
                state["messages"],
                self._tool_defs(p),
                meta={"task": "resolve", "triage": state.get("triage")},
            )
        except BudgetExceeded as exc:
            return {"status": "blocked", "blocked_reason": "budget_exhausted", "answer": f"AI assistance is paused: {exc}"}
        total = state.get("cost_usd", 0.0) + cost
        msgs = list(state["messages"]) + [{"role": "assistant", "content": resp.text, "tool_calls": resp.tool_calls}]
        if resp.tool_calls:
            return {"messages": msgs, "steps": steps + 1, "cost_usd": total}
        return {"messages": msgs, "steps": steps + 1, "cost_usd": total, "answer": resp.text}

    def _act(self, state: AgentState) -> AgentState:
        p = state["principal"]
        last = state["messages"][-1]
        msgs = list(state["messages"])
        events, approvals, citations = (
            list(state.get("tool_events", [])),
            list(state.get("approvals", [])),
            list(state.get("citations", [])),
        )
        for call in last["tool_calls"]:  # type: ToolCall
            result = self.svc.tools.call(
                call.name, call.arguments, p, request_id=state["request_id"], allowed=self.template.tools
            )
            events.append({"tool": call.name, "ok": result.get("ok", True), "error": (result.get("error") or {}).get("code")})
            if result.get("status") == "pending_approval":
                approvals.append(result["approval_id"])
            if call.name == "search_kb":
                citations = [
                    {"doc_id": r["doc_id"], "title": r["title"], "section": r["section"]} for r in result.get("results", [])
                ]
            msgs.append({"role": "tool", "tool_call_id": call.id, "name": call.name, "content": json.dumps(result, default=str)})
        return {"messages": msgs, "tool_events": events, "approvals": approvals, "citations": citations}

    def _finalize(self, state: AgentState) -> AgentState:
        answer, found = check_output(state.get("answer") or "I could not produce an answer.")
        acted = any(e["tool"] in ("reset_request", "create_ticket", "notify_channel") for e in state.get("tool_events", []))
        if (
            self.template.policies.require_citations
            and state.get("citations")
            and not acted
            and "[1]" not in answer
            and "Sources:" not in answer
        ):
            titles = list(dict.fromkeys(c["title"] for c in state["citations"]))[:3]
            answer += "\n\nSources: " + "; ".join(f"[{i}] {t}" for i, t in enumerate(titles, 1))
        status = state.get("status") or ("pending_approval" if state.get("approvals") else "resolved")
        if state.get("approvals") and status == "resolved":
            status = "pending_approval"
        return {"answer": answer, "status": status, "redactions": {**state.get("redactions", {}), **found}}

    # ------------------------------------------------------------------ public API
    def classify(self, principal: Principal, text: str, *, request_id: str | None = None) -> dict[str, Any]:
        """Triage-only LLM call (used by email automation). Input is guarded and redacted first."""
        rid = request_id or uuid.uuid4().hex[:16]
        s = self.svc.settings
        decision = check_user_input(
            text[: s.max_input_chars * 2], max_chars=s.max_input_chars * 2, threshold=s.injection_block_threshold
        )
        if not decision.allowed:
            return {"category": "security", "priority": "high", "needs_human": True, "flagged": decision.reason}
        state: AgentState = {"request_id": rid, "principal": principal, "clean_text": decision.text}
        out = self._triage(state)
        return {**out["triage"], "cost_usd": out.get("cost_usd", 0.0), "flagged": None}

    def run(self, principal: Principal, message: str, *, channel: str = "chat", request_id: str | None = None) -> AgentResult:
        rid = request_id or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            with self.svc.tracer.span(rid, "agent.run", agent=self.template.name, channel=channel, user=principal.user_id):
                state = self.graph.invoke(
                    {
                        "request_id": rid,
                        "principal": principal,
                        "channel": channel,
                        "raw_text": message,
                        "steps": 0,
                        "tool_events": [],
                        "approvals": [],
                        "citations": [],
                        "cost_usd": 0.0,
                    },
                    {"recursion_limit": 60},
                )
            status = state["status"]
        except Exception as exc:
            log.exception("agent run failed")
            state, status = (
                {
                    "answer": "Something went wrong on my side. I've logged it; please try again or open a ticket.",
                    "cost_usd": 0.0,
                },
                "error",
            )
            state["blocked_reason"] = type(exc).__name__
        latency = (time.perf_counter() - t0) * 1000
        triage = state.get("triage") or {}
        result = AgentResult(
            request_id=rid,
            answer=state.get("answer", ""),
            status=status,
            category=triage.get("category"),
            priority=triage.get("priority"),
            tool_calls=state.get("tool_events", []),
            approvals=state.get("approvals", []),
            citations=state.get("citations", []),
            blocked_reason=state.get("blocked_reason"),
            cost_usd=round(state.get("cost_usd", 0.0), 8),
            latency_ms=round(latency, 2),
        )
        self.svc.db.execute(
            "INSERT OR REPLACE INTO requests(request_id, ts, user_id, team, client_id, channel, status, category,"
            " guardrail_blocked,"
            " latency_ms, cost_usd, tool_calls, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                utcnow(),
                principal.user_id,
                principal.team,
                principal.client_id,
                channel,
                status,
                result.category,
                int(bool(state.get("guardrail_blocked"))),
                result.latency_ms,
                result.cost_usd,
                len(result.tool_calls),
                dumps({"blocked_reason": result.blocked_reason, "redactions": state.get("redactions", {})}),
            ),
        )
        request_id_var.reset(token)
        return result
