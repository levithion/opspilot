# Measured results

Numbers below were produced by the commands shown, on 2026-10-09, with the **offline mock LLM and hashing
embedder** (no API keys). They validate the platform's plumbing, retrieval, security controls and accounting.
They are **not** model-quality results: re-run with real providers before quoting accuracy or latency anywhere.

| Metric | Value | How to reproduce |
|---|---|---|
| MCP tools exposed | 9 (+1 prompt) | `python -m opspilot.mcp_server`, `tests/test_mcp.py` |
| MCP verification | via official client: in-memory and a real stdio subprocess | `pytest tests/test_mcp.py` |
| Approval flow end to end from Claude Desktop | verified 2026-10-09: reset requested (pending) → denied spend report → IT admin approved in the web console → `get_approval_status` returned executed; audit chain intact | `docs/mcp-clients.md` |
| MCP apps actually connected | **Claude Desktop verified** (2026-10-09: `search_kb` called from a chat, logged in the audit table as client `claude-desktop`). Cursor and Copilot not yet verified | configs in `docs/mcp-clients.md` |
| Automated tests | 113 passed | `pytest` |
| Line coverage | 90 % (gate in CI: 85 %) | `pytest --cov=opspilot` |
| Lint | ruff clean | `ruff check . && ruff format --check .` |
| KB | 18 documents, 69 chunks | `GET /ready` |
| Retrieval test set | 40 Q&A pairs | `python -m opspilot.rag.evaluate` |
| Retrieval hit@1 / hit@3 / hit@4 | 95.0 % / 97.5 % / 100 %, MRR 0.969 (dense-only baseline was 92.5 % / 95.0 % / 95.0 %, MRR 0.938) | same |
| Access-control leakage probes | 0 leaks / 7 | same (`eval/leakage.jsonl`) |
| Email automation test set | 30 labelled emails → 30 tickets, 9 high/urgent notifications | `python automation/simulate.py` |
| Triage accuracy (mock rules) | category 93.3 %, priority 96.7 % | same |
| Handling time vs manual | not reported: with the mock, latency is ~1 ms so the percentage is meaningless | rerun with a real provider and your own `--manual-minutes` assumption |
| Guardrails (live demo run of 14 requests) | 2 blocked (14.3 %), 10 resolved, 2 pending approval | `python scripts/demo.py` |
| Cost per resolved request (demo run) | $0.00077 **at the mock's nominal price** | dashboard / `GET /api/admin/metrics` |
| Hash-chain audit | intact after demo run | `GET /api/admin/audit/verify` |

Retrieval is hybrid: dense (hashing embedder) score plus a fixed 0.3 weight on the share of query terms a chunk
contains. The weight was set before measuring and not tuned, but I did read the baseline's misses when deciding to add
a lexical signal, and the 40 questions were written by the same author as the knowledge base, so treat 100 % hit@4 as
an optimistic figure for a tiny set, not a general accuracy claim. Remaining weak spots: `q12` (Authenticator setup)
ranks 4th and `q40` (leaver's mailbox) 2nd. A semantic embedder (`OPSPILOT_EMBEDDING_BACKEND=sentence-transformers`,
extra `.[embeddings]`) is the next thing to try. Answer *text* from the offline mock is a crude sentence extract; use a
real model to judge answer quality.

## Not verified in the authoring environment

* Real Claude/OpenAI API calls (adapter translation is unit-tested with fake SDK clients). These are paid and blocked unless `OPSPILOT_ALLOW_PAID_LLM=true`; the free real-model path is Ollama (`OPSPILOT_LLM_PROVIDER=ollama`), which is also untested here because no Ollama server was available.
* Live Entra ID tenant, Langfuse server, Slack/Teams webhooks, n8n import, Power Automate.
* The Azure deployment (no Azure CLI), and the compose `ticketing` / `automation` (n8n) profiles. The base image and the api + Qdrant compose stack were built and run.
* The browser UI was syntax-checked (`node --check`) and its endpoints exercised over HTTP, but not clicked through.

## How to turn these into resume bullets honestly

Use only what you have run: e.g. "9-tool MCP server, 105 tests / 90 % coverage, 40-question retrieval set at 95 %
hit@4 with 0/7 access leaks" is true today; "integrated with Claude Desktop and Cursor" becomes true after you
connect them and record the demo; accuracy and handling-time claims need a real-provider run.
