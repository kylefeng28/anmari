#!/usr/bin/env python3
"""
Anmari - Email cache with selective body storage
Python implementation
"""

import imaplib
import sqlite3
import email
from email.header import decode_header
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, List, Tuple
import click
import tomllib

# Config paths
def get_config_path() -> Path:
    """Get config file path (same as Rust version)"""
    if Path.home().joinpath("Library/Application Support").exists():
        # macOS
        return Path.home() / "Library/Application Support/anmari/config.toml"
    else:
        # Linux/Unix
        return Path.home() / ".config/anmari/config.toml"

def load_config():
    """Load config from TOML file"""
    config_path = get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found at {config_path}")

    with open(config_path, "rb") as f:
        return tomllib.load(f)

# Database
class EmailCache:
    def __init__(self, db_path: str, cache_days: int):
        self.db_path = db_path
        self.cache_days = cache_days
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                uid INTEGER NOT NULL,
                folder TEXT NOT NULL,
                from_addr TEXT NOT NULL,
                from_name TEXT,
                subject TEXT NOT NULL,
                date INTEGER NOT NULL,
                body_preview TEXT,
                full_body TEXT,
                flags TEXT,
                PRIMARY KEY (uid, folder)
            );
            CREATE INDEX IF NOT EXISTS idx_date ON messages(date);
            CREATE INDEX IF NOT EXISTS idx_folder ON messages(folder);

            CREATE TABLE IF NOT EXISTS folder_state (
                folder TEXT PRIMARY KEY,
                uidvalidity INTEGER NOT NULL,
                highestmodseq INTEGER NOT NULL
            );
        """)
        self.conn.commit()

    def get_message(self, uid: int, folder: str) -> Optional[dict]:
        """Get cached message"""
        cur = self.conn.execute(
            "SELECT * FROM messages WHERE uid = ? AND folder = ?",
            (uid, folder)
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def insert_message(self, uid: int, folder: str, from_addr: str, from_name: Optional[str],
                      subject: str, date: int, flags: str):
        """Insert or replace message"""
        self.conn.execute(
            """INSERT OR REPLACE INTO messages 
               (uid, folder, from_addr, from_name, subject, date, body_preview, full_body, flags)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (uid, folder, from_addr, from_name, subject, date, flags)
        )
        self.conn.commit()

    def update_flags(self, uid: int, folder: str, flags: str):
        """Update message flags"""
        self.conn.execute(
            "UPDATE messages SET flags = ? WHERE uid = ? AND folder = ?",
            (flags, uid, folder)
        )
        self.conn.commit()

    def delete_message(self, uid: int, folder: str):
        """Delete message"""
        self.conn.execute(
            "DELETE FROM messages WHERE uid = ? AND folder = ?",
            (uid, folder)
        )
        self.conn.commit()

    def get_last_seen_uid(self, folder: str) -> Optional[int]:
        """Get highest UID in cache"""
        cur = self.conn.execute(
            "SELECT MAX(uid) FROM messages WHERE folder = ?",
            (folder,)
        )
        result = cur.fetchone()[0]
        return result

    def get_all_uids(self, folder: str) -> List[int]:
        """Get all cached UIDs"""
        cur = self.conn.execute(
            "SELECT uid FROM messages WHERE folder = ? ORDER BY uid",
            (folder,)
        )
        return [row[0] for row in cur.fetchall()]

    def search(self, folder: str, query: str) -> List[dict]:
        """Search messages"""
        pattern = f"%{query}%"
        cur = self.conn.execute(
            """SELECT uid, from_addr, from_name, subject, date, flags
               FROM messages 
               WHERE folder = ? AND (from_addr LIKE ? OR subject LIKE ?)
               ORDER BY date DESC""",
            (folder, pattern, pattern)
        )
        return [dict(row) for row in cur.fetchall()]

# IMAP operations
def connect_imap(host: str, port: int, email: str, password: str):
    """Connect to IMAP server"""
    imap = imaplib.IMAP4_SSL(host, port)
    imap.login(email, password)
    return imap

def decode_header_value(value):
    """Decode email header value"""
    if not value:
        return ""
    decoded = decode_header(value)
    parts = []
    for content, encoding in decoded:
        if isinstance(content, bytes):
            parts.append(content.decode(encoding or 'utf-8', errors='ignore'))
        else:
            parts.append(content)
    return " ".join(parts)

def parse_address(addr_str):
    """Parse email address into (name, addr)"""
    if not addr_str:
        return None, ""

    # Simple parsing - email.utils.parseaddr would be better
    addr_str = decode_header_value(addr_str)
    if '<' in addr_str and '>' in addr_str:
        name = addr_str.split('<')[0].strip().strip('"')
        addr = addr_str.split('<')[1].split('>')[0].strip()
        return name or None, addr
    return None, addr_str.strip()

# Commands
@click.group()
def cli():
    """Anmari - Email cache with selective body storage"""
    pass

@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default='INBOX', help='Folder to sync')
@click.option('--page-size', default=100, help='Page size for fetching')
def sync(account: int, folder: str, page_size: int):
    """Sync emails from IMAP to local cache"""
    config = load_config()

    if account >= len(config['accounts']):
        click.echo(f"Error: Account {account} not found", err=True)
        return

    acc = config['accounts'][account]
    email_addr = acc['email']

    # Get password
    password = acc.get('password')
    if not password:
        click.echo("Error: No password configured", err=True)
        return

    # Initialize cache
    db_path = f"anmari_{account}.db"
    cache = EmailCache(db_path, acc.get('cache_days', 90))

    click.echo(f"Syncing {folder} from {email_addr}...")

    # Connect to IMAP
    imap = connect_imap(acc['imap_host'], acc['imap_port'], email_addr, password)
    imap.select(folder, readonly=True)

    # Get all UIDs
    _, data = imap.uid('search', None, 'ALL')
    all_uids = [int(uid) for uid in data[0].split()]

    if not all_uids:
        click.echo("No messages found")
        imap.close()
        imap.logout()
        return

    last_seen_uid = cache.get_last_seen_uid(folder)
    server_uids = set(all_uids)

    total_new = 0
    total_updated = 0

    # Fetch messages in batches
    for i in range(0, len(all_uids), page_size):
        batch = all_uids[i:i+page_size]
        uid_list = ','.join(map(str, batch))

        click.echo(f"Processing UIDs {batch[0]}-{batch[-1]} ({len(batch)} messages)...")

        # Fetch envelope data
        _, data = imap.uid('fetch', uid_list, '(FLAGS ENVELOPE')

        for item in data:
            if not isinstance(item, tuple):
                continue

            # Parse response
            response = item[0].decode('utf-8', errors='ignore')
            uid_match = response.split('UID ')[1].split()[0] if 'UID ' in response else None
            if not uid_match:
                continue

            uid = int(uid_match)

            # Parse flags
            flags_start = response.find('FLAGS (')
            flags_end = response.find(')', flags_start)
            flags = response[flags_start+7:flags_end] if flags_start != -1 else ""

            # Parse envelope
            msg = email.message_from_bytes(item[1])
            from_name, from_addr = parse_address(msg.get('From', ''))
            subject = decode_header_value(msg.get('Subject', ''))
            date_str = msg.get('Date', '')

            # Parse date
            try:
                date_tuple = email.utils.parsedate_to_datetime(date_str)
                date_ts = int(date_tuple.timestamp())
            except:
                date_ts = int(datetime.now().timestamp())

            # Check if exists
            cached = cache.get_message(uid, folder)
            if cached:
                if cached['flags'] != flags:
                    cache.update_flags(uid, folder, flags)
                    total_updated += 1
            else:
                cache.insert_message(uid, folder, from_addr, from_name, subject, date_ts, flags)
                if last_seen_uid is None or uid > last_seen_uid:
                    total_new += 1

    # Detect expunged
    total_expunged = 0
    if last_seen_uid:
        cached_uids = cache.get_all_uids(folder)
        for uid in cached_uids:
            if uid not in server_uids:
                cache.delete_message(uid, folder)
                total_expunged += 1

    imap.close()
    imap.logout()

    click.echo(f"\nSync complete! New: {total_new}, Updated: {total_updated}, Expunged: {total_expunged}")

@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default='INBOX', help='Folder to search')
@click.option('--limit', '-l', default=20, help='Limit results')
@click.option('--all', is_flag=True, help='Show all results')
@click.argument('query')
def search(account: int, folder: str, limit: int, all: bool, query: str):
    """Search emails in local cache"""
    config = load_config()

    if account >= len(config['accounts']):
        click.echo(f"Error: Account {account} not found", err=True)
        return

    acc = config['accounts'][account]

    # Initialize cache
    db_path = f"anmari_{account}.db"
    cache = EmailCache(db_path, acc.get('cache_days', 90))

    # Search
    results = cache.search(folder, query)

    display_limit = len(results) if all else limit

    click.echo(f"Found {len(results)} messages in cache:")
    for msg in results[:display_limit]:
        date = datetime.fromtimestamp(msg['date']).strftime('%Y-%m-%d')
        from_display = msg['from_name'] if msg['from_name'] else msg['from_addr']
        click.echo(f"  [{msg['uid']}] {date} {from_display} - {msg['subject']}")

    if len(results) > display_limit:
        click.echo(f"  ... and {len(results) - display_limit} more")

if __name__ == '__main__':
    cli()
