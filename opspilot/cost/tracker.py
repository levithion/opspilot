"""Token / cost accounting, per-team budgets with warn + hard-stop, and the shadow-AI view."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from opspilot.config import Settings
from opspilot.cost.pricing import compute_cost
from opspilot.db import Database, month_key, utcnow

KNOWN_CLIENTS = {"web", "teams", "slack", "claude-desktop", "cursor", "vscode-copilot", "n8n", "power-automate", "eval"}


class BudgetExceeded(Exception):
    def __init__(self, team: str, spent: float, budget: float):
        super().__init__(f"monthly AI budget for team '{team}' exhausted (${spent:.2f} / ${budget:.2f})")
        self.team, self.spent, self.budget = team, spent, budget


@dataclass
class BudgetStatus:
    team: str
    month: str
    spent_usd: float
    budget_usd: float
    ratio: float
    state: str  # ok | warn | blocked


class CostTracker:
    def __init__(self, db: Database, settings: Settings, alert_sink=None):
        self.db = db
        self.settings = settings
        self.alert_sink = alert_sink  # callable(team, level, message)

    # ---- budgets -------------------------------------------------------------------------
    def budget_for(self, team: str) -> float:
        row = self.db.query_one("SELECT monthly_usd FROM budgets WHERE team=?", (team,))
        return float(row["monthly_usd"]) if row else self.settings.default_team_budget_usd

    def set_budget(self, team: str, monthly_usd: float) -> None:
        self.db.execute(
            "INSERT INTO budgets(team, monthly_usd) VALUES (?,?)"
            " ON CONFLICT(team) DO UPDATE SET monthly_usd=excluded.monthly_usd",
            (team, monthly_usd),
        )

    def spent(self, team: str, month: str | None = None) -> float:
        m = month or month_key()
        v = self.db.scalar("SELECT COALESCE(SUM(cost_usd),0) FROM usage_events WHERE team=? AND substr(ts,1,7)=?", (team, m))
        return float(v or 0.0)

    def status(self, team: str) -> BudgetStatus:
        budget, spent = self.budget_for(team), self.spent(team)
        ratio = spent / budget if budget > 0 else 1.0
        state = "blocked" if ratio >= 1.0 else "warn" if ratio >= self.settings.budget_warn_ratio else "ok"
        return BudgetStatus(team, month_key(), round(spent, 6), budget, round(ratio, 4), state)

    def enforce(self, team: str) -> BudgetStatus:
        """Call before every LLM call. Raises BudgetExceeded once the team hits 100%."""
        st = self.status(team)
        if st.state == "blocked":
            self._alert(team, "blocked", st)
            raise BudgetExceeded(team, st.spent_usd, st.budget_usd)
        return st

    # ---- recording -----------------------------------------------------------------------
    def record(
        self,
        *,
        team: str,
        user_id: str | None,
        client_id: str,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        request_id: str | None = None,
    ) -> float:
        cost = 0.0 if provider == "ollama" else compute_cost(model, input_tokens, output_tokens)
        self.db.execute(
            "INSERT INTO usage_events(ts, request_id, team, user_id, client_id, provider, model,"
            " input_tokens, output_tokens, cost_usd)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (utcnow(), request_id, team, user_id, client_id or "unknown", provider, model, input_tokens, output_tokens, cost),
        )
        st = self.status(team)
        if st.state in ("warn", "blocked"):
            self._alert(team, st.state, st)
        return cost

    def _alert(self, team: str, level: str, st: BudgetStatus) -> None:
        """Alert once per team / month / level."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO budget_alerts(team, month, level, ts) VALUES (?,?,?,?)",
            (team, st.month, level, utcnow()),
        )
        if cur.rowcount and self.alert_sink:
            verb = "reached the warning threshold" if level == "warn" else "exhausted its budget"
            self.alert_sink(
                team, level, f"AI budget: team {team} has {verb} (${st.spent_usd:.2f} of ${st.budget_usd:.2f}, {st.ratio:.0%})."
            )

    # ---- reporting -----------------------------------------------------------------------
    def spend_by_team(self, month: str | None = None) -> list[dict[str, Any]]:
        m = month or month_key()
        rows = self.db.query(
            "SELECT team, COUNT(*) calls, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,"
            " ROUND(SUM(cost_usd),6) cost_usd FROM usage_events WHERE substr(ts,1,7)=? GROUP BY team ORDER BY cost_usd DESC",
            (m,),
        )
        for r in rows:
            r["budget_usd"] = self.budget_for(r["team"])
            r["ratio"] = round(r["cost_usd"] / r["budget_usd"], 4) if r["budget_usd"] else None
        return rows

    def spend_by_model(self, month: str | None = None) -> list[dict[str, Any]]:
        return self.db.query(
            "SELECT provider, model, COUNT(*) calls, SUM(input_tokens+output_tokens) tokens, ROUND(SUM(cost_usd),6) cost_usd"
            " FROM usage_events WHERE substr(ts,1,7)=? GROUP BY provider, model ORDER BY cost_usd DESC",
            (month or month_key(),),
        )

    def team_report(self, team: str) -> dict[str, Any]:
        st = self.status(team)
        by_model = self.db.query(
            "SELECT model, COUNT(*) calls, SUM(input_tokens+output_tokens) tokens, ROUND(SUM(cost_usd),6) cost_usd"
            " FROM usage_events WHERE team=? AND substr(ts,1,7)=? GROUP BY model",
            (team, st.month),
        )
        return {
            "team": team,
            "month": st.month,
            "spent_usd": st.spent_usd,
            "budget_usd": st.budget_usd,
            "ratio": st.ratio,
            "state": st.state,
            "by_model": by_model,
        }

    def shadow_ai(self, month: str | None = None) -> list[dict[str, Any]]:
        """Usage grouped by client / API key. Clients that are not registered stand out as shadow AI."""
        rows = self.db.query(
            "SELECT client_id, COUNT(*) calls, COUNT(DISTINCT user_id) users, SUM(input_tokens+output_tokens) tokens,"
            " ROUND(SUM(cost_usd),6) cost_usd FROM usage_events WHERE substr(ts,1,7)=? GROUP BY client_id ORDER BY cost_usd DESC",
            (month or month_key(),),
        )
        for r in rows:
            r["registered"] = r["client_id"] in KNOWN_CLIENTS
            r["flag"] = "unregistered client" if not r["registered"] else ""
        return rows
