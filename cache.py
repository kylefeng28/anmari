import sqlite3
from typing import NamedTuple, Optional

from search import parse_search_query
from utils import decode_if_bytes

def get_db_path(account):
    return f"anmari_{account}.db"


def normalize_flags_serialize(flags: str | list[str]) -> str:
    if isinstance(flags, list):
        flags = ' '.join(sorted(flags))
    return flags


def normalize_flags_deserialize(flags: str) -> list[str]:
    if flags == '':
        return []
    return sorted(flags.split(' '))

class CachedMessage(NamedTuple):
    uid: int
    folder: str
    from_addr: str
    from_name: Optional[str]
    subject: str
    date: int
    flags: str

    @classmethod
    def from_row(cls, row):
        return CachedMessage(**{
            k: decode_if_bytes(v)
            for k, v in dict(row).items() if k in CachedMessage._fields
        })

    def get_flags_as_list(self):
        return normalize_flags_deserialize(self.flags)


class CachedFolderState(NamedTuple):
    uidvalidity: str
    highestmodseq: str


# Database
class EmailCache:
    def __init__(self, account: str, cache_days: int):
        db_path = get_db_path(account)
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

            CREATE TABLE IF NOT EXISTS tags (
                uid INTEGER NOT NULL,
                folder TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (uid, folder, tag),
                FOREIGN KEY (uid, folder) REFERENCES messages(uid, folder) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
            CREATE INDEX IF NOT EXISTS idx_tags_message ON tags(uid, folder);
            
            CREATE TABLE IF NOT EXISTS gm_labels (
                uid INTEGER NOT NULL,
                folder TEXT NOT NULL,
                label TEXT NOT NULL,
                PRIMARY KEY (uid, folder, label),
                FOREIGN KEY (uid, folder) REFERENCES messages(uid, folder) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_gm_labels_label ON gm_labels(label);
            CREATE INDEX IF NOT EXISTS idx_gm_labels_message ON gm_labels(uid, folder);
        """)
        self.conn.commit()

    def get_message(self, uid: int, folder: str) -> Optional[CachedMessage]:
        """Get cached message"""
        cur = self.conn.execute(
            "SELECT * FROM messages WHERE uid = ? AND folder = ?",
            (uid, folder)
        )
        row = cur.fetchone()
        return CachedMessage.from_row(row) if row else None

    def insert_message(self, uid: int, folder: str, from_addr: str, from_name: Optional[str],
                      subject: str, date: int, flags: str | list[str]):
        flags = normalize_flags_serialize(flags)
        """Insert or replace message"""
        self.conn.execute(
            """INSERT OR REPLACE INTO messages
               (uid, folder, from_addr, from_name, subject, date, body_preview, full_body, flags)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (uid, folder, from_addr, from_name, subject, date, flags)
        )
        self.conn.commit()

    def update_flags(self, uid: int, folder: str, flags: str | list[str]):
        """Update message flags"""
        flags = normalize_flags_serialize(flags)
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

    def search(self, folder: str, query: str) -> List[CachedMessage]:
        conditions, params, has_tag_filter = parse_search_query(query)

        # If query includes tag filter, join with tags table
        if has_tag_filter:
            sql = f"""SELECT DISTINCT m.*
                      FROM messages m
                      LEFT JOIN tags t ON m.uid = t.uid AND m.folder = t.folder
                      WHERE m.folder = ? AND ({conditions})
                      ORDER BY m.date DESC"""
        else:
            sql = f"""SELECT *
                      FROM messages m
                      WHERE folder = ? AND ({conditions})
                      ORDER BY date DESC"""

        print(f'[debug] {conditions}, {params}')

        cur = self.conn.execute(sql, [folder] + params)
        return [CachedMessage.from_row(row) for row in cur.fetchall()]

    def get_folder_state(self, folder: str) -> Optional[tuple[int, int]]:
        """Get cached UIDVALIDITY and HIGHESTMODSEQ for folder"""
        cur = self.conn.execute(
            "SELECT uidvalidity, highestmodseq FROM folder_state WHERE folder = ?",
            (folder,)
        )
        row = cur.fetchone()
        return CachedFolderState(row[0], row[1]) if row else None

    def set_folder_state(self, folder: str, uidvalidity: int, highestmodseq: int):
        """Cache UIDVALIDITY and HIGHESTMODSEQ for folder"""
        self.conn.execute(
            """INSERT OR REPLACE INTO folder_state (folder, uidvalidity, highestmodseq)
               VALUES (?, ?, ?)""",
            (folder, uidvalidity, highestmodseq)
        )
        self.conn.commit()

    def clear_folder(self, folder: str):
        """Clear all messages and state for folder (used when UIDVALIDITY changes)"""
        self.conn.execute("DELETE FROM messages WHERE folder = ?", (folder,))
        self.conn.execute("DELETE FROM folder_state WHERE folder = ?", (folder,))
        self.conn.commit()

    # Tag operations
    def add_tag(self, uid: int, folder: str, tag: str):
        """Add a tag to a message"""
        self.conn.execute(
            "INSERT OR IGNORE INTO tags (uid, folder, tag) VALUES (?, ?, ?)",
            (uid, folder, tag)
        )
        self.conn.commit()

    def remove_tag(self, uid: int, folder: str, tag: str):
        """Remove a tag from a message"""
        self.conn.execute(
            "DELETE FROM tags WHERE uid = ? AND folder = ? AND tag = ?",
            (uid, folder, tag)
        )
        self.conn.commit()

    def get_tags(self, uid: int, folder: str) -> list[str]:
        """Get all tags for a message"""
        cur = self.conn.execute(
            "SELECT tag FROM tags WHERE uid = ? AND folder = ? ORDER BY tag",
            (uid, folder)
        )
        return [row[0] for row in cur.fetchall()]

    def tag_messages(self, messages: list[CachedMessage], tags_to_add: list[str], tags_to_remove: list[str] = None):
        """Apply tags to messages matching a query (like notmuch tag command)

        Args:
            messages: List of CachedMessage or (uid, folder)
            tags_to_add: List of tags to add (e.g., ['newsletter', 'automated'])
            tags_to_remove: List of tags to remove (e.g., ['inbox', 'unread'])

        Returns:
            Number of messages tagged
        """
        for msg in messages:
            for tag in tags_to_add:
                self.add_tag(msg.uid, msg.folder, tag)

            if tags_to_remove:
                for tag in tags_to_remove:
                    self.remove_tag(msg.uid, msg.folder, tag)

        return len(messages)

    # Gmail label operations
    def set_gm_labels(self, uid: int, folder: str, labels: list[str]):
        """Set Gmail labels for a message (replaces existing labels)"""
        # Clear existing labels
        self.conn.execute(
            "DELETE FROM gm_labels WHERE uid = ? AND folder = ?",
            (uid, folder)
        )
        # Insert new labels
        for label in labels:
            self.conn.execute(
                "INSERT OR IGNORE INTO gm_labels (uid, folder, label) VALUES (?, ?, ?)",
                (uid, folder, label)
            )
        self.conn.commit()

    def get_gm_labels(self, uid: int, folder: str) -> list[str]:
        """Get all Gmail labels for a message"""
        cur = self.conn.execute(
            "SELECT label FROM gm_labels WHERE uid = ? AND folder = ? ORDER BY label",
            (uid, folder)
        )
        return [row[0] for row in cur.fetchall()]
