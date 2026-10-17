// FootballFoundry on Azure. Deploy with the Azure Developer CLI from the repository root: `azd up`.
targetScope = 'subscription'

@minLength(1)
@maxLength(40)
@description('azd environment name; used for the resource group and resource names')
param environmentName string

@minLength(1)
@description('Region for every resource. Pick one where the model below is available.')
param location string

@description('Your user object id (azd sets it), granted data access so you can run everything locally too')
param principalId string = ''

@description('Model deployed in the Foundry project and used by every agent')
param modelName string = 'gpt-6-luna'
param modelVersion string = '2026-09-22'
param modelSku string = 'GlobalStandard'
@description('Tokens per minute, in thousands')
param modelCapacity int = 100

@description('LLM deployment for Translator\'s casual-fan translations (informal tone). Empty: NMT only. Must be a model Translator supports for LLM translation.')
param translatorLlmDeployment string = ''

@secure()
@description('Key for the control room (/control), where testers start and stop streams. Empty: the control room is off.')
param controlKey string = ''

@description('Optional custom domain bound to the public agents app.')
param agentsCustomDomain string = ''
@description('Managed certificate resource ID for agentsCustomDomain.')
param agentsCustomDomainCertificateId string = ''

@description('Fabric Eventhouse query URI. Empty: the MCP server uses the season data baked into its image.')
param kustoUri string = ''
param kustoDatabase string = 'footballfoundry'

@description('Create a listen-only SAS rule for Fabric Eventstream, whose Event Hubs source signs in with a key')
param fabricEventstreamAccess bool = true

// Set by azd after the first deploy, so re-provisioning keeps the deployed images.
param agentsImage string = ''
param mcpImage string = ''

var token = toLower(uniqueString(subscription().id, environmentName, location))
var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module identity 'modules/identity.bicep' = {
  scope: rg
  params: { name: 'id-ff-${token}', location: location, tags: tags }
}

module monitoring 'modules/monitoring.bicep' = {
  scope: rg
  params: { token: token, location: location, tags: tags }
}

module eventHubs 'modules/eventhubs.bicep' = {
  scope: rg
  params: {
    name: 'ehns-ff-${token}'
    location: location
    tags: tags
    fabricEventstreamAccess: fabricEventstreamAccess
  }
}

module storage 'modules/storage.bicep' = {
  scope: rg
  params: { name: 'stff${token}', location: location, tags: tags }
}

module cosmos 'modules/cosmos.bicep' = {
  scope: rg
  params: {
    name: 'cosmos-ff-${token}'
    location: location
    tags: tags
    principalIds: filter([identity.outputs.principalId, principalId], id => !empty(id))
  }
}

module foundry 'modules/foundry.bicep' = {
  scope: rg
  params: {
    name: 'ai-ff-${token}'
    location: location
    tags: tags
    modelName: modelName
    modelVersion: modelVersion
    modelSku: modelSku
    modelCapacity: modelCapacity
  }
}

module registry 'modules/registry.bicep' = {
  scope: rg
  params: { name: 'crff${token}', location: location, tags: tags }
}

module roles 'modules/roles.bicep' = {
  scope: rg
  params: {
    appPrincipalId: identity.outputs.principalId
    userPrincipalId: principalId
    eventHubNamespaceName: eventHubs.outputs.namespaceName
    storageAccountName: storage.outputs.name
    foundryAccountName: foundry.outputs.accountName
    registryName: registry.outputs.name
  }
}

module apps 'modules/apps.bicep' = {
  scope: rg
  dependsOn: [roles]
  params: {
    token: token
    location: location
    tags: tags
    identityId: identity.outputs.id
    identityClientId: identity.outputs.clientId
    logAnalyticsWorkspaceId: monitoring.outputs.logAnalyticsWorkspaceId
    appInsightsConnectionString: monitoring.outputs.appInsightsConnectionString
    registryServer: registry.outputs.loginServer
    eventHubNamespaceName: eventHubs.outputs.namespaceName
    eventHubName: eventHubs.outputs.eventHubName
    storageAccountName: storage.outputs.name
    checkpointBlobUrl: storage.outputs.blobEndpoint
    cosmosEndpoint: cosmos.outputs.endpoint
    foundryProjectEndpoint: foundry.outputs.projectEndpoint
    modelDeployment: foundry.outputs.modelDeployment
    translatorEndpoint: foundry.outputs.translatorEndpoint
    translatorLlmDeployment: translatorLlmDeployment
    controlKey: controlKey
    agentsCustomDomain: agentsCustomDomain
    agentsCustomDomainCertificateId: agentsCustomDomainCertificateId
    kustoUri: kustoUri
    kustoDatabase: kustoDatabase
    agentsImage: agentsImage
    mcpImage: mcpImage
  }
}

// azd stores these in .azure/<env>/.env; the same names configure the services when you run them locally.
output AZURE_LOCATION string = location
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = registry.outputs.loginServer
output EVENTHUB_NAMESPACE string = eventHubs.outputs.namespaceName
output EVENTHUB_NAME string = eventHubs.outputs.eventHubName
output CHECKPOINT_BLOB_URL string = storage.outputs.blobEndpoint
output COSMOS_ENDPOINT string = cosmos.outputs.endpoint
output COSMOS_DATABASE string = cosmos.outputs.databaseName
output FOUNDRY_PROJECT_ENDPOINT string = foundry.outputs.projectEndpoint
output FOUNDRY_MODEL string = foundry.outputs.modelDeployment
output TRANSLATOR_ENDPOINT string = foundry.outputs.translatorEndpoint
output AGENTS_URL string = apps.outputs.agentsUrl // the web app, its API and the chat
output APPLICATIONINSIGHTS_CONNECTION_STRING string = monitoring.outputs.appInsightsConnectionString
