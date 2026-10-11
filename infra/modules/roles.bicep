// Azure RBAC for the apps' managed identity and, for local development, the deploying user.
param appPrincipalId string
param userPrincipalId string
param eventHubNamespaceName string
param storageAccountName string
param foundryAccountName string
param registryName string

var role = {
  eventHubsDataOwner: 'f526a384-b230-433a-b45c-95f59c4a2dec'
  eventHubsDataReceiver: 'a638d3c7-ab3a-418d-83e6-5f17a39d4fde'
  eventHubsDataSender: '2b629674-e913-4c01-ae53-ef4638d8f975'
  storageBlobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  azureAiUser: '53ca6127-db72-4b80-b1b0-d745d6d5456d'
  cognitiveServicesUser: 'a97b65f3-24c7-4388-baec-2e87135dc908'
  acrPull: '7f951dda-4ed3-4680-a7ca-43fe172d538d'
}

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' existing = {
  name: eventHubNamespaceName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource foundry 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: registryName
}

// -- the apps ------------------------------------------------------------------------------------------------

resource appEventHubs 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: namespace
  name: guid(namespace.id, appPrincipalId, role.eventHubsDataReceiver)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.eventHubsDataReceiver)
  }
}

// The control room (/control) streams matches into Event Hubs.
resource appEventHubsSend 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: namespace
  name: guid(namespace.id, appPrincipalId, role.eventHubsDataSender)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.eventHubsDataSender)
  }
}

resource appStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, appPrincipalId, role.storageBlobDataContributor)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.storageBlobDataContributor)
  }
}

resource appFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, appPrincipalId, role.azureAiUser)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.azureAiUser)
  }
}

// Translator with Entra ID on the AI Services account.
resource appTranslator 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: foundry
  name: guid(foundry.id, appPrincipalId, role.cognitiveServicesUser)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.cognitiveServicesUser)
  }
}

resource appRegistry 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: registry
  name: guid(registry.id, appPrincipalId, role.acrPull)
  properties: {
    principalId: appPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.acrPull)
  }
}

// -- you, for local runs (stream, listen, agents and MCP on your machine) ---------------------------------------

resource userEventHubs 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  scope: namespace
  name: guid(namespace.id, userPrincipalId, role.eventHubsDataOwner)
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.eventHubsDataOwner)
  }
}

resource userStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  scope: storage
  name: guid(storage.id, userPrincipalId, role.storageBlobDataContributor)
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.storageBlobDataContributor)
  }
}

resource userTranslator 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  scope: foundry
  name: guid(foundry.id, userPrincipalId, role.cognitiveServicesUser)
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.cognitiveServicesUser)
  }
}

resource userFoundry 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(userPrincipalId)) {
  scope: foundry
  name: guid(foundry.id, userPrincipalId, role.azureAiUser)
  properties: {
    principalId: userPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', role.azureAiUser)
  }
}
