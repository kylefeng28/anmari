# anmari (あんまり)

An email tagging system, similar to [notmuch](https://notmuchmail.org/) but can connect to without requiring Maildir (i.e. avoiding mbsync/offlineimap + a large local Maildir + lieer for tag synchronization for Gmail).

It features a minimal email cache with selective body storage - keep headers forever, bodies for a set amount of time days (e.g. 90 days).

Written in Rust using [email-lib](https://crates.io/crates/email-lib).

## Features

- **Selective caching**: Store headers always, full bodies only for recent emails (configurable days)
- **Tag system**: notmuch-style tagging for organizing emails
- **Search**: Query by sender, subject, or body preview
- **Automatic cleanup**: Remove old message bodies while keeping headers

## CLI Usage

### Add an account

```bash
anmari add-account \
  --email user@example.com \
  --imap-host imap.example.com \
  --imap-port 993 \
  --cache-days 90 \
  --password "your-password"
```

### Search emails

Search using notmuch-style query syntax:

```bash
# Search by subject
anmari search "subject:meeting"

# Search by sender
anmari search "from:alice@example.com"

# Combine queries
anmari search "subject:invoice and from:billing"

# Use specific account (default is 0)
anmari search --account 1 "subject:test"
```

Query syntax supports:
- `subject:text` - Match subject
- `from:address` - Match sender
- `to:address` - Match recipient
- `and` / `or` - Combine filters
- Date filters and more (see [email-lib docs](https://docs.rs/email-lib/latest/email/search_query/))

### List accounts

```bash
anmari list-accounts
```

### Show config path

```bash
anmari config-path
```

## Configuration

Config is stored at `~/.config/anmari/config.toml` (or platform equivalent):

```toml
[[accounts]]
email = "user@example.com"
imap_host = "imap.example.com"
imap_port = 993
cache_days = 90
```

## Library Usage

```rust
use anmari::{CachedMessage, CacheConfig, EmailCache};
use chrono::Utc;

// Initialize cache
let config = CacheConfig {
    db_path: "emails.db".to_string(),
    cache_days: 90,
};
let cache = EmailCache::new(config)?;

// Insert a message
let msg = CachedMessage {
    uid: 1,
    folder: "INBOX".to_string(),
    from: "sender@example.com".to_string(),
    subject: "Important Email".to_string(),
    date: Utc::now(),
    body_preview: Some("First 200 chars...".to_string()),
    full_body: Some("Full email body".to_string()),
    flags: vec!["\\Seen".to_string()],
};
cache.insert_message(&msg)?;

// Add tags
cache.add_tag(1, "INBOX", "important")?;

// Search
let results = cache.search("Important")?;
let work_emails = cache.search_by_tag("work")?;

// Cleanup old bodies
cache.cleanup_old_bodies()?;
```

## Building

```bash
cargo build --release
```

The binary will be at `target/release/anmari`.
