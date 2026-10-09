# Deploying to Azure (Container Apps)

> **COSTS MONEY - optional.** Container Apps, Container Registry, Key Vault and Log Analytics are billed. Nothing in this project requires them; run locally (free) instead. See "Free hosting options" below.
>
> **Status: not deployed.** `infra/azure/main.bicep` and `.github/workflows/deploy-azure.yml` are unvalidated
> (no Azure CLI/Bicep available when written). Run `az bicep build` and `az deployment group what-if` first.

## Resources created by `main.bicep`

Log Analytics, Container Registry (Basic, no admin user), user-assigned managed identity (AcrPull, Key Vault
Secrets User), Key Vault (RBAC), Container Apps environment, and one container app (`min=max=1` replica because
SQLite and the in-process rate limiter are single-node by design; see architecture "Known limits").

## Steps

```bash
az group create -n rg-opspilot -l westeurope
az deployment group create -g rg-opspilot -f infra/azure/main.bicep \
  -p tenantId=<tenant-id> apiAudience=api://<api-app-client-id>
# put secrets in the vault (names are referenced by the template)
az keyvault secret set --vault-name <kv> -n anthropic-api-key --value '<key>'
az keyvault secret set --vault-name <kv> -n openai-api-key    --value 'unused'
az keyvault secret set --vault-name <kv> -n ticket-api-token  --value "$(openssl rand -hex 24)"
```

Data persistence: the container's `/data` is ephemeral unless you mount an Azure Files share (add a `volumes`
entry on the environment/app) — do this before using it for anything real, or move to Azure Database for
PostgreSQL.

## CI/CD with GitHub Actions (OIDC, no stored secrets)

1. Create an Entra app registration for GitHub, add a federated credential for `repo:<org>/<repo>:environment:production`.
2. Grant it `Contributor` on the resource group and `AcrPush`/`Contributor` on the registry.
3. Set repository variables: `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `AZURE_RESOURCE_GROUP`,
   `AZURE_ACR_NAME`, `AZURE_CONTAINER_APP`.
4. Tag `v0.1.0` (or run the workflow manually). CI (`ci.yml`) must pass first on `main`.

Rollback: `az containerapp revision list -n <app> -g <rg>` then `az containerapp ingress traffic set --revision-weight <old>=100`.

## AWS equivalent

ECR + ECS Fargate (or App Runner) behind an ALB; secrets in Secrets Manager; Cognito or Entra OIDC via the same
`OPSPILOT_OIDC_*` settings (set `OPSPILOT_OIDC_ISSUER`/`OPSPILOT_OIDC_JWKS_URL` explicitly for non-Entra IdPs);
EFS or RDS for state; CloudWatch for logs. No code changes are required: configuration only.

## Free hosting options (no spend)

* **Your own machine / a home server:** `docker compose up --build` (or `opspilot-api`). Everything, including
  Qdrant and n8n, is open source and self-hosted.
* **Free-tier PaaS:** the Dockerfile runs on any container host with a free tier (e.g. Fly.io, Render, Koyeb,
  Google Cloud Run's free quota). Free tiers change and often need a card on file; check current terms and set a
  spending cap/alert before deploying. State is SQLite on an ephemeral disk unless the host offers a free volume.
* **Free identity for real SSO:** a Microsoft 365 Developer Program tenant (if you qualify) provides Entra ID
  and SharePoint at no cost; otherwise stay on dev auth mode.
