# Architecture

```
 Employee ──► Chat UI / Teams / Slack / n8n / Power Automate / MCP clients
                  │  Entra ID (OIDC) bearer token  →  Principal(user, team, roles → scopes)
                  ▼
   FastAPI (opspilot/api) ───────────────────────────────┐        MCP server (opspilot/mcp_server.py)
        │ rate limit, request-id, security headers       │          (Claude Desktop, Cursor, VS Code)
        ▼                                                │                   │
   Agent workflow (LangGraph)                            │                   │
     intake ─► triage ─► resolve ⇄ act ─► finalize       │                   │
       │guardrails  │LLM     │LLM+tools   │                                  │
       ▼            ▼        ▼            ▼                                  ▼
   ┌──────────────────────── ToolRegistry.call  (the single choke point) ────────────────────────┐
   │ allow-list → scope check (RBAC) → pydantic validation → handler → injection screen → audit │
   └───────┬──────────────┬───────────────┬───────────────┬──────────────┬──────────────────────┘
           ▼              ▼               ▼               ▼              ▼
     Retriever+Qdrant  Ticketing      Directory/access  Notifier     Approvals (human in the loop)
     (role/team filter  (SQLite or     (SQLite)         (Slack/Teams  (IT admin decides; executes
      inside search)     REST API)                       + outbox)     the action; audit has approver)
   Cost tracker (tokens → $, team budgets, shadow-AI) · Tracer (spans → SQLite and optionally Langfuse)
```

## Design decisions worth defending

1. **One choke point for tools.** MCP, the agent and the HTTP gateway all call `ToolRegistry.call`. Policy
   (allow-list, scopes, validation, output screening, auditing, tracing) is enforced once, so adding a client
   cannot create a bypass. Tests assert the same behaviour through each entry point.
2. **Approval gating is in the tool, not the prompt.** `reset_request` can only create an approval row. The
   effect runs in `ApprovalService.decide`, which requires `approvals:decide`, rejects self-approval and
   double decisions, and writes the approver into the audit record. A jailbroken model cannot skip it.
3. **Least privilege reaches the model.** The LLM is only told about tools its principal may use and the
   template allows; a denied call is still audited.
4. **Hybrid, access-aware RAG.** Ranking = dense score + term-coverage; filters run **inside Qdrant** (role audience + team payload filters) before top-K, so
   forbidden chunks can never occupy a result slot. `eval/leakage.jsonl` probes this.
5. **Untrusted text is data.** Retrieved chunks and tool outputs are screened for injection patterns and
   quarantined (never forwarded) above a threshold; user input is blocked on the same scoring.
6. **PII is removed before it leaves the trust boundary:** before the LLM, in logs, audit args, tickets,
   notifications. Audit records hold pseudonymous ids only.
7. **Offline-first.** The mock LLM and hashing embedder make CI, demos and tests hermetic. The mock is a
   rule-based planner: it proves the plumbing, not model quality.

## Request lifecycle (`/api/chat`)

1. Auth → `Principal`; rate limit. 2. `intake`: length/injection check, PII redaction, budget check.
3. `triage`: LLM classifies category/priority. 4. `resolve`: LLM with permitted tools. 5. `act`: each tool call
through the registry; approvals recorded. 6. Loop until an answer or `max_steps`. 7. `finalize`: output
filter, citations, request row, cost, spans.

## Known limits (what changes for production)

| Today | Production change |
|---|---|
| SQLite single file, single replica | Postgres; the `Database` wrapper is the only seam |
| In-process rate limiter | Redis or API Management policy |
| Approvals poll in UI / API | Queue + Teams adaptive-card approvals, expiry |
| Simulated reset effects (`ApprovalService._execute`) | Microsoft Graph / VPN admin API calls with a dedicated managed identity |
| Directory in SQLite seeded with demo users | Entra ID / HR system sync; SCIM |
| MCP HTTP transport has no per-user identity | Put behind an authenticating gateway or use MCP OAuth |
| Hash embedder (lexical) | sentence-transformers or API embeddings, hybrid search + reranker |
| Regex PII/injection detection | Add a classifier (e.g. Presidio, Azure AI Content Safety / Prompt Shields) |
| Multi-tenant isolation is by team field | Per-tenant collections and databases |
