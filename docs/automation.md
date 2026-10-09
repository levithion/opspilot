# Workflow automation (n8n and Power Automate)

Both call the same HTTP surface as everything else, authenticated as the `automation` service principal:

| Endpoint | Purpose |
|---|---|
| `POST /api/automation/classify` `{text}` | LLM triage → `{category, priority, needs_human, flagged}` |
| `POST /api/tools/{name}` | Generic tool gateway (`create_ticket`, `notify_channel`, …) with full RBAC/audit |
| `POST /api/automation/email-triage` `{sender, subject, body}` | One call: classify → ticket (on behalf of sender) → notify if high/urgent |
| `GET /openapi.json` | Import into Power Automate / Copilot Studio as a custom connector |

Inbound email content is untrusted: `classify` runs input guardrails, redacts PII, and flags injection attempts
(`flagged` is set, priority `high`, `needs_human: true`) instead of obeying them.

## n8n

`automation/n8n/email-to-ticket.json`: IMAP trigger → HTTP *classify* → HTTP *create_ticket* → IF high/urgent →
HTTP *notify_channel*. Import via Workflows → Import from file, set n8n env vars `OPSPILOT_URL` and
`OPSPILOT_AUTOMATION_TOKEN` (dev: `python -m opspilot.cli token automation@opspilot.example`; prod: client-credentials
token, see [entra-id.md](entra-id.md)). HTTP nodes use 15 s timeouts and 3 retries.

> **Status:** the workflow JSON is syntactically valid and its HTTP contract is covered by API tests, but it has
> not been imported into a running n8n instance. Expect to adjust node versions/field names after import.

## Power Automate

Flow *"Email → ticket"* (cloud flow):

1. Trigger: **When a new email arrives (V3)** (shared mailbox).
2. Action: **HTTP** (or the custom connector built from `/openapi.json`) → `POST {host}/api/automation/email-triage`,
   headers `Authorization: Bearer <token from Entra client credentials>`, `X-Client-Id: power-automate`; body
   `{"sender": From, "subject": Subject, "body": Body (plain text)}`.
3. Condition: `triage.priority` is `high` or `urgent` → **Post message in a chat or channel** (Teams) with the ticket key.

Flow *"SharePoint list item → API → write back"*:

1. Trigger: **When an item is created** (SharePoint list "IT requests").
2. HTTP `POST {host}/api/tools/create_ticket` with `on_behalf_of` = item author email, `title`, `description`, `category`.
3. **Update item**: write the returned `ticket.key` and `ticket.status` back to the list.

> **Status:** documented steps only; Power Automate/Copilot Studio flows cannot be exported from this repo
> and were not built in a tenant.

## Measuring impact

```bash
python automation/simulate.py --manual-minutes 6
```

Runs 30 labelled emails through `email-triage`, reports tasks automated, classification accuracy against labels,
LLM cost per email, and handling time vs the manual baseline **you assume**. With the mock provider the timing is
not meaningful; rerun with a real provider and quote the assumption.
