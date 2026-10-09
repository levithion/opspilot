# Project Idea: OpsPilot, an Enterprise IT Helpdesk Agent Platform

One project designed to cover every gap in your resume against the Netradyne AI Automation Engineer JD, so that you can build it once and defend it end to end in an interview.

**Pitch (one line):** A secure, observable, cost-governed AI helpdesk that takes an employee request ("I can't access SharePoint", "reset my VPN", "who owns this license?"), answers it from company knowledge (RAG), and performs approved actions (create ticket, check access, notify Slack/Teams) through MCP tools, with every step logged, budgeted and permission-checked.

---

## 1. Why this project

| JD theme | How OpsPilot covers it |
|---|---|
| Reusable agents, workflows, MCP connectors | Core of the project: MCP server plus agent workflows |
| Integrate with Claude, OpenAI, Copilot, Cursor | The same MCP server is plugged into several clients |
| Automate business workflows | n8n and/or Power Automate pipelines around the agent |
| Software-engineering practices | Git, pytest, CI, Docker, structured logging |
| Partner with function teams, scale from prototype to production | Reusable "agent template" others can copy |
| Responsible AI, GDPR, InfoSec | PII redaction, guardrails, least-privilege scopes, audit log |
| Govern Gen AI budgets, shadow-AI spend | Token and cost tracker with per-team budgets and alerts |
| Monitor and troubleshoot in production | Tracing, dashboards, alerts |
| Microsoft 365 / SharePoint / Power Platform | SharePoint as the knowledge source, Power Automate flow |
| Cloud (Azure / AWS) | Deployed on Azure (or AWS) |
| Identity (Entra ID, SSO, LDAP, VPN) | Entra ID / OAuth2 login and role-based access |

---

## 2. Architecture (high level)

```
 Employee ──► Chat UI (FastAPI + simple web page / Teams / Slack)
                 │   (Entra ID / OAuth2 login, roles)
                 ▼
          Agent Orchestrator (LangGraph or Claude Agent SDK / OpenAI Agents SDK)
           │        │          │              │
           │        │          │              └─► Guardrails (PII redaction, prompt-injection checks,
           │        │          │                   action allow-list, human approval for risky actions)
           │        │          └─► Cost & Usage Tracker (tokens, $ per team, budget alerts)
           │        └─► RAG pipeline (embeddings + Qdrant, SharePoint / docs as source, access-aware)
           ▼
     MCP Server (Python, FastMCP)  ── tools:
        • search_kb(query)            • create_ticket(...)
        • get_user_access(user)       • notify_channel(...)
        • get_ai_spend_report(team)   • reset_request(...)  [approval-gated]
           ▲
           └── Same server connected to: Claude Desktop, Cursor, GitHub Copilot / VS Code, OpenAI-based client

 n8n / Power Automate: inbound email → classify (LLM) → ticket → notify, calling the same tools via HTTP/MCP
 Observability: Langfuse (or OpenTelemetry) traces + structured logs + dashboard
 Deployment: Docker → Azure App Service / Container Apps (or AWS Lambda / ECS), GitHub Actions CI/CD
```

---

## 3. Build plan (phased, so you always have something showable)

### Phase 1: MCP server and core tools (the biggest JD gap)
- Build a Python MCP server (official MCP Python SDK / FastMCP) exposing 4-6 tools above, backed by a mock ticketing API (SQLite + FastAPI) so no paid system is needed.
- Connect it to **Claude Desktop** and **Cursor** and record a short demo.
- Add tests with `pytest` (tool inputs, error cases) and a GitHub Actions CI job.
- **Outcome to record:** number of tools, clients connected, test coverage.

### Phase 2: RAG over a knowledge base
- Ingest IT policy / FAQ documents (use public or self-written docs; SharePoint export if you have a developer tenant).
- Chunk, embed, store in Qdrant (you already know it from Lumina); answer with citations.
- Make retrieval **access-aware**: each chunk carries a team/role tag and the user's role filters results (reuse your "filter inside Qdrant before top-K" idea from Lumina).
- **Outcome to record:** retrieval hit rate on a small test set of questions (build 30-50 Q&A pairs).

### Phase 3: Agent workflow
- Use LangGraph (or the Claude Agent SDK / OpenAI Agents SDK) so the agent plans, calls MCP tools, and asks for **human approval** before risky actions.
- Add a second agent or sub-flow if useful (triage agent + resolver agent).
- Package the agent as a **reusable template** (config file for tools, prompts, policies) so a new team can spin one up.

### Phase 4: Automation workflows (n8n / Power Automate)
- n8n flow: incoming email → LLM classifies urgency and category → calls `create_ticket` → posts to Slack/Teams.
- Power Automate (or Copilot Studio) flow: new SharePoint list item → calls your API → writes the result back.
- **Outcome to record:** average handling time before and after (simulated on a test set) and tasks automated per run.

### Phase 5: Security, identity and responsible AI
- **Entra ID / OAuth2 (OIDC)** login on the FastAPI app, with roles (employee, IT admin) and RBAC on tools.
- Least-privilege scopes for each tool; secrets in environment variables / Azure Key Vault.
- **PII redaction** before text is sent to any LLM or logged (emails, phone numbers, IDs).
- **Prompt-injection checks** on retrieved documents and tool outputs; allow-list of actions.
- Immutable **audit log** of every tool call (who, what, when, approved by).
- Write a one-page note: how the design maps to GDPR principles (data minimisation, purpose limitation, retention, right to erasure) and to InfoSec expectations.

### Phase 6: Observability and cost governance
- Trace every request (prompt, tool calls, latency, tokens) with **Langfuse** (self-hostable) or OpenTelemetry.
- Build a small dashboard: requests/day, p95 latency, error rate, token usage and cost per team/model.
- Implement **budgets and alerts** (e.g. warn at 80% of a team's monthly budget, hard-stop at 100%) and a "shadow AI" view: usage grouped by API key / client so unknown usage stands out.
- **Outcome to record:** cost per resolved request, % of requests blocked by guardrails.

### Phase 7: Deploy and harden
- Dockerise; deploy to **Azure** (App Service or Container Apps) or **AWS** (Lambda / ECS).
- CI/CD with GitHub Actions: lint, test, build image, deploy.
- Add health checks, retries and timeouts; write a short runbook (how to debug a failing agent run).

---

## 4. Suggested tech stack

| Area | Choice |
|---|---|
| Language / API | Python, FastAPI, Pydantic (Java optional, not needed) |
| MCP | Official MCP Python SDK (FastMCP) |
| Agents | LangGraph, or Claude Agent SDK / OpenAI Agents SDK |
| LLMs | Claude API (primary), OpenAI API (second provider to show portability) |
| RAG | Sentence-Transformers or API embeddings, Qdrant |
| Workflow automation | n8n (self-hosted) and/or Power Automate |
| Microsoft ecosystem | SharePoint (docs), Power Automate, Teams webhook; Copilot Studio if available |
| Identity | Microsoft Entra ID (OAuth2/OIDC), RBAC |
| Observability | Langfuse or OpenTelemetry, structured JSON logs |
| Cloud | Azure (free credits) or AWS free tier |
| DevOps | Docker, GitHub Actions, pytest |

---

## 5. Resume bullets to add after you build it

Fill in the real numbers you measure. Do not copy these as they are.

**OpsPilot: Enterprise IT Helpdesk Agent Platform** | GitHub | Month Year – Month Year
- Built a Python **MCP server** exposing *N* helpdesk tools (ticketing, access lookup, spend reports) and integrated it with **Claude Desktop, Cursor** and *[other clients]*, with pytest and GitHub Actions CI.
- Designed an access-aware **RAG** pipeline (Qdrant, SharePoint-sourced docs) with an agentic **LangGraph** workflow and human-approval gate, reaching *X%* answer accuracy on a *N*-question test set.
- Automated email-to-ticket triage with **n8n / Power Automate** (LLM classification, Slack/Teams notification), cutting simulated handling time by *X%*.
- Secured the platform with **Entra ID / OAuth2** SSO, RBAC, PII redaction, prompt-injection guardrails and an audit log, aligned to GDPR principles.
- Added **Langfuse tracing** and a **token-cost governance** dashboard with per-team budgets and alerts; deployed on **Azure** via Docker and GitHub Actions.

**Skills row to add:** MCP, LangGraph, n8n, Power Automate, SharePoint, Entra ID (OAuth2/OIDC), Azure, Langfuse/OpenTelemetry, RAG, Guardrails, RBAC.

---

## 6. Interview talking points

- Why MCP: one tool server reused across Claude, Cursor and Copilot instead of one integration per client.
- How you stop an agent from doing harm: allow-lists, approval gates, least privilege, audit log.
- How you handled PII and what you log versus what you redact.
- How you measured quality (test set, retrieval hit rate) and cost (cost per resolved request).
- What you would change to take it from prototype to production (queueing, retries, secrets management, multi-tenant isolation).

---

## 7. Realistic scope

If time is tight, build in this order and stop when you run out of time: **Phase 1 → 2 → 5 (OAuth + PII) → 6 → 7 → 4 → 3**. Phases 1, 2, 5 and 6 give you MCP, RAG, identity, responsible AI, observability and cost governance. Deployment (7) then covers cloud. Only list on your resume what you have actually built and can explain.