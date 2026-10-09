# Microsoft Entra ID (OIDC / OAuth2) setup

OpsPilot validates bearer JWTs issued by Entra ID (v2.0 endpoint). Set `OPSPILOT_AUTH_MODE=oidc`,
`OPSPILOT_OIDC_TENANT_ID`, `OPSPILOT_OIDC_AUDIENCE`. Keys come from the tenant JWKS URL and are cached.
Validation: RS256 only, `iss`, `aud`, `exp` required.

## 1. App registration for the API

1. Entra admin center → App registrations → **New registration** `opspilot-api` (single tenant).
2. **Expose an API**: Application ID URI `api://<client-id>`; add scope `access_as_user`.
3. **App roles** (allowed member types: Users/Groups for the first three, Applications for `automation`):

   | Value | Maps to | Scopes |
   |---|---|---|
   | `employee` | employee | kb, own tickets, own access, reset request |
   | `team_lead` | team_lead | + team spend report |
   | `it_admin` (alias `IT.Admin`) | it_admin | approvals, audit, budgets, metrics, any-user access |
   | `automation` | automation | create tickets on behalf, notify |

4. Enterprise applications → `opspilot-api` → **Users and groups**: assign groups to roles. Users without a
   role get `employee` (least privilege).
5. Optional claims (token configuration): add `email`, `family_name`/`given_name`, and a `department` claim
   (`OPSPILOT_OIDC_TEAM_CLAIM`) for budget attribution; otherwise the directory team is used, else `unassigned`.

## 2. Human sign-in (chat UI / Teams)

Use MSAL (browser or Teams SSO) to acquire a token for `api://<client-id>/access_as_user` and send it as
`Authorization: Bearer …`. The bundled dev UI uses locally minted tokens; replace its login box with MSAL.js
for production (the UI's CSP currently only allows same-origin scripts, so bundle MSAL into `/static`).

## 3. Service-to-service (n8n, Power Automate)

Register a second app `opspilot-automation`, grant it the `automation` **application** permission on
`opspilot-api`, create a client secret or certificate, and use the client-credentials flow
(scope `api://<client-id>/.default`). App-only tokens carry no email; OpsPilot synthesises a service identity
(`app-<appid>@service.opspilot.local`, team `Automation`) from `appid`/`azp`.

## 4. Verify

```bash
curl -s -H "Authorization: Bearer $TOKEN" https://<host>/api/me
```

`roles`/`scopes` in the response show what the token maps to.

> **Status:** claim mapping, audience/issuer/expiry checks, signature and `alg` downgrade rejection and the
> app-only flow are covered by `tests/test_auth.py` using generated RSA keys. A live tenant has not been tested.
