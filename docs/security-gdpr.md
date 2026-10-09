# Security, responsible AI and GDPR (one-page note)

Scope: OpsPilot as implemented in this repository. "Control" lists where each is enforced and tested.

## Threat model in brief

| Threat | Control | Where / test |
|---|---|---|
| Prompt injection in user text | Pattern scoring; block ≥ 0.6 | `security/guardrails.py`, `test_pii_guardrails.py`, `test_agent.py` |
| Indirect injection (KB docs, tickets, tool output) | Quarantine flagged content, never forward | `rag/retriever.py`, `tools/registry.py::_screen`, `test_rag.py::poisoned`, `test_tools.py` |
| Agent takes a harmful action | Tool allow-list per template, scopes, approval gate, step limit | `agent/template.py`, `tools/`, `test_agent.py::step_limit` |
| Privilege escalation via the model | RBAC enforced in the registry, independent of the prompt | `test_tools.py::rbac_*` |
| Reading someone else's data | Self-or-admin checks; `not_found` hides existence | `test_tools.py::ticket_*` |
| Confidential KB leakage | Role + team filters inside Qdrant | `test_rag.py`, `eval/leakage.jsonl` (0/7 leaks) |
| Token forgery / downgrade | RS256 only in OIDC mode; iss/aud/exp required | `test_auth.py` |
| Log / audit tampering | Append-only triggers + SHA-256 hash chain; `GET /api/admin/audit/verify` | `test_audit_cost.py` |
| Cost abuse / shadow AI | Per-team budgets (warn 80 %, stop 100 %), usage by client | `cost/tracker.py` |

## GDPR principles → design

* **Lawfulness, purpose limitation.** Data is used only to resolve IT requests; the assistant's system prompt and
  tool allow-list make other uses impossible. Document the lawful basis (legitimate interest / employment
  contract) in your RoPA.
* **Data minimisation.** Emails, phones, card/ID numbers, IPs, employee ids and secrets are replaced by typed
  placeholders *before* text reaches an LLM, a log line, an audit record, a ticket or a notification
  (`security/pii.py`). The LLM is given name/team/role, never the user's email address. Unknown email senders are
  stored as an unsalted SHA-1 prefix pseudonym (`external:…`); use a keyed HMAC if you need resistance to
  guessing.
* **Storage limitation.** Documented retention (KB doc `data-retention-and-privacy`): tickets 24 months, chat
  transcripts 90 days, audit 7 years (pseudonymous). **Note:** the code does not yet run an automatic purge job;
  implement it as a scheduled task (see runbook) before relying on those periods.
* **Integrity and confidentiality.** TLS at the ingress, secrets in env/Key Vault, RBAC, audit chain, least-privilege
  tool visibility. The SQLite file is not encrypted at rest here; use Azure disk/Storage encryption or move to a
  managed database with TDE.
* **Accuracy / transparency.** Answers cite KB sources; the UI shows tools called, status and cost. Pending
  approvals are explicit, so the user is never told an action happened when it did not.
* **Right to erasure / access.** The audit log stores only pseudonymous user ids, so erasing a person means
  deleting their `users` row, tickets and comments while the audit trail remains valid (the hash chain covers
  ids, not personal fields). Subject-access export is a query over `tickets`, `ticket_comments`, `approvals` and
  `usage_events` by `user_id`. These procedures are documented but **not exposed as API endpoints yet**.
* **Processors and transfers.** Sending text to Anthropic/OpenAI makes them processors: sign DPAs, choose regions
  / zero-retention options, and list them in the privacy notice. With the mock provider nothing leaves the host.
* **Human oversight (EU AI Act-style).** High-risk actions need a different human to approve; the approver is
  recorded; separation of duties is enforced.

## InfoSec checklist status

| Item | Status |
|---|---|
| SSO (Entra ID OIDC), RBAC | Implemented; OIDC path unit-tested with generated keys, **not** run against a live tenant |
| Secrets management | Env vars + Key Vault references in Bicep (not deployed) |
| Dependency / image scanning | Not configured: add Dependabot + `pip-audit` + Trivy in CI |
| Pen test / red-team of guardrails | Not done. Regex guardrails are a first layer, not a guarantee |
| DPIA | Template inputs above; must be completed by the data owner |
