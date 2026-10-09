// PAID / OPTIONAL: deploying this creates billable Azure resources. The project runs free locally without it.
// OpsPilot on Azure Container Apps.
//   az group create -n rg-opspilot -l westeurope
//   az deployment group create -g rg-opspilot -f infra/azure/main.bicep -p tenantId=<tenant> apiAudience=api://<app-id>
// NOT validated in CI: review parameters and run `az deployment group what-if` first (see docs/deploy-azure.md).

@description('Azure region')
param location string = resourceGroup().location

@description('Short prefix for resource names')
@minLength(3)
@maxLength(12)
param prefix string = 'opspilot'

@description('Entra ID tenant used to validate bearer tokens')
param tenantId string

@description('Expected token audience, e.g. api://<app registration client id>')
param apiAudience string

@description('Container image (set by the deploy workflow after the first build)')
param image string = 'mcr.microsoft.com/k8se/quickstart:latest'

@description('LLM provider: mock (free) | anthropic | openai (paid; also needs OPSPILOT_ALLOW_PAID_LLM=true)')
param llmProvider string = 'mock'

var suffix = uniqueString(resourceGroup().id)
var acrName = toLower('${prefix}${suffix}')

resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: { sku: { name: 'PerGB2018' }, retentionInDays: 30 }
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: acrName
  location: location
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

resource identity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-id'
  location: location
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: '${prefix}-kv-${take(suffix, 6)}'
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 14
  }
}

// Role definitions: AcrPull and Key Vault Secrets User for the app's managed identity.
var acrPullRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var kvSecretsUserRole = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')

resource acrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(acr.id, identity.id, 'acrpull')
  scope: acr
  properties: { principalId: identity.properties.principalId, roleDefinitionId: acrPullRole, principalType: 'ServicePrincipal' }
}

resource kvAccess 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(kv.id, identity.id, 'kvsecrets')
  scope: kv
  properties: { principalId: identity.properties.principalId, roleDefinitionId: kvSecretsUserRole, principalType: 'ServicePrincipal' }
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
  }
}

resource app 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-api'
  location: location
  identity: { type: 'UserAssigned', userAssignedIdentities: { '${identity.id}': {} } }
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: true, targetPort: 8000, transport: 'auto', allowInsecure: false }
      registries: [ { server: acr.properties.loginServer, identity: identity.id } ]
      // Secrets are Key Vault references resolved by the managed identity; create them in the vault first.
      secrets: [
        { name: 'anthropic-api-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/anthropic-api-key', identity: identity.id }
        { name: 'openai-api-key', keyVaultUrl: '${kv.properties.vaultUri}secrets/openai-api-key', identity: identity.id }
        { name: 'ticket-api-token', keyVaultUrl: '${kv.properties.vaultUri}secrets/ticket-api-token', identity: identity.id }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: image
          resources: { cpu: json('0.5'), memory: '1Gi' }
          env: [
            { name: 'OPSPILOT_ENV', value: 'prod' }
            { name: 'OPSPILOT_AUTH_MODE', value: 'oidc' }
            { name: 'OPSPILOT_OIDC_TENANT_ID', value: tenantId }
            { name: 'OPSPILOT_OIDC_AUDIENCE', value: apiAudience }
            { name: 'OPSPILOT_LLM_PROVIDER', value: llmProvider }
            { name: 'OPSPILOT_DATA_DIR', value: '/data' }
            { name: 'ANTHROPIC_API_KEY', secretRef: 'anthropic-api-key' }
            { name: 'OPENAI_API_KEY', secretRef: 'openai-api-key' }
            { name: 'OPSPILOT_TICKET_API_TOKEN', secretRef: 'ticket-api-token' }
          ]
          probes: [
            { type: 'Liveness', httpGet: { path: '/health', port: 8000 }, periodSeconds: 20 }
            { type: 'Readiness', httpGet: { path: '/ready', port: 8000 }, initialDelaySeconds: 10, periodSeconds: 10 }
          ]
        }
      ]
      // SQLite + in-process rate limiting are single-replica by design; scale out by moving to Postgres/Redis (docs/runbook.md).
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
  dependsOn: [ acrPull, kvAccess ]
}

output fqdn string = app.properties.configuration.ingress.fqdn
output acrName string = acr.name
output keyVaultName string = kv.name
output managedIdentityClientId string = identity.properties.clientId
