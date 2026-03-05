use rusqlite::{Connection, Result};
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

#[derive(Debug)]
pub struct CachedFolderState {
    pub uidvalidity: u32,
    pub highestmodseq: u64,
}

pub struct EmailCache {
    conn: Connection,
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

    pub fn get_message(&self, uid: u32, folder: &str) -> Result<Option<CachedMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT uid, folder, from_addr, from_name, subject, date, flags 
             FROM messages WHERE uid = ? AND folder = ?"
        )?;

        let mut rows = stmt.query(rusqlite::params![uid, folder])?;

        if let Some(row) = rows.next()? {
            Ok(Some(CachedMessage {
                uid: row.get(0)?,
                folder: row.get(1)?,
                from_addr: row.get(2)?,
                from_name: row.get(3)?,
                subject: row.get(4)?,
                date: row.get(5)?,
                flags: row.get(6)?,
            }))
        } else {
            Ok(None)
        }
    }

}

fn get_db_path(account_index: usize) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let state_dir = dirs::state_dir()
        .ok_or("Could not find state directory")?;

    Ok(state_dir.join("anmari").join(format!("anmari_{}.db", account_index)))
}
