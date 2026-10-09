# Screenshots

Captured automatically with headless Chrome against a throwaway demo database (offline mock LLM, demo users,
`scripts/demo.py` traffic, and HR's budget set to $0.0017 so the warning bar shows). Costs are at the mock's nominal
price. Answer text from the mock is a plain extract of the knowledge base; use a real model to judge answer quality.

| File | Shows |
|---|---|
| `chat-search-kb.png` | Cited answer from the knowledge base; tools called, latency and cost on every reply |
| `chat-approval-pending.png` | A reset request becomes `pending_approval`; nothing is executed |
| `chat-injection-blocked.png` | Prompt-injection attempt blocked before any LLM spend |
| `chat-pii-redacted-ticket.png` | Phone and email replaced with `[PHONE]` / `[EMAIL]` in the stored ticket |
| `approvals-console-pending.png` | IT admin sees the pending request with Approve / Reject |
| `approvals-console-approved.png` | After approval: status `executed`, resolution ticket created |
| `dashboard-top.png`, `dashboard-full.png` | Requests, p95 latency, error rate, guardrail block %, cost per resolved request |
| `spend-vs-budget.png` | Per-team spend against budget (HR in the warning state) |
| `shadow-ai.png` | Usage by client; `random-script` flagged as unregistered |
| `audit-log.png` | Hash-chain status, client and `Approved by` columns |
| `api-docs.png` | OpenAPI docs for the tool gateway, tickets and admin endpoints |

Not captured here (needs your desktop apps): Claude Desktop tool list and the MCP calls.
