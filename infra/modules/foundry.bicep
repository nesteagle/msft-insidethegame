// Microsoft Foundry: an AI Services account with a project and one model deployment for the agents.
param name string
param location string
param tags object
param modelName string
param modelVersion string
param modelSku string
param modelCapacity int

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: name
    allowProjectManagement: true
    disableLocalAuth: true // Entra ID only
    publicNetworkAccess: 'Enabled'
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: 'footballfoundry'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    displayName: 'FootballFoundry'
    description: 'Live match intelligence agents for the Inside the Game hackathon'
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = {
  parent: account
  name: modelName
  sku: { name: modelSku, capacity: modelCapacity }
  properties: {
    model: { format: 'OpenAI', name: modelName, version: modelVersion }
  }
}

output accountName string = account.name
// Azure Translator is part of the AI Services account; the SDK adds the /translator/text path to this endpoint.
output translatorEndpoint string = 'https://${account.properties.customSubDomainName}.cognitiveservices.azure.com'
output projectEndpoint string = 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${project.name}'
output modelDeployment string = deployment.name
