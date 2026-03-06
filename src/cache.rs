use rusqlite::{Connection, Transaction, Result, params, OptionalExtension};
use std::num::NonZeroU32;
use std::path::PathBuf;

#[derive(Debug)]
pub struct CachedMessage {
    pub uid: u32,
    pub folder: String,
    pub from_addr: String,
    pub from_name: Option<String>,
    pub subject: String,
    pub date: String,  // Stored as TEXT datetime in SQLite
    pub flags: String,
}

impl CachedMessage {
    pub fn get_flags_as_list(&self) -> Vec<String> {
        if self.flags.is_empty() {
            Vec::new()
        } else {
            self.flags.split(' ').map(|s| s.to_string()).collect()
        }
    }

    fn from_row(row: &rusqlite::Row) -> Result<Self> {
        Ok(CachedMessage {
            uid: row.get(0)?,
            folder: row.get(1)?,
            from_addr: row.get(2)?,
            from_name: row.get(3)?,
            subject: row.get(4)?,
            date: row.get(5)?,
            flags: row.get(6)?,
        })
    }
}

#[derive(Debug)]
pub struct CachedFolderState {
    pub uidvalidity: u32,
    pub highestmodseq: u64,
}

pub struct EmailCache {
    conn: Connection,
}

fn normalize_flags_serialize(flags: &[String]) -> String {
    let mut sorted = flags.to_vec();
    sorted.sort();
    sorted.join(" ")
}

fn get_db_path(account_index: usize) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let state_dir = dirs::state_dir()
        .ok_or("Could not find state directory")?;

    Ok(state_dir.join("anmari").join(format!("anmari_{}.db", account_index)))
}

impl EmailCache {
    pub fn new(account_index: usize) -> Result<Self, Box<dyn std::error::Error>> {
        let db_path = get_db_path(account_index)?;

        // Create parent directory if it doesn't exist
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let conn = Connection::open(&db_path)?;
        let mut cache = Self { conn };
        cache.init_db()?;
        Ok(cache)
    }

    fn init_db(&mut self) -> Result<()> {
        self.conn.execute_batch(
            r#"
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

            CREATE TABLE IF NOT EXISTS action_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT NOT NULL,
                folder TEXT NOT NULL,
                action_type TEXT NOT NULL,
                action_data TEXT NOT NULL,
                message_count INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                status TEXT DEFAULT 'pending'
            );
            CREATE INDEX IF NOT EXISTS idx_action_queue_status ON action_queue(status);
            "#,
        )?;
        Ok(())
    }

    // ──────────────────────────────────────────────────────────────────────────────
    // Folder state operations
    // ──────────────────────────────────────────────────────────────────────────────
    pub fn get_folder_state(&self, folder: &str) -> Result<Option<CachedFolderState>> {
        let mut stmt = self.conn.prepare(
            "SELECT uidvalidity, highestmodseq FROM folder_state WHERE folder = ?"
        )?;

        let mut rows = stmt.query([folder])?;

        if let Some(row) = rows.next()? {
            Ok(Some(CachedFolderState {
                uidvalidity: row.get(0)?,
                highestmodseq: row.get(1)?,
            }))
        } else {
            Ok(None)
        }
    }

    pub fn set_folder_state(&self, folder: &str, uidvalidity: u32, highestmodseq: u64) -> Result<()> {
        self.conn.execute(
            "INSERT OR REPLACE INTO folder_state (folder, uidvalidity, highestmodseq) VALUES (?, ?, ?)",
            params![folder, uidvalidity, highestmodseq],
        )?;
        Ok(())
    }

    pub fn get_last_seen_uid(&self, folder: &str) -> Result<Option<u32>> {
        let mut stmt = self.conn.prepare("SELECT MAX(uid) FROM messages WHERE folder = ?")?;
        let result: Option<u32> = stmt.query_row(params![folder], |row| row.get(0))?;
        Ok(result)
    }

    pub fn get_all_uids(&self, folder: &str) -> Result<Vec<NonZeroU32>> {
        let mut stmt = self.conn.prepare_cached("SELECT uid FROM messages WHERE folder = ?")?;
        let rows = stmt.query_map(params![folder], |row| row.get(0))?;
        rows.collect()
    }

    pub fn clear_folder_messages_for_uidvalidity_change(&self, folder: &str) -> Result<()> {
        self.conn.execute("DELETE FROM folder_state WHERE folder = ?", params![folder])?;
        self.conn.execute("DELETE FROM messages WHERE folder = ?", params![folder])?;
        Ok(())
    }

    pub fn clear_folders_state_for_cache_cleanup(&self, folders: &[String]) -> Result<()> {
        let placeholders = vec!["?"; folders.len()].join(", ");
        let query = format!("DELETE FROM folder_state WHERE folder IN ({})", placeholders);
        self.conn.execute(&query, rusqlite::params_from_iter(folders))?;
        Ok(())
    }

    // ──────────────────────────────────────────────────────────────────────────────
    // Message operations
    // ──────────────────────────────────────────────────────────────────────────────
    pub fn get_message(&self, uid: u32, folder: &str) -> Result<Option<CachedMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT uid, folder, from_addr, from_name, subject, date, flags
             FROM messages WHERE uid = ? AND folder = ?"
        )?;

        let mut rows = stmt.query(params![uid, folder])?;

        if let Some(row) = rows.next()? {
            Ok(Some(CachedMessage::from_row(row)?))
        } else {
            Ok(None)
        }
    }

    pub fn insert_message(
        &self,
        uid: u32,
        folder: &str,
        from_addr: &str,
        from_name: Option<&str>,
        subject: &str,
        date: &str,
        flags: &[String],
    ) -> Result<()> {
        let flags_str = normalize_flags_serialize(flags);
        self.conn.execute(
            "INSERT OR REPLACE INTO messages (uid, folder, from_addr, from_name, subject, date, flags, body_preview, full_body)
             VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            params![uid, folder, from_addr, from_name, subject, date, flags_str],
        )?;
        Ok(())
    }

    /// IMPORTANT: make sure to call .commit() on the transaction object returned
    pub fn transaction(&self) -> Result<Transaction<'_>> {
        self.conn.unchecked_transaction()
    }

    pub fn delete_message(&self, uid: u32, folder: &str) -> Result<()> {
        self.conn.execute(
            "DELETE FROM messages WHERE uid = ? AND folder = ?",
            params![uid, folder],
        )?;
        Ok(())
    }

    pub fn update_message_flags(&self, uid: u32, folder: &str, flags: &[String]) -> Result<()> {
        let flags_str = normalize_flags_serialize(flags);
        self.conn.execute(
            "UPDATE messages SET flags = ? WHERE uid = ? AND folder = ?",
            params![flags_str, uid, folder],
        )?;
        Ok(())
    }

    pub fn copy_message(&self, source_uid: u32, source_folder: &str, dest_uid: u32, dest_folder: &str) -> Result<()> {
        // Copy message data
        self.conn.execute(
            "INSERT OR REPLACE INTO messages
             (uid, folder, from_addr, from_name, subject, date, body_preview, full_body, flags)
             SELECT ?, ?, from_addr, from_name, subject, date, body_preview, full_body, flags
             FROM messages WHERE uid = ? AND folder = ?",
            params![dest_uid, dest_folder, source_uid, source_folder],
        )?;

        // Copy tags
        self.conn.execute(
            "INSERT OR IGNORE INTO tags (uid, folder, tag)
             SELECT ?, ?, tag FROM tags WHERE uid = ? AND folder = ?",
            params![dest_uid, dest_folder, source_uid, source_folder],
        )?;

        // Copy Gmail labels
        self.conn.execute(
            "INSERT OR IGNORE INTO gm_labels (uid, folder, label)
             SELECT ?, ?, label FROM gm_labels WHERE uid = ? AND folder = ?",
            params![dest_uid, dest_folder, source_uid, source_folder],
        )?;

        Ok(())
    }

    // ──────────────────────────────────────────────────────────────────────────────
    // Tag operations
    // ──────────────────────────────────────────────────────────────────────────────
    pub fn get_tags(&self, uid: u32, folder: &str) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT tag FROM tags WHERE uid = ? AND folder = ? ORDER BY tag"
        )?;
        let rows = stmt.query_map(params![uid, folder], |row| row.get(0))?;
        rows.collect()
    }

    pub fn add_tag(&self, uid: u32, folder: &str, tag: &str) -> Result<()> {
        self.conn.execute(
            "INSERT OR IGNORE INTO tags (uid, folder, tag) VALUES (?, ?, ?)",
            params![uid, folder, tag],
        )?;
        Ok(())
    }

    pub fn remove_tag(&self, uid: u32, folder: &str, tag: &str) -> Result<()> {
        self.conn.execute(
            "DELETE FROM tags WHERE uid = ? AND folder = ? AND tag = ?",
            params![uid, folder, tag],
        )?;
        Ok(())
    }

    pub fn tag_messages(&self, messages: &[CachedMessage], tags_to_add: &[String], tags_to_remove: &[String]) -> Result<usize> {
        for msg in messages {
            for tag in tags_to_add {
                self.add_tag(msg.uid, &msg.folder, tag)?;
            }
            for tag in tags_to_remove {
                self.remove_tag(msg.uid, &msg.folder, tag)?;
            }
        }
        Ok(messages.len())
    }

    // ──────────────────────────────────────────────────────────────────────────────
    // Gmail label operations
    // ──────────────────────────────────────────────────────────────────────────────
    pub fn get_gm_labels(&self, uid: u32, folder: &str) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT label FROM gm_labels WHERE uid = ? AND folder = ? ORDER BY label"
        )?;
        let rows = stmt.query_map(params![uid, folder], |row| row.get(0))?;
        rows.collect()
    }

    pub fn set_gm_labels(&self, uid: u32, folder: &str, labels: &[String]) -> Result<()> {
        // Clear existing labels
        self.conn.execute(
            "DELETE FROM gm_labels WHERE uid = ? AND folder = ?",
            params![uid, folder],
        )?;

        // Insert new labels
        for label in labels {
            self.conn.execute(
                "INSERT OR IGNORE INTO gm_labels (uid, folder, label) VALUES (?, ?, ?)",
                params![uid, folder, label],
            )?;
        }
        Ok(())
    }

    // ──────────────────────────────────────────────────────────────────────────────
    // Search
    // ──────────────────────────────────────────────────────────────────────────────
    pub fn search(&self, folder: &str, query: &str) -> Result<Vec<CachedMessage>> {
        // Simple search implementation - just search in subject and from_addr
        // For now, we'll do a basic LIKE search. Full query parsing can be added later.
        let search_pattern = format!("%{}%", query);

        let mut stmt = self.conn.prepare(
            "SELECT uid, folder, from_addr, from_name, subject, date, flags
             FROM messages
             WHERE folder = ? AND (subject LIKE ? OR from_addr LIKE ?)
             ORDER BY date DESC"
        )?;

        let rows = stmt.query_map(
            params![folder, search_pattern, search_pattern],
            CachedMessage::from_row,
        )?;

        rows.collect()
    }
}
