# OpsPilot

[![CI](https://github.com/levithion/opspilot/actions/workflows/ci.yml/badge.svg)](https://github.com/levithion/opspilot/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
![Cost](https://img.shields.io/badge/runs%20for-%240-brightgreen)

**A secure, observable, cost-governed AI helpdesk.** An employee asks "I can't connect to the VPN" or "who owns this
licence?". OpsPilot answers from company knowledge (RAG with citations) and performs *approved* actions (open a ticket,
look up access, request a reset, notify a channel) through [MCP](https://modelcontextprotocol.io) tools. Every step is
permission-checked, logged in a tamper-evident audit trail, and counted against a team budget.

It runs **fully offline with no API keys and no cost**. A deterministic mock LLM and a local embedder are the defaults,
so the whole platform, its tests and its demo work on a laptop. Real models are opt-in.

![OpsPilot chat: request, tool calls and approval reference](docs/screenshots/chat-approval-pending.png)

## What it demonstrates

| Capability | How |
|---|---|
| **One tool server, many clients** | A FastMCP server with 9 tools. The same process works with Claude Desktop, Cursor and VS Code/Copilot, and an HTTP gateway gives n8n and Power Automate the same tools. |
| **Policy enforced in one place** | Every call from MCP, the agent or HTTP passes through a single registry: allow-list, scope check (RBAC), schema validation, output screening, audit, tracing. |
| **Human approval for risky actions** | `reset_request` can only file an approval. A *different* IT admin must approve it; self-approval and double decisions are rejected; the approver is recorded in the audit log. |
| **Access-aware RAG** | Qdrant search with role and team filters applied *inside* the vector search, so restricted chunks never take a top-K slot. Hybrid ranking (dense score plus term coverage). |
| **Responsible AI** | PII and secret redaction before text reaches an LLM, a log, a ticket or a notification; prompt-injection screening of user input, retrieved documents and tool output; least-privilege tool visibility. |
| **Identity** | Microsoft Entra ID OIDC bearer tokens (RS256, issuer/audience/expiry checks, app-only tokens for automation), role-to-scope mapping. Dev tokens for local use. |
| **Cost governance** | Token and cost ledger per team, budgets that warn at 80% and stop at 100%, and a shadow-AI view that flags usage from unregistered clients. |
| **Observability** | Per-request traces (agent, LLM and tool spans), structured JSON logs, metrics dashboard; optional Langfuse mirroring. |
| **Reusable agents** | LangGraph workflow (intake guardrails, triage, resolver loop, finalize) configured by YAML templates: prompt, tool allow-list, policies. |
| **Automation** | n8n workflow and Power Automate guide for email to classify to ticket to notify. |

## Architecture

```
 Employee / Claude Desktop / Cursor / n8n / Power Automate
            │  Entra ID bearer token  ->  Principal (user, team, roles -> scopes)
            ▼
   FastAPI  ·  MCP server          rate limit, request id, security headers
            │
   LangGraph agent:  intake ─► triage ─► resolve ⇄ act ─► finalize
            │                  (guardrails, budget check at every LLM call)
            ▼
   ToolRegistry.call  ── allow-list ► scope ► validate ► run ► screen output ► audit ► trace
      │            │             │               │               │
   Retriever    Ticketing     Directory       Notifier        Approvals
   + Qdrant     (SQLite or    (access,        (Slack/Teams    (IT admin decides,
   (role/team    REST API)     licences)       + outbox)       executes, audits)
    filters)
```

Design notes and known limits: [docs/architecture.md](docs/architecture.md).

## Screenshots

Captured from the running app with the offline demo data (see [docs/screenshots](docs/screenshots)).

### Connected to Claude Desktop (MCP)
Claude Desktop discovers all 9 tools from the MCP server. Calls it makes are permission-checked and audited server-side.

![OpsPilot's 9 tools discovered by Claude Desktop](docs/screenshots/claude-desktop-tools.png)

### Answers with citations
![Cited answer from the knowledge base](docs/screenshots/chat-search-kb.png)

### Human approval for risky actions
The assistant can only *request* a reset. An IT admin approves it in the console, and only then does it run.

![Reset request pending approval](docs/screenshots/chat-approval-pending.png)
![Admin sees the pending request](docs/screenshots/approvals-console-pending.png)
![After approval the request is executed](docs/screenshots/approvals-console-approved.png)

### Guardrails
![Prompt-injection attempt blocked](docs/screenshots/chat-injection-blocked.png)
![Phone number and email redacted in the stored ticket](docs/screenshots/chat-pii-redacted-ticket.png)

### Operations and cost dashboard
![Dashboard overview](docs/screenshots/dashboard-top.png)
![Spend versus budget per team](docs/screenshots/spend-vs-budget.png)
![Usage by client with unregistered clients flagged](docs/screenshots/shadow-ai.png)
![Hash-chained audit log with approver](docs/screenshots/audit-log.png)

<details>
<summary>Full dashboard page</summary>

![Full dashboard](docs/screenshots/dashboard-full.png)

</details>

### API
![OpenAPI documentation](docs/screenshots/api-docs.png)

## Quickstart

```bash
git clone https://github.com/levithion/opspilot.git && cd opspilot
uv venv --python 3.12 .venv && source .venv/bin/activate      # or: python -m venv .venv
uv pip install -e ".[dev]"                                     # or: pip install -e ".[dev]"

opspilot-api                    # http://localhost:8000   (chat UI, /dashboard, /docs)
python scripts/demo.py          # in a second terminal: realistic traffic for the dashboard
```

Open `http://localhost:8000`, choose **Alice** and ask "Please reset my VPN, it keeps failing". Switch to **Carol Costa**
(IT admin) to approve it and to open `/dashboard`.

**Use it from Claude Desktop, Cursor or VS Code:** see [docs/mcp-clients.md](docs/mcp-clients.md).

**Docker:** `docker compose up --build` starts the API and a Qdrant server (`OPSPILOT_PORT=8010` if 8000 is busy).

### Using a real model (optional)

| Option | Cost | Setup |
|---|---|---|
| Mock (default) | free | nothing |
| Ollama (local models) | free | `ollama pull llama3.2`, then `OPSPILOT_LLM_PROVIDER=ollama` |
| Anthropic / OpenAI | **paid per token** | set `OPSPILOT_ALLOW_PAID_LLM=true` plus the provider and API key; the app refuses to start them otherwise |

Copy `.env.example` to `.env` for all settings.

## Quality and results

Measured with the offline mock LLM and hashing embedder (not a model-quality benchmark):

| Check | Result |
|---|---|
| Automated tests | 113 passing, about 89% line coverage, including a real stdio MCP subprocess test |
| CI | GitHub Actions: ruff, pytest with a coverage gate, retrieval-quality gate, Docker build and smoke test |
| Retrieval (40 hand-written Q&A) | hit@1 95%, hit@4 100%, MRR 0.969 |
| Access-control leakage probes | 0 of 7 restricted documents leaked |
| Verified from Claude Desktop | search with citation, approval-gated reset, permission denial, approval status, all in the audit log |

The retrieval set is small and written by the same author as the knowledge base, so read it as a regression gate, not as
general accuracy. Full numbers, commands and what is **not** verified: [docs/RESULTS.md](docs/RESULTS.md).

```bash
pytest --cov=opspilot                  # tests
python -m opspilot.rag.evaluate        # retrieval hit-rate and access-leak check
python automation/simulate.py          # email -> ticket automation on 30 labelled emails
```

## Repository map

| Path | Contents |
|---|---|
| `opspilot/mcp_server.py` | FastMCP server (9 tools, 1 prompt) |
| `opspilot/tools/` | Tool registry, tool definitions, approval service |
| `opspilot/agent/`, `agent_templates/` | LangGraph workflow and reusable YAML agent templates |
| `opspilot/rag/`, `kb/`, `eval/` | Chunking, embeddings, Qdrant store, evaluation; sample knowledge base and test sets |
| `opspilot/security/` | PII redaction, injection guardrails, OIDC auth, RBAC, hash-chained audit log |
| `opspilot/cost/`, `opspilot/observability/` | Cost and budgets, tracing, metrics, logging |
| `opspilot/api/` | FastAPI app, chat UI and dashboard |
| `opspilot/ticketing/` | Mock ticketing service and standalone REST API |
| `automation/` | n8n workflow, labelled emails, simulation script |
| `docs/` | Architecture, MCP clients, security and GDPR note, Entra ID, automation, observability, runbook |

## Security and privacy

Threat model, controls and GDPR mapping: [docs/security-gdpr.md](docs/security-gdpr.md). Entra ID setup:
[docs/entra-id.md](docs/entra-id.md). The guardrails are pattern-based first-line defences, not a guarantee, and
the project has not had an external security review.

## Status and limitations

- **Verified:** the test suite and CI, the Docker image and API plus Qdrant compose stack, the web UI (screenshots in
  `docs/screenshots`), and MCP from Claude Desktop.
- **Not verified:** Cursor and VS Code/Copilot (configs provided), a live Entra ID tenant, Slack/Teams webhooks, n8n
  import, Power Automate, Langfuse, Ollama, and any cloud deployment.
- **Demo-grade pieces:** SQLite (single replica), in-process rate limiting, simulated effects for approved resets, a
  seeded demo directory, and regex-based PII and injection detection. The production changes are listed in
  [docs/architecture.md](docs/architecture.md#known-limits-what-changes-for-production).
- The knowledge base in `kb/` is invented sample content.

## Documentation

[Architecture](docs/architecture.md) · [MCP clients](docs/mcp-clients.md) · [Security and GDPR](docs/security-gdpr.md) ·
[Entra ID](docs/entra-id.md) · [Automation](docs/automation.md) · [Observability and budgets](docs/observability.md) ·
[Runbook](docs/runbook.md) · [Results](docs/RESULTS.md)
