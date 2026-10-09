---
id: admin-privileged-access-runbook
title: IT admin runbook for privileged access and approvals
audience: [it_admin]
teams: [all]
---
# IT admin runbook: privileged access and approvals

## Approving reset requests
Before approving a password, MFA or VPN reset, verify the requester by calling their manager on the number in the HR directory. An administrator can never approve their own request; another administrator must do it. Record the verification in the decision note.

## Elevated roles
Privileged roles are activated just in time through Privileged Identity Management for at most 4 hours with a ticket reference. Standing global administrator access is forbidden.

## Break-glass accounts
Two break-glass accounts exist, with passwords split between the security officer and the IT director. Any sign-in with them triggers a page to the on-call engineer and must be documented within 24 hours.

## Audit
The audit log is append-only. Run the integrity check weekly and escalate any broken chain to the security officer immediately.
