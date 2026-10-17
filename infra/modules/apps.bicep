// Container Apps: the agents (public: web app, API and chat; scales on Event Hubs backlog) and the MCP server
// (internal only). Apps reach each other by name inside the environment.
param token string
param location string
param tags object
param identityId string
param identityClientId string
param logAnalyticsWorkspaceId string
@secure()
param appInsightsConnectionString string
param registryServer string
param eventHubNamespaceName string
param eventHubName string
param storageAccountName string
param checkpointBlobUrl string
param cosmosEndpoint string
param foundryProjectEndpoint string
param modelDeployment string
param translatorEndpoint string
param translatorLlmDeployment string
@secure()
param controlKey string
param agentsCustomDomain string
param agentsCustomDomainCertificateId string
param kustoUri string
param kustoDatabase string
param agentsImage string
param mcpImage string

// Until azd deploys the real images, apps run a placeholder.
var placeholder = 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
var agentsName = 'ca-agents-${token}'
var mcpName = 'ca-mcp-${token}'

resource env 'Microsoft.App/managedEnvironments@2025-01-01' = {
  name: 'cae-ff-${token}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: reference(logAnalyticsWorkspaceId, '2023-09-01').customerId
        sharedKey: listKeys(logAnalyticsWorkspaceId, '2023-09-01').primarySharedKey
      }
    }
  }
}

var identity = {
  type: 'UserAssigned'
  userAssignedIdentities: { '${identityId}': {} }
}
var registries = [{ server: registryServer, identity: identityId }]

resource mcp 'Microsoft.App/containerApps@2025-01-01' = {
  name: mcpName
  location: location
  tags: union(tags, { 'azd-service-name': 'mcp' })
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      ingress: { external: false, targetPort: 8001, transport: 'http' } // reachable only inside the environment
      registries: registries
    }
    template: {
      containers: [{
        name: 'mcp'
        image: empty(mcpImage) ? placeholder : mcpImage
        resources: { cpu: json('0.5'), memory: '1Gi' }
        env: [
          { name: 'AZURE_CLIENT_ID', value: identityClientId }
          { name: 'KUSTO_URI', value: kustoUri }
          { name: 'KUSTO_DATABASE', value: kustoDatabase }
          // The agents' event archive: first choice for live-match tools, fallback for season queries.
          { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
          { name: 'COSMOS_DATABASE', value: 'footballfoundry' }
          { name: 'AGENTS_URL', value: 'http://${agentsName}' }
        ]
      }]
      scale: {
        minReplicas: 1
        maxReplicas: 3
        rules: [{ name: 'http', http: { metadata: { concurrentRequests: '30' } } }]
      }
    }
  }
}

resource agents 'Microsoft.App/containerApps@2025-01-01' = {
  name: agentsName
  location: location
  tags: union(tags, { 'azd-service-name': 'agents' })
  identity: identity
  properties: {
    managedEnvironmentId: env.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8100
        transport: 'auto'
        stickySessions: { affinity: 'sticky' }
        customDomains: empty(agentsCustomDomain) ? [] : [{
          name: agentsCustomDomain
          bindingType: 'SniEnabled'
          certificateId: agentsCustomDomainCertificateId
        }]
      }
      registries: registries
      secrets: concat([{ name: 'appinsights', value: appInsightsConnectionString }],
        empty(controlKey) ? [] : [{ name: 'control-key', value: controlKey }])
    }
    template: {
      containers: [{
        name: 'agents'
        image: empty(agentsImage) ? placeholder : agentsImage
        resources: { cpu: json('1'), memory: '2Gi' }
        env: concat([
          { name: 'AZURE_CLIENT_ID', value: identityClientId }
          { name: 'FF_SOURCE', value: 'eventhub' }
          { name: 'EVENTHUB_NAMESPACE', value: eventHubNamespaceName }
          { name: 'EVENTHUB_NAME', value: eventHubName }
          { name: 'EVENTHUB_CONSUMER_GROUP', value: 'analyst' }
          { name: 'VIEWER_CONSUMER_GROUP', value: 'viewer' }
          { name: 'CHECKPOINT_BLOB_URL', value: checkpointBlobUrl }
          { name: 'CHECKPOINT_CONTAINER', value: 'checkpoints' }
          { name: 'FF_STORE', value: 'cosmos' }
          { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
          { name: 'COSMOS_DATABASE', value: 'footballfoundry' }
          { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'FOUNDRY_MODEL', value: modelDeployment }
          { name: 'TRANSLATOR_ENDPOINT', value: translatorEndpoint }
          { name: 'TRANSLATOR_LLM_DEPLOYMENT', value: translatorLlmDeployment }
          { name: 'MCP_URL', value: 'http://${mcpName}/mcp' }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights' }
        ], empty(controlKey) ? [] : [{ name: 'CONTROL_KEY', secretRef: 'control-key' }])  // turns on /control
        probes: [{ type: 'Readiness', httpGet: { path: '/healthz', port: 8100 } }]
      }]
      scale: {
        // One replica. The control room runs its streams inside the replica that started them, and keeps their
        // status in memory: with several replicas, the control page's polls land on different replicas (the stream
        // seems to come and go), and scaling in can stop a replica mid-stream. One replica handles a full matchday.
        // To scale out (one replica per partition at most), raise maxReplicas and stream with
        // `footballfoundry stream --sink eventhub` from outside instead of the control room.
        minReplicas: 1
        maxReplicas: 1
        rules: [{
          name: 'eventhub-backlog'
          custom: {
            type: 'azure-eventhub'
            identity: identityId
            metadata: {
              eventHubNamespace: eventHubNamespaceName
              eventHubName: eventHubName
              consumerGroup: 'analyst'
              unprocessedEventThreshold: '500'
              storageAccountName: storageAccountName
              blobContainer: 'checkpoints'
              checkpointStrategy: 'blobMetadata'
            }
          }
        }]
      }
    }
  }
}

output agentsUrl string = 'https://${agents.properties.configuration.ingress.fqdn}'
