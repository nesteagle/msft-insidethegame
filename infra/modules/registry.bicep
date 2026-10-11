// Container registry for the azd-built images. Pulled with the managed identity, no admin user.
param name string
param location string
param tags object

resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: name
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

output name string = registry.name
output loginServer string = registry.properties.loginServer
