// Serverless Cosmos DB for NoSQL: viewer profiles, match state snapshots, the agents' insights timeline, and the
// event archive (every event and a result per match) that the MCP server reads first for live matches.
param name string
param location string
param tags object
@description('Principals granted the built-in data contributor role')
param principalIds array

var databaseName = 'footballfoundry'
var containers = [
  { name: 'insights', partitionKey: '/matchId', ttl: -1 }
  { name: 'matchState', partitionKey: '/matchId', ttl: -1 }
  { name: 'profiles', partitionKey: '/userId', ttl: -1 }
  { name: 'events', partitionKey: '/matchId', ttl: 2592000 } // 30 days; the Eventhouse keeps the history
  { name: 'matchResults', partitionKey: '/matchId', ttl: -1 }
]

resource account 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: name
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    locations: [{ locationName: location, failoverPriority: 0 }]
    capabilities: [{ name: 'EnableServerless' }]
    disableLocalAuth: true // Entra ID only
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
  }
}

resource database 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-11-15' = {
  parent: account
  name: databaseName
  properties: { resource: { id: databaseName } }
}

resource containerResources 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-11-15' = [for c in containers: {
  parent: database
  name: c.name
  properties: {
    resource: {
      id: c.name
      partitionKey: { paths: [c.partitionKey], kind: 'Hash' }
      defaultTtl: c.ttl // -1: no expiry
    }
  }
}]

// Cosmos DB Built-in Data Contributor (data plane role, not Azure RBAC).
resource dataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleDefinitions@2024-11-15' existing = {
  parent: account
  name: '00000000-0000-0000-0000-000000000002'
}

resource roleAssignments 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-11-15' = [for id in principalIds: {
  parent: account
  name: guid(account.id, id, dataContributor.id)
  properties: {
    principalId: id
    roleDefinitionId: dataContributor.id
    scope: account.id
  }
}]

output endpoint string = account.properties.documentEndpoint
output databaseName string = databaseName
