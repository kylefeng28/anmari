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
