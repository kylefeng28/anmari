import sqlite3

def get_db_path(account):
    return f"anmari_{account}.db"

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
