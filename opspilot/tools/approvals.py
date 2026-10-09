"""Human-in-the-loop approvals: decide pending high-risk requests and execute them once approved."""

from __future__ import annotations

import json
from typing import Any

from opspilot.db import Database, dumps, utcnow
from opspilot.security.audit import AuditLog
from opspilot.security.identity import PermissionDenied, Principal, require_scope


class ApprovalError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class ApprovalService:
    def __init__(self, db: Database, audit: AuditLog, tickets, notifier):
        self.db, self.audit, self.tickets, self.notifier = db, audit, tickets, notifier

    def list_pending(self, principal: Principal) -> list[dict[str, Any]]:
        require_scope(principal, "approvals:decide")
        rows = self.db.query("SELECT * FROM approvals WHERE status='pending' ORDER BY created_at")
        return [self._public(r) for r in rows]

    def list_for(self, principal: Principal) -> list[dict[str, Any]]:
        if principal.has_scope("approvals:decide"):
            rows = self.db.query("SELECT * FROM approvals ORDER BY created_at DESC LIMIT 100")
        else:
            rows = self.db.query(
                "SELECT * FROM approvals WHERE requested_by=? ORDER BY created_at DESC LIMIT 50", (principal.user_id,)
            )
        return [self._public(r) for r in rows]

    @staticmethod
    def _public(r: dict[str, Any]) -> dict[str, Any]:
        out = {
            k: r[k]
            for k in (
                "id",
                "action",
                "requested_by",
                "requester_team",
                "reason",
                "status",
                "decided_by",
                "decided_at",
                "decision_note",
                "created_at",
            )
        }
        out["params"] = json.loads(r["params"])
        out["result"] = json.loads(r["result"]) if r["result"] else None
        return out

    def decide(self, approval_id: str, approver: Principal, *, approve: bool, note: str = "") -> dict[str, Any]:
        try:
            require_scope(approver, "approvals:decide")
        except PermissionDenied as exc:
            raise ApprovalError("forbidden", "Only IT administrators can decide approvals.") from exc
        row = self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not row:
            raise ApprovalError("not_found", f"Approval {approval_id} not found.")
        if row["status"] != "pending":
            raise ApprovalError("conflict", f"Approval {approval_id} is already {row['status']}.")
        if row["requested_by"] == approver.user_id:
            raise ApprovalError("separation_of_duties", "You cannot decide your own request.")

        now = utcnow()
        if not approve:
            self.db.execute(
                "UPDATE approvals SET status='rejected', decided_by=?, decided_at=?, decision_note=? WHERE id=?",
                (approver.user_id, now, note, approval_id),
            )
            self.audit.record(
                actor=approver.user_id,
                client_id=approver.client_id,
                action="approval:reject",
                args={"approval_id": approval_id, "note": note},
                outcome="rejected",
                request_id=row["request_id"],
                approved_by=None,
            )
            return self._public(self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,)))  # type: ignore[arg-type]

        params = json.loads(row["params"])
        try:
            result = self._execute(row["action"], params, requester=row["requested_by"], approver=approver)
            status = "executed"
        except Exception as exc:
            result, status = {"error": str(exc)}, "failed"
        self.db.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=?, decision_note=?, result=? WHERE id=?",
            (status, approver.user_id, now, note, dumps(result), approval_id),
        )
        self.audit.record(
            actor=row["requested_by"],
            client_id=approver.client_id,
            action=f"approved_action:{row['action']}",
            args={"approval_id": approval_id, **params},
            outcome=status,
            request_id=row["request_id"],
            approved_by=approver.user_id,
        )
        return self._public(self.db.query_one("SELECT * FROM approvals WHERE id=?", (approval_id,)))  # type: ignore[arg-type]

    def _execute(self, action: str, params: dict[str, Any], *, requester: str, approver: Principal) -> dict[str, Any]:
        """Simulated effects against the demo directory. In production this calls Entra ID / the VPN admin API."""
        if action != "reset":
            raise ValueError(f"unsupported approved action '{action}'")
        system, uid = params["system"], params["target_user_id"]
        if system == "vpn":
            self.db.execute("UPDATE users SET vpn_enabled=1 WHERE user_id=?", (uid,))
            effect = "VPN device certificate revoked and re-issued; restart the client."
        elif system == "password":
            effect = "Temporary password generated and delivered to the user's manager through the secure channel."
        elif system == "mfa":
            self.db.execute("UPDATE users SET mfa_enrolled=0 WHERE user_id=?", (uid,))
            effect = "MFA methods cleared; a Temporary Access Pass (8h) was issued for re-enrolment."
        elif system == "sharepoint_access":
            effect = "SharePoint access request forwarded to the site owner with admin approval attached."
        else:
            effect = "Licence seat unassigned and returned to the pool."
        t = self.tickets.create(
            requester_id=requester,
            title=f"{system} reset executed",
            category="access",
            description=f"Approved by {approver.user_id}. {effect}",
            priority="medium",
            source="approval",
        )
        self.tickets.set_status(t["key"], "resolved")
        return {"system": system, "effect": effect, "ticket": t["key"]}
