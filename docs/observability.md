# Observability and cost governance

## Signals

| Signal | Where |
|---|---|
| Structured JSON logs (request id, PII-redacted) | stderr; `logging_setup.py` |
| Traces: `agent.run` → `llm.triage`/`llm.resolve` → `tool.<name>` with latency, tokens, status | SQLite `spans`; `GET /api/admin/traces/{request_id}` |
| Langfuse (optional) | set `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`; `pip install '.[tracing]'`. Spans are mirrored best-effort and never break a request |
| Request ledger (status, latency, cost, guardrail block) | SQLite `requests` |
| Token / cost ledger per call | SQLite `usage_events`, priced from `cost/pricing.py` |

Self-hosting Langfuse: use Langfuse's own `docker-compose.yml`, create a project, and paste the keys above.
The mirroring code was written against the Langfuse v2 Python SDK and has **not** been run against a live instance.

## Dashboard (`/dashboard`, it_admin)

Requests, resolved/pending, p50/p95 latency, error rate, % blocked by guardrails, cost per resolved request,
spend vs budget per team, **shadow-AI** table (usage by `X-Client-Id`; unregistered clients are flagged), spend
by model, slowest spans, audit-chain status. The same data is JSON at `GET /api/admin/metrics`.

## Budgets

* Default `OPSPILOT_DEFAULT_TEAM_BUDGET_USD`; override per team with `PUT /api/admin/budgets`.
* ≥ 80 %: one alert per team/month to `#it-helpdesk` (Slack/Teams webhook or outbox).
* ≥ 100 %: agent calls are refused before any LLM spend ("budget_exhausted") and one alert goes to `#it-oncall`.
* Pricing is a static table; update `cost/pricing.py` when vendor prices change.

## Suggested alerts (Azure Monitor / Log Analytics)

* `/ready` failing for > 2 min. * HTTP 5xx rate > 2 % over 5 min. * `guardrail_block_pct` jump > 3× baseline.
* Any new `client_id` with `registered=false`. * `audit/verify` returning `intact:false` (page security).
