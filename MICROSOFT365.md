# Microsoft 365 Integration

Microsoft is deprecating IMAP and EWS access. This guide shows how to use Microsoft Graph API instead.

## Why Microsoft Graph?

- **IMAP**: Being deprecated by Microsoft
- **EWS**: Also being deprecated
- **Microsoft Graph**: The modern replacement with better features

## Features

- **Delta sync**: Only fetch changes since last sync (efficient)
- **OAuth2 authentication**: Secure, no password storage
- **Full API access**: Messages, folders, labels, flags
- **Change tracking**: Automatic detection of new/updated/deleted messages

## Setup

### 1. Install dependencies

```bash
pip install msgraph-sdk azure-identity requests
```

### 2. Add your Microsoft 365 account

```bash
./o365_setup.py add-account --email your.email@company.com
```

This will:
1. Open your browser to Microsoft login
2. Prompt you to authenticate
3. Ask you to paste the redirect URL
4. Save credentials to `~/.config/anmari/config.toml`

### 3. Sync your email

```bash
./anmari.py sync --account your.email@company.com
```

## Authentication Flow

The setup uses **device code flow** (similar to DavMail):

1. Script generates an authorization URL
2. You open it in your browser
3. Sign in with your Microsoft account
4. Complete any MFA/conditional access requirements
5. Copy the redirect URL from browser
6. Paste it back to the script

### Using DavMail's Client ID

By default, we use DavMail's public client ID:
```
client_id=d3590ed6-52b3-4102-aeff-aad2292ab01c
redirect_uri=urn:ietf:wg:oauth:2.0:oob
```

This is a **public client** registered by DavMail, so no client secret is needed.

### Using Your Own Azure AD App

For production use, register your own app:

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Azure Active Directory** → **App registrations**
3. Click **New registration**
4. Set redirect URI: `urn:ietf:wg:oauth:2.0:oob`
5. Under **API permissions**, add:
   - `Mail.Read`
   - `Mail.ReadWrite`
   - `offline_access`
6. Copy the **Application (client) ID**

Then use it:
```bash
./o365_setup.py add-account \
  --email your.email@company.com \
  --client-id YOUR_CLIENT_ID \
  --tenant YOUR_TENANT_ID
```

## How It Works

### Delta Sync Algorithm

Microsoft Graph provides **delta queries** for efficient syncing:

```python
# First sync: Get all messages + delta link
GET /me/mailFolders/inbox/messages/delta

# Subsequent syncs: Only get changes
GET /me/mailFolders/inbox/messages/delta?$deltatoken=...
```

The delta response includes:
- **New messages**: Full message objects
- **Updated messages**: Changed properties (flags, read status)
- **Deleted messages**: Marked with `@removed` property

### Message ID Mapping

- **IMAP**: Uses numeric UIDs (e.g., `12345`)
- **Graph API**: Uses string IDs (e.g., `AAMkAGI2T...`)

The cache stores Graph message IDs in the `uid` column (as strings).

### Folder Mapping

Common IMAP folder names map to Graph well-known names:

| IMAP Folder | Graph Folder |
|-------------|--------------|
| INBOX       | inbox        |
| Sent        | sentitems    |
| Drafts      | drafts       |
| Trash       | deleteditems |
| Archive     | archive      |

### Flag Mapping

| IMAP Flag | Graph Property |
|-----------|----------------|
| `\Seen`   | `isRead: true` |
| `\Flagged`| `flag.flagged: true` |

## Configuration

Example `~/.config/anmari/config.toml`:

```toml
[[accounts]]
email = "user@company.com"
provider = "microsoft365"
client_id = "d3590ed6-52b3-4102-aeff-aad2292ab01c"
tenant_id = "common"
cache_days = 90

[[accounts]]
email = "personal@gmail.com"
imap_host = "imap.gmail.com"
imap_port = 993
cache_days = 90
```

The system automatically detects provider type:
- If `provider = "microsoft365"` → Use Graph API
- If `imap_host` is set → Use IMAP

## API Reference

### Graph Client Methods

```python
from graph_client import EmailGraphClient

# Initialize
client = EmailGraphClient(
    client_id="...",
    tenant_id="common",
    email_addr="user@company.com",
    cache=cache
)

# Sync folder (uses delta query)
client.sync_from_server(folder="INBOX", page_size=100)

# Get unread messages
unread_ids = client.get_unread_messages()

# Fetch message body
body = client.get_message_body(msg_id)

# Fetch headers
headers = client.get_message_headers(msg_id)
```

### Delta Link Storage

Delta links are stored in the cache database:

```sql
CREATE TABLE delta_links (
    folder TEXT PRIMARY KEY,
    delta_link TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Methods:
```python
# Get delta link for incremental sync
delta_link = cache.get_delta_link(folder)

# Save delta link after sync
cache.set_delta_link(folder, delta_link)
```

## Troubleshooting

### "No authorization code found in URL"

Make sure you copy the **full redirect URL** from your browser, including the `?code=...` parameter.

### "AADSTS50011: The redirect URI specified in the request does not match"

Your Azure AD app's redirect URI must be exactly: `urn:ietf:wg:oauth:2.0:oob`

### "Insufficient privileges to complete the operation"

Add these API permissions in Azure AD:
- `Mail.Read`
- `Mail.ReadWrite`
- `offline_access`

Then grant admin consent.

### Token expired

The device code flow provides refresh tokens. If your token expires, re-run:
```bash
./o365_setup.py add-account --email your.email@company.com
```

## Migration from IMAP

If you're currently using IMAP (via DavMail or direct):

1. **Keep existing cache**: The database schema is compatible
2. **Add O365 account**: Run `o365_setup.py add-account`
3. **Update config**: Change `imap_host` to `provider = "microsoft365"`
4. **First sync**: Will use delta query to get all messages
5. **Subsequent syncs**: Only fetch changes (much faster)

## Performance

**IMAP sync** (traditional):
- Fetch all UIDs: `UID SEARCH 1:*`
- Fetch all flags: `UID FETCH 1:* FLAGS`
- Compare with cache
- Time: ~30s for 10,000 messages

**Graph delta sync**:
- First sync: Fetch all messages (~30s)
- Subsequent syncs: Only changes (~2s)
- Delta link tracks last sync point
- Time: ~2s for typical changes

## References

- [Microsoft Graph Mail API](https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview)
- [Delta Query](https://learn.microsoft.com/en-us/graph/delta-query-messages)
- [Device Code Flow](https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-device-code)
- [DavMail O365 Auth](https://github.com/mguessan/davmail/blob/master/src/java/davmail/exchange/auth/O365Authenticator.java)
