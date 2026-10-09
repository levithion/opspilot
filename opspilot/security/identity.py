"""Principals, roles and least-privilege scopes (RBAC)."""

from __future__ import annotations

from dataclasses import dataclass, field

ROLE_EMPLOYEE = "employee"
ROLE_TEAM_LEAD = "team_lead"
ROLE_IT_ADMIN = "it_admin"
ROLE_AUTOMATION = "automation"  # service principal used by n8n / Power Automate

_EMPLOYEE_SCOPES = frozenset(
    {
        "kb:read",
        "tickets:create",
        "tickets:read:own",
        "access:read:self",
        "notify:send",
        "reset:request",
    }
)

ROLE_SCOPES: dict[str, frozenset[str]] = {
    ROLE_EMPLOYEE: _EMPLOYEE_SCOPES,
    ROLE_TEAM_LEAD: _EMPLOYEE_SCOPES | {"spend:read:team"},
    ROLE_IT_ADMIN: _EMPLOYEE_SCOPES
    | {
        "tickets:read:any",
        "tickets:create:on_behalf",
        "access:read:any",
        "notify:send:any",
        "spend:read:team",
        "spend:read:any",
        "approvals:decide",
        "audit:read",
        "budgets:write",
        "metrics:read",
    },
    ROLE_AUTOMATION: frozenset({"kb:read", "tickets:create", "tickets:create:on_behalf", "notify:send"}),
}

# Entra ID app-role names (or aliases) -> OpsPilot roles
ROLE_ALIASES = {
    "employee": ROLE_EMPLOYEE,
    "team_lead": ROLE_TEAM_LEAD,
    "teamlead": ROLE_TEAM_LEAD,
    "it_admin": ROLE_IT_ADMIN,
    "it.admin": ROLE_IT_ADMIN,
    "itadmin": ROLE_IT_ADMIN,
    "automation": ROLE_AUTOMATION,
}


def normalise_roles(raw: list[str] | str | None) -> frozenset[str]:
    if raw is None:
        return frozenset({ROLE_EMPLOYEE})
    items = [raw] if isinstance(raw, str) else list(raw)
    roles = {ROLE_ALIASES[r.strip().lower()] for r in items if r.strip().lower() in ROLE_ALIASES}
    return frozenset(roles or {ROLE_EMPLOYEE})


@dataclass(frozen=True)
class Principal:
    user_id: str
    email: str
    name: str
    team: str
    roles: frozenset[str] = field(default_factory=lambda: frozenset({ROLE_EMPLOYEE}))
    client_id: str = "web"

    @property
    def scopes(self) -> frozenset[str]:
        out: set[str] = set()
        for r in self.roles:
            out |= ROLE_SCOPES.get(r, frozenset())
        return frozenset(out)

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_admin(self) -> bool:
        return ROLE_IT_ADMIN in self.roles

    def with_client(self, client_id: str) -> Principal:
        return Principal(self.user_id, self.email, self.name, self.team, self.roles, client_id)


class PermissionDenied(Exception):
    def __init__(self, message: str, scope: str | None = None):
        super().__init__(message)
        self.scope = scope


def require_scope(principal: Principal, scope: str) -> None:
    if not principal.has_scope(scope):
        raise PermissionDenied(f"missing scope '{scope}'", scope)
