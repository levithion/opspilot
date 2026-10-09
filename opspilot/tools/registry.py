"""Tool registry: the single choke point every tool call passes through.

Order of checks for every call: allow-list -> scope (RBAC) -> argument validation -> execution ->
output screening -> audit -> trace. MCP, the agent and the HTTP API all call `ToolRegistry.call`, so
policy is enforced once, not per client.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from opspilot.config import Settings
from opspilot.observability.logging_setup import request_id_var
from opspilot.security.guardrails import sanitize_untrusted
from opspilot.security.identity import PermissionDenied, Principal

log = logging.getLogger("opspilot.tools")

RISK_LOW, RISK_MEDIUM, RISK_HIGH = "low", "medium", "high"


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    scope: str
    risk: str
    handler: Callable[[Principal, BaseModel, ToolContext], dict[str, Any]]
    read_only: bool = True

    def json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema.pop("title", None)
        return schema


@dataclass
class ToolContext:
    """Dependencies handed to tool handlers (no globals, easy to fake in tests)."""

    settings: Settings
    db: Any
    retriever: Any
    tickets: Any
    notifier: Any
    cost: Any
    audit: Any
    tracer: Any
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    def __init__(self, ctx: ToolContext):
        self.ctx = ctx
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def specs_for(self, principal: Principal, allowed: list[str] | None = None) -> list[ToolSpec]:
        """Tools this principal may see (least privilege also applies to what the LLM is told about)."""
        return [s for n, s in sorted(self._tools.items()) if (allowed is None or n in allowed) and principal.has_scope(s.scope)]

    def call(
        self,
        name: str,
        args: dict[str, Any],
        principal: Principal,
        *,
        request_id: str | None = None,
        allowed: list[str] | None = None,
        approved_by: str | None = None,
    ) -> dict[str, Any]:
        """Execute a tool. Always returns a dict (never raises) so agents can reason about failures."""
        rid = request_id or request_id_var.get()
        ctx = self.ctx
        spec = self._tools.get(name)
        actor, client = principal.user_id, principal.client_id

        def deny(outcome: str, code: str, message: str) -> dict[str, Any]:
            ctx.audit.record(
                actor=actor,
                client_id=client,
                action=f"tool:{name}",
                args=args,
                outcome=outcome,
                request_id=rid,
                approved_by=approved_by,
            )
            return {"ok": False, "error": {"code": code, "message": message}}

        if spec is None or (allowed is not None and name not in allowed):
            return deny("denied:not_allowed", "tool_not_allowed", f"Tool '{name}' is not available.")
        try:
            if not principal.has_scope(spec.scope):
                raise PermissionDenied(f"You do not have permission to use '{name}'.", spec.scope)
            try:
                parsed = spec.input_model(**(args or {}))
            except ValidationError as exc:
                msgs = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
                return deny("rejected:invalid_args", "invalid_arguments", msgs)

            t0 = time.perf_counter()
            tctx = ToolContext(**{**ctx.__dict__, "request_id": rid, "extra": {**ctx.extra, "approved_by": approved_by}})
            with ctx.tracer.span(rid, f"tool.{name}", user=actor, client=client, risk=spec.risk):
                result = spec.handler(principal, parsed, tctx)
            result = self._screen(name, result)
            outcome = result.pop("_outcome", "ok" if result.get("ok", True) else "error")
            ctx.audit.record(
                actor=actor,
                client_id=client,
                action=f"tool:{name}",
                args=parsed.model_dump(),
                outcome=outcome,
                request_id=rid,
                approved_by=approved_by,
            )
            result.setdefault("ok", True)
            result["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
            return result
        except PermissionDenied as exc:
            return deny(f"denied:{exc.scope}", "permission_denied", exc.args[0])
        except ToolError as exc:
            return deny(f"error:{exc.code}", exc.code, exc.message)
        except Exception as exc:  # unexpected: log with context, return a safe message
            log.exception("tool %s crashed", name)
            return deny("error:internal", "internal_error", f"Tool '{name}' failed unexpectedly ({type(exc).__name__}).")

    def _screen(self, name: str, result: dict[str, Any]) -> dict[str, Any]:
        """Tool output is untrusted data: quarantine anything that looks like injected instructions."""

        def walk(v: Any) -> Any:
            if isinstance(v, str):
                text, _ = sanitize_untrusted(v, source=f"tool:{name}", threshold=self.ctx.settings.injection_block_threshold)
                return text
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [walk(x) for x in v]
            return v

        return walk(result)
