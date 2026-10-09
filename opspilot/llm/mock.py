"""Deterministic offline "LLM".

It exists so the full platform (agent loop, guardrails, approvals, cost tracking, tests, demos) runs
without API keys. It is a rule-based planner that emits real tool calls and builds an extractive,
cited answer from tool results. It is NOT a quality benchmark for the real models.
"""

from __future__ import annotations

import json
import re
from typing import Any

from opspilot.cost.pricing import estimate_tokens
from opspilot.llm.types import LLMResponse, Message, ToolCall, ToolDef, Usage
from opspilot.rag.embeddings import tokenize

_SYSTEM_PATTERNS = [
    ("vpn", re.compile(r"\bvpn\b|secure connect", re.I)),
    ("mfa", re.compile(r"\bmfa\b|authenticator|2fa|multi.?factor|temporary access pass", re.I)),
    ("password", re.compile(r"password|passcode|locked out|account (?:is )?locked", re.I)),
    ("sharepoint_access", re.compile(r"sharepoint|teams site|document library", re.I)),
    ("software_license", re.compile(r"licen[sc]e|seat", re.I)),
]
_CATEGORY_PATTERNS = [
    ("security", re.compile(r"phish|malware|breach|stolen|suspicious|ransom|leak|compromised|hacked", re.I)),
    ("vpn", re.compile(r"\bvpn\b|secure connect|gateway", re.I)),
    ("password", re.compile(r"password|locked out|unlock|passcode", re.I)),
    ("access", re.compile(r"\bmfa\b|authenticator|sharepoint|access|permission|teams site|folder", re.I)),
    ("licensing", re.compile(r"licen[sc]e|seat|subscription", re.I)),
    ("hardware", re.compile(r"laptop|monitor|keyboard|mouse|printer|headset|dock", re.I)),
    ("network", re.compile(r"wi-?fi|network|internet|ethernet|dns", re.I)),
    ("software", re.compile(r"install|software|outlook|excel|teams app|update|crash", re.I)),
]
_ACTION_RESET = re.compile(
    r"^(?!\s*(?:how|what|where|why|when|is|are|does|do)\b).*(?:\bplease\b.*\breset\b|\bcan (?:you|someone|it)\b.*\breset\b|"
    r"\bi need\b.*\breset\b|\breset (?:my|the)\b|locked out|lost my phone|re-?issue)",
    re.I,
)
_QUESTION_START = re.compile(r"^\s*(?:how|what|where|why|when|which|who|can i|do i|does|is|are)\b", re.I)


def classify(text: str) -> dict[str, Any]:
    category = next((name for name, rx in _CATEGORY_PATTERNS if rx.search(text)), "general")
    if re.search(r"outage|down for (?:everyone|all)|everyone (?:is|are) (?:affected|locked)|breach|ransom", text, re.I):
        priority = "urgent"
    elif re.search(r"can(?:'|no)?t work|cannot work|blocked|urgent|asap|stolen|phish|compromised", text, re.I):
        priority = "high"
    elif re.search(r"\bhow (?:do|can) i\b|\bwhat is\b|question|wondering", text, re.I):
        priority = "low"
    else:
        priority = "medium"
    return {"category": category, "priority": priority, "needs_human": category == "security" or priority == "urgent"}


def _system_for(text: str) -> str | None:
    return next((name for name, rx in _SYSTEM_PATTERNS if rx.search(text)), None)


def _best_sentences(query: str, passages: list[tuple[str, str]], limit: int = 3) -> list[tuple[int, str]]:
    """Pick the sentences that overlap most with the query; the section title counts as extra context."""
    q = set(tokenize(query))
    scored = []
    for pi, (section, passage) in enumerate(passages):
        section_tokens = set(tokenize(section))
        for si, sent in enumerate(re.split(r"(?<=[.!?])\s+|\n+", passage)):
            sent = sent.strip(" -")
            if len(sent) < 25:
                continue
            overlap = len(q & set(tokenize(sent))) + 0.5 * len(q & section_tokens)
            scored.append((overlap, -pi, -si, sent))
    top = sorted(scored, reverse=True)[:limit]
    return [(-pi, s) for _, pi, _, s in sorted(top, key=lambda t: (-t[1], -t[2]))]


class MockLLM:
    provider = "mock"
    model = "mock-llm"

    def complete(
        self,
        system: str,
        messages: list[Message],
        tools: list[ToolDef] | None = None,
        *,
        max_tokens: int = 800,
        meta: dict[str, Any] | None = None,
    ) -> LLMResponse:
        meta = meta or {}
        if meta.get("task") == "triage":
            out = json.dumps(classify(messages[-1]["content"]))
            return self._resp(system, messages, out, [])
        available = {t.name for t in (tools or [])}
        return self._resolve(system, messages, available, meta)

    # ------------------------------------------------------------------
    def _resp(self, system: str, messages: list[Message], text: str, calls: list[ToolCall]) -> LLMResponse:
        in_tok = estimate_tokens(system + "".join(str(m.get("content", "")) for m in messages))
        out_tok = estimate_tokens(text + json.dumps([c.arguments for c in calls]))
        return LLMResponse(text, calls, Usage(in_tok, out_tok), self.model, self.provider)

    def _resolve(self, system: str, messages: list[Message], available: set[str], meta: dict[str, Any]) -> LLMResponse:
        request = next(m["content"] for m in messages if m["role"] == "user")
        called = [tc.name for m in messages if m["role"] == "assistant" for tc in m.get("tool_calls", [])]
        results = {m["name"]: json.loads(m["content"]) for m in messages if m["role"] == "tool"}
        triage = meta.get("triage") or classify(request)
        n = len(called)

        def call(name: str, **args: Any) -> LLMResponse:
            return self._resp(system, messages, "", [ToolCall(f"mock_{n}_{name}", name, args)])

        if "search_kb" in available and "search_kb" not in called:
            return call("search_kb", query=request[:480])

        kb_empty = "search_kb" in results and not results["search_kb"].get("results")
        ticket_id = re.search(r"\bHELP-\d{4,8}\b", request)
        approval_id = re.search(r"\bAPR-[0-9A-F]{8}\b", request)

        if ticket_id and "get_ticket" in available and "get_ticket" not in called:
            return call("get_ticket", ticket_id=ticket_id.group(0))
        if approval_id and "get_approval_status" in available and "get_approval_status" not in called:
            return call("get_approval_status", approval_id=approval_id.group(0))
        if (
            re.search(r"\b(?:my|open) tickets\b", request, re.I)
            and "list_my_tickets" in available
            and "list_my_tickets" not in called
        ):
            return call("list_my_tickets")
        if (
            re.search(r"who owns|my access|what access|which (?:access|licen[sc]es)|my licen[sc]es|permissions", request, re.I)
            and "get_user_access" in available
            and "get_user_access" not in called
        ):
            return call("get_user_access")
        if (
            re.search(
                r"\b(?:ai|genai|llm|tokens?)\b.{0,30}\b(?:spen[dt]|cost|budget|usage)|"
                r"\b(?:spen[dt]|cost|budget)\b.{0,30}\b(?:on|for)?\s*(?:ai|genai|llm|tokens?)\b",
                request,
                re.I,
            )
            and "get_ai_spend_report" in available
            and "get_ai_spend_report" not in called
        ):
            return call("get_ai_spend_report")
        system_name = _system_for(request)
        if system_name and _ACTION_RESET.search(request) and "reset_request" in available and "reset_request" not in called:
            return call("reset_request", system=system_name, reason=request[:480])
        wants_ticket = re.search(r"\b(?:open|raise|create|log|file)\b.{0,25}\b(?:ticket|case|issue)\b", request, re.I) and not (
            _QUESTION_START.match(request)
        )
        fallback = kb_empty and not _QUESTION_START.match(request)
        if (
            (wants_ticket or fallback)
            and "create_ticket" in available
            and "create_ticket" not in called
            and "reset_request" not in called
        ):
            title = re.sub(r"\s+", " ", request).strip()[:110]
            return call(
                "create_ticket",
                title=title if len(title) >= 5 else "Helpdesk request",
                description=request[:1500],
                category=triage["category"],
                priority=triage["priority"],
            )
        return self._resp(system, messages, self._compose(request, results), [])

    def _compose(self, request: str, results: dict[str, Any]) -> str:
        parts: list[str] = []
        kb = results.get("search_kb", {})
        acted = any(n in results for n in ("reset_request", "create_ticket", "notify_channel"))
        # When an action ran, background KB text only adds noise; answer with the action outcome.
        passages = [] if acted else kb.get("results", [])
        if passages:
            picked = _best_sentences(request, [(p["section"], p["text"]) for p in passages[:4]])
            used = list(dict.fromkeys(pi for pi, _ in picked))  # passages that actually contributed a sentence
            if picked:
                parts.append(" ".join(s for _, s in picked) + " [1]")
            passages = [passages[i] for i in used] or passages[:1]
        for name in (
            "reset_request",
            "create_ticket",
            "get_ticket",
            "get_approval_status",
            "list_my_tickets",
            "get_user_access",
            "get_ai_spend_report",
            "notify_channel",
        ):
            r = results.get(name)
            if not r:
                continue
            if not r.get("ok", True):
                parts.append(f"I couldn't complete that: {r['error']['message']}")
            elif name == "reset_request":
                parts.append(f"{r['message']} Your approval reference is {r['approval_id']}.")
            elif name == "create_ticket":
                t = r["ticket"]
                parts.append(f"I opened ticket {t['key']} ({t['category']}, {t['priority']} priority).")
            elif name == "get_ticket":
                t = r["ticket"]
                parts.append(f"Ticket {t['key']} is {t['status']}: {t['title']}.")
            elif name == "get_approval_status":
                a = r["approval"]
                parts.append(f"Approval {a['id']} is {a['status']}.")
            elif name == "list_my_tickets":
                ts = r["tickets"]
                parts.append(
                    "Your tickets: " + ("; ".join(f"{t['key']} ({t['status']}) {t['title']}" for t in ts) or "none") + "."
                )
            elif name == "get_user_access":
                g = "; ".join(f"{x['resource']} ({x['level']})" for x in r["access_grants"]) or "none"
                lic = "; ".join(f"{x['product']} renews {x['renewal_date']}" for x in r["licenses"]) or "none"
                parts.append(
                    f"Access: {g}. Licences: {lic}. VPN {'enabled' if r['vpn_enabled'] else 'not enabled'}, "
                    f"MFA {'enrolled' if r['mfa_enrolled'] else 'not enrolled'}."
                )
            elif name == "get_ai_spend_report":
                parts.append(
                    f"Team {r['team']} has used ${r['spent_usd']:.2f} of its ${r['budget_usd']:.2f} AI budget "
                    f"this month ({r['ratio']:.0%}, status {r['state']})."
                )
            elif name == "notify_channel":
                parts.append(f"I posted your message to #{r['channel']}.")
        if not parts:
            parts.append("I couldn't find an answer in the knowledge base. Would you like me to open a ticket?")
        if passages:
            titles = list(dict.fromkeys(p["title"] for p in passages))[:3]
            parts.append("Sources: " + "; ".join(f"[{i}] {t}" for i, t in enumerate(titles, 1)))
        return "\n\n".join(parts)
