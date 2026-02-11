pip install msgraph-sdk azure-identity

Supported commands:
- CAPABILITY - Lists capabilities
- LOGIN - Authentication (currently accepts any credentials)
- LIST - Lists folders from Graph API
- SELECT - Selects a folder and loads messages
- FETCH - Gets message flags, UIDs, or full MIME content
- STORE - Updates flags (read/flagged status)
- LOGOUT - Closes connection

Limitations:
- No TLS (add with ssl module for production)
- Simple UID mapping (resets on reconnect)
- No SEARCH, IDLE, or APPEND yet
- Basic error handling

## Dev Testing
Download [dev-proxy](https://learn.microsoft.com/en-us/microsoft-cloud/dev/dev-proxy/get-started/set-up?tabs=automated) and [set up mock responses](https://learn.microsoft.com/en-us/microsoft-cloud/dev/dev-proxy/how-to/mock-responses) for Microsoft Graph API using the [GraphMockResponsePlugin](https://learn.microsoft.com/en-us/microsoft-cloud/dev/dev-proxy/technical-reference/graphmockresponseplugin).

Useful collection of mocks: https://github.com/pnp/proxy-samples/tree/main/samples/microsoft-graph-docs-mocks/.devproxy
