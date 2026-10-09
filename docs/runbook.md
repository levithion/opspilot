# Runbook

## Health

* `GET /health` – process is up. `GET /ready` – DB reachable and KB indexed (used by probes). 503 on `/ready` means
  the KB index is empty (check `OPSPILOT_KB_DIR`) or the DB is locked/unavailable.
* Logs are JSON on stderr with `request_id`; every response carries `x-request-id`.

## Debugging a failing agent run

1. Get the `request_id` (response header or `answer` metadata in the UI).
2. `GET /api/admin/traces/{request_id}` → span list: which step failed (`llm.*` error, `tool.*` error), latency, tokens.
3. Audit trail: `GET /api/admin/audit?limit=200` and filter by `request_id` – shows allowed/denied tool calls and outcomes
   (`denied:<scope>`, `rejected:invalid_args`, `error:<code>`, `pending_approval`).
4. Typical causes:

| Symptom | Likely cause | Action |
|---|---|---|
| `status: error`, "went wrong on my side" | LLM/provider exception (key, quota, network) | check span `llm.resolve` `error`; verify `ANTHROPIC_API_KEY`; provider retries 2× on transient errors |
| `blocked` / `budget_exhausted` | team hit 100 % budget | `PUT /api/admin/budgets` (audited) or wait for next month |
| `blocked` / "possible prompt injection" | guardrail score ≥ threshold | review the audit `agent:request` args (labels, score); tune `OPSPILOT_INJECTION_BLOCK_THRESHOLD` carefully |
| Tool returns `permission_denied` | principal lacks scope | `GET /api/me` shows scopes; fix Entra app-role assignment |
| Empty/poor answers | KB doesn't cover it, or doc audience/team excludes the user | run `python -m opspilot.rag.evaluate`; check doc front matter |
| 401 on every call in OIDC mode | wrong `OIDC_AUDIENCE`/tenant, clock skew, v1 token | decode token at jwt.ms; expect `ver: 2.0`, `aud` equal to `OPSPILOT_OIDC_AUDIENCE` |
| 429 | rate limit (default 30/min/user) | raise `OPSPILOT_RATE_LIMIT_PER_MINUTE` or fix the noisy client |
| Webhook alerts missing | Slack/Teams webhook failing | `notifications` table shows `via=outbox, delivered=0`; check URL; sends retry 3× with backoff |

## Routine tasks

* **Reindex KB** after editing `kb/*.md`: restart the API (it re-ingests on start) or `opspilot-ingest` for a Qdrant server.
* **Audit integrity:** `python -m opspilot.cli audit-verify` (exit code 1 on a broken chain) – run weekly.
* **Backups:** SQLite file in `OPSPILOT_DATA_DIR` (`opspilot.db`, WAL mode): use `sqlite3 .backup`, not a raw copy.
* **Retention purge (to implement):** delete `ticket_comments`/`tickets` older than 24 months, `spans`/`requests` older
  than 90 days. Never delete from `audit_log` (triggers block it by design).
* **Rotate secrets:** update Key Vault secret → new container app revision. Dev JWT secret is unused in OIDC mode.

## Failure modes by design

* Notification delivery failure never fails a request (outbox row is written regardless).
* Langfuse outage never fails a request (mirroring is best-effort).
* A crashing tool returns `internal_error` to the model, is logged with a stack trace, and is audited.
* The agent loop is bounded by `policies.max_steps`; LangGraph `recursion_limit` is a second backstop.
