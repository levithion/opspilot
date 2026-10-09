# OpsPilot quickstart

**This project costs nothing to run.** Everything below works offline with no API keys: the default LLM is a
deterministic mock and the default embedder is a local hashing embedder. Qdrant, n8n, SQLite, the MCP server and
the dashboard are all free/open source.

| Want | Free way | Costs money (opt-in only) |
|---|---|---|
| A real LLM | `OPSPILOT_LLM_PROVIDER=ollama` with [Ollama](https://ollama.com) running local models | `anthropic` / `openai` need `OPSPILOT_ALLOW_PAID_LLM=true`; otherwise the app refuses to start them |
| Semantic embeddings | `pip install '.[embeddings]'` + `OPSPILOT_EMBEDDING_BACKEND=sentence-transformers` (local model download) | – |
| Hosting | run locally / `docker compose up`, or any container host with a free tier (the Dockerfile is portable; set a spending cap first) | any paid cloud |
| Tracing | built-in SQLite traces; self-hosted Langfuse | Langfuse Cloud beyond its free tier |
| SSO | dev auth mode; Microsoft 365 Developer tenant if eligible | – |
| CI | GitHub Actions free allowance (unlimited on public repos; limited minutes on private) | – |

## 1. Install

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate     # or: python -m venv .venv
uv pip install -e ".[dev]"                                    # or: pip install -e ".[dev]"
cp .env.example .env                                          # optional
```

Optional extras: `.[embeddings]` (sentence-transformers) and `.[tracing]` (Langfuse).

## 2. Run the platform

```bash
opspilot-api                    # http://localhost:8000  (chat UI, /dashboard, /docs)
python scripts/demo.py          # sends realistic traffic so the dashboard has data
```

Open `http://localhost:8000`, pick a user from the "Sign in as" box (dev auth mode) and chat. Sign in as
**Carol Costa** (IT admin) to approve resets and to open `/dashboard`.

| Demo user | Role | Try |
|---|---|---|
| alice@opspilot.example | employee | "Please reset my VPN, it keeps failing" (creates a pending approval) |
| bob@opspilot.example | team_lead | "How much has my team spent on AI this month?" |
| carol@opspilot.example | it_admin | approve/reject resets, dashboard, audit log |
| dave@opspilot.example | employee (Finance) | sees the Finance-only KB document |
| automation@opspilot.example | automation | service principal for n8n / Power Automate |

## 3. Use it from Claude Desktop / Cursor / VS Code (MCP)

```bash
python -m opspilot.mcp_server            # stdio, what MCP clients spawn
```

Client configuration snippets are in [docs/mcp-clients.md](docs/mcp-clients.md).

## 4. Test, evaluate, lint

```bash
pytest --cov=opspilot                    # unit + integration + a real stdio MCP subprocess test
python -m opspilot.rag.evaluate          # retrieval hit-rate on eval/qa.jsonl + access-leak check
python automation/simulate.py            # email -> ticket automation on a labelled test set
ruff check . && ruff format --check .
```

Measured numbers and how they were produced: [docs/RESULTS.md](docs/RESULTS.md).

## 5. Docker

```bash
docker compose up --build                          # api + qdrant
docker compose --profile ticketing --profile automation up   # + standalone ticketing API + n8n
```

> Verified: the image builds, runs as a non-root user, passes `/ready`, and answers chat. `docker compose up`
> was verified with the API using the Qdrant server (69 points indexed). Set `OPSPILOT_PORT=8010` if port 8000
> is busy. Qdrant is not published to the host, so it will not clash with a Qdrant you already run on 6333.
> The `ticketing` and `automation` (n8n) profiles have not been started.

## Project map

| Path | What |
|---|---|
| `opspilot/mcp_server.py` | FastMCP server: 9 tools, 1 prompt |
| `opspilot/tools/` | Tool registry (RBAC, validation, screening, audit) and definitions, approval service |
| `opspilot/agent/` | LangGraph workflow, reusable YAML templates (`agent_templates/`) |
| `opspilot/rag/` | Chunking, embeddings, Qdrant store with access filters, evaluation |
| `opspilot/security/` | PII redaction, prompt-injection guardrails, OIDC/JWT auth, RBAC, hash-chained audit log |
| `opspilot/cost/`, `opspilot/observability/` | Token cost + budgets + shadow-AI, tracing, metrics, JSON logs |
| `opspilot/api/` | FastAPI app, chat UI + dashboard (`static/`) |
| `opspilot/ticketing/` | Mock ticketing service and standalone REST API |
| `kb/`, `eval/` | Knowledge base documents, 40 Q&A pairs, 7 access-leak probes |
| `automation/` | n8n workflow, labelled emails, simulation script |
| `docs/` | Architecture, MCP clients, security/GDPR note, Entra ID, automation, observability, runbook |
| `.github/workflows/` | CI: lint, tests with coverage gate, retrieval-quality gate, Docker build and smoke test |
