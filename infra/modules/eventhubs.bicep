// The live event stream: one event hub, partitioned by match_id, with a consumer group per reader.
param name string
param location string
param tags object
param fabricEventstreamAccess bool

resource namespace 'Microsoft.EventHub/namespaces@2024-01-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Standard', tier: 'Standard', capacity: 1 } // Basic allows only the $Default consumer group
  properties: {
    minimumTlsVersion: '1.2'
    // Apps sign in with Entra ID. Keys stay enabled only if Fabric Eventstream needs its listen-only rule.
    disableLocalAuth: !fabricEventstreamAccess
  }
}

resource hub 'Microsoft.EventHub/namespaces/eventhubs@2024-01-01' = {
  parent: namespace
  name: 'match-events'
  properties: {
    partitionCount: 4
    messageRetentionInDays: 1
  }
}

resource analyst 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: hub
  name: 'analyst'
}

// The web app's relay: every agents replica reads every partition from the start, without checkpoints.
resource viewer 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: hub
  name: 'viewer'
}

resource fabric 'Microsoft.EventHub/namespaces/eventhubs/consumergroups@2024-01-01' = {
  parent: hub
  name: 'fabric'
}

resource fabricListen 'Microsoft.EventHub/namespaces/eventhubs/authorizationRules@2024-01-01' = if (fabricEventstreamAccess) {
  parent: hub
  name: 'fabric-listen'
  properties: { rights: ['Listen'] }
}

output namespaceName string = namespace.name
output eventHubName string = hub.name
