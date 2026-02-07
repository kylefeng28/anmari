# anmari (あんまり)

An email tagging system, similar to [notmuch](https://notmuchmail.org/) but can connect to IMAP directly without requiring Maildir (i.e. avoiding mbsync/offlineimap + a large local Maildir + lieer for tag synchronization for Gmail).

It features a minimal email cache with selective body storage - keep headers forever, bodies for a set amount of time (e.g. 90 days).

Written in Python using [IMAPClient](https://imapclient.readthedocs.io/).

## Features

- **Selective caching**: Store headers always, full bodies only for recent emails (configurable days)
- **Tag system**: notmuch-style tagging for organizing emails
- **Search**: Query by sender, subject, date, tags, Gmail labels, and more
- **Action queue**: Git-like staging for IMAP operations (move, flag, label)
- **Gmail support**: X-GM-LABELS, CONDSTORE for efficient sync
- **Threaded sync**: Parallel folder syncing with progress bars

## CLI Usage

### Setup

```bash
# Install dependencies
pip install imapclient click rich tqdm dateparser prompt-toolkit
```

### Sync emails to cache

```bash
# Sync INBOX
./anmari.py sync

# Sync specific folder
./anmari.py sync --folder "Sent"

# Sync all folders (threaded)
./anmari.py sync --all-folders

# Sync with more threads
./anmari.py sync --all-folders --threads 8

# Custom page size
./anmari.py sync --page-size 50
```

### Search emails

Search with powerful query syntax:

```bash
# Simple search
./anmari.py search "meeting"

# Field-specific search
./anmari.py search 'subject:"project update"'
./anmari.py search 'from:boss@company.com'

# Date filters
./anmari.py search 'date:yesterday'
./anmari.py search 'date:2024-01-01..2024-12-31'
./anmari.py search 'since:"1 week ago"'

# Status filters
./anmari.py search 'is:unread'
./anmari.py search 'is:read'

# Tag filters (local tags)
./anmari.py search 'tag:newsletter'
./anmari.py search 'tag:important'

# Gmail label filters
./anmari.py search 'label:INBOX'
./anmari.py search 'label:Important'

# Logical operators
./anmari.py search 'from:alice OR from:bob'
./anmari.py search 'subject:invoice AND is:unread'
./anmari.py search 'tag:newsletter NOT from:spam'

# Complex queries
./anmari.py search 'from:boss subject:urgent is:unread date:yesterday'
./anmari.py search '(tag:work OR tag:important) AND is:unread'
```

**Supported filters:**
- `subject:"text"` - Search in subject
- `from:"text"` - Search in from address/name
- `body:"text"` - Search in body preview
- `tag:tagname` - Filter by local tag
- `label:labelname` - Filter by Gmail label
- `is:read` / `is:unread` - Read status
- `date:YYYY-MM-DD` - Specific date
- `date:start..end` - Date range
- `since:"relative date"` - Relative dates (yesterday, "1 week ago", etc.)
- `uid:123` or `uid:100..200` - UID search
- Operators: `AND`, `OR`, `NOT`

### Tagging

Apply local tags to messages:

**Syntax**:
```bash
./anmari.py tag +tag_to_add search_query
./anmari.py tag -- +tag_to_add -tag_to_remove search_query
```

The `--` is optional if only adding tags, but must be included if removing tags so that the `-` is not parsed as a CLI option.

**Examples**:

```bash
# Add tags
./anmari.py tag -- +newsletter from:substack.com
./anmari.py tag -- +work +important subject:urgent

# Remove tags
./anmari.py tag -- -inbox +archived tag:old

# Multiple operations
./anmari.py tag -- +newsletter -inbox 'from:"The New York Times"'
```

### Action Queue (Staging)

Queue IMAP operations locally, review them, then apply to server:

```bash
# Queue operations
./anmari.py queue move --to Newsletters tag:newsletter
./anmari.py queue archive tag:old # alias for ./anmari.py queue move --to '[Gmail]/All Mail'
./anmari.py queue flag --add Seen date:yesterday
./anmari.py queue flag --remove Flagged tag:spam
./anmari.py queue label --add Tax from:turbotax.com
./anmari.py queue label --remove Inbox tag:archived

# Review pending actions
./anmari.py status

# Preview without executing
./anmari.py apply --dry-run

# Apply all pending actions
./anmari.py apply

# Manage queue
./anmari.py queue clear
./anmari.py queue undo --count 3
```

**Supported actions:**
- `move` - Move messages to folder
- `flag --add/--remove` - Add/remove IMAP flags (Seen, Flagged, Deleted)
- `label --add/--remove` - Add/remove Gmail labels
- `markread/markunread` - Alias for `flag --add/--remove '\\Seen'`
- `archive` - Alias for `move '[Gmail]/All Mail'`

### Other commands

```bash
# List all folders
./anmari.py folders

# Cleanup old messages
./anmari.py cleanup

# Interactive REPL
./anmari.py repl
```

### Interactive REPL

Start an interactive session with command history and tab completion:

```bash
./anmari.py repl

anmari> search is:unread
anmari> tag -- +important from:boss
anmari> queue move --to Newsletters tag:newsletter
anmari> queue archive tag:old # alias for move --to '[Gmail]/All Mail'
anmari> status
anmari> apply
anmari> exit
```

## Configuration

Config is stored at `~/.config/anmari/config.toml`:

```toml
[[accounts]]
email = "user@example.com"
imap_host = "imap.gmail.com"
imap_port = 993
cache_days = 90
```

## Examples

### Workflow: Process newsletters

```bash
# Search for newsletters
./anmari.py search 'from:substack.com OR subject:newsletter'

# Tag them
./anmari.py tag -- +newsletter 'from:substack.com OR subject:newsletter'

# Queue move to Newsletter folder
./anmari.py queue move --to Newsletters tag:newsletter

# Review and apply
./anmari.py status
./anmari.py apply
```

### Workflow: Archive old read messages

```bash
# Find old read messages
./anmari.py search 'is:read date:2024-01-01..2024-06-30'

# Tag for archiving
./anmari.py tag -- +archive 'is:read date:2024-01-01..2024-06-30'

# Queue archive operation
./anmari.py queue move --to Archive tag:archive

# Apply
./anmari.py apply
```

### Workflow: Mark spam

```bash
# Search for spam
./anmari.py search 'from:suspicious@spam.com'

# Tag as spam
./anmari.py tag -- +spam 'from:suspicious@spam.com'

# Queue move to Trash and add label
./anmari.py queue move --to Trash tag:spam
./anmari.py queue label --add Spam tag:spam

# Apply
./anmari.py apply
```
