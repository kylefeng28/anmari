use anyhow::Result;
use chrono::{DateTime, Duration, Utc};
use email::search_query::{filter::SearchEmailsFilterQuery, SearchEmailsQuery};
use rusqlite::{params, Connection};

use crate::schema::{CachedMessage, CacheConfig};

pub struct EmailCache {
    conn: Connection,
    config: CacheConfig,
}

impl EmailCache {
    pub fn new(config: CacheConfig) -> Result<Self> {
        let conn = Connection::open(&config.db_path)?;
        let cache = Self { conn, config };
        cache.init_db()?;
        Ok(cache)
    }

    fn init_db(&self) -> Result<()> {
        self.conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS messages (
                uid INTEGER NOT NULL,
                folder TEXT NOT NULL,
                from_addr TEXT NOT NULL,
                subject TEXT NOT NULL,
                date INTEGER NOT NULL,
                body_preview TEXT,
                full_body TEXT,
                flags TEXT,
                PRIMARY KEY (uid, folder)
            );
            CREATE INDEX IF NOT EXISTS idx_date ON messages(date);
            CREATE INDEX IF NOT EXISTS idx_folder ON messages(folder);
            
            CREATE TABLE IF NOT EXISTS tags (
                message_uid INTEGER NOT NULL,
                message_folder TEXT NOT NULL,
                tag TEXT NOT NULL,
                PRIMARY KEY (message_uid, message_folder, tag),
                FOREIGN KEY (message_uid, message_folder) REFERENCES messages(uid, folder)
            );
            CREATE INDEX IF NOT EXISTS idx_tag ON tags(tag);",
        )?;
        Ok(())
    }

    pub fn insert_message(&self, msg: &CachedMessage) -> Result<()> {
        let flags_json = serde_json::to_string(&msg.flags)?;
        self.conn.execute(
            "INSERT OR REPLACE INTO messages 
             (uid, folder, from_addr, subject, date, body_preview, full_body, flags)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                msg.uid,
                &msg.folder,
                &msg.from,
                &msg.subject,
                msg.date.timestamp(),
                &msg.body_preview,
                &msg.full_body,
                flags_json,
            ],
        )?;
        Ok(())
    }

    pub fn get_message(&self, uid: u32, folder: &str) -> Result<Option<CachedMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT uid, folder, from_addr, subject, date, body_preview, full_body, flags
             FROM messages WHERE uid = ?1 AND folder = ?2",
        )?;

        let msg = stmt.query_row(params![uid, folder], |row| {
            let flags_json: String = row.get(7)?;
            let flags: Vec<String> = serde_json::from_str(&flags_json).unwrap_or_default();
            
            Ok(CachedMessage {
                uid: row.get(0)?,
                folder: row.get(1)?,
                from: row.get(2)?,
                subject: row.get(3)?,
                date: DateTime::from_timestamp(row.get(4)?, 0).unwrap_or_default(),
                body_preview: row.get(5)?,
                full_body: row.get(6)?,
                flags,
            })
        });

        match msg {
            Ok(m) => Ok(Some(m)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }

    pub fn search(&self, query: &str) -> Result<Vec<CachedMessage>> {
        let pattern = format!("%{}%", query);
        let mut stmt = self.conn.prepare(
            "SELECT uid, folder, from_addr, subject, date, body_preview, full_body, flags
             FROM messages 
             WHERE from_addr LIKE ?1 OR subject LIKE ?1 OR body_preview LIKE ?1
             ORDER BY date DESC",
        )?;

        let rows = stmt.query_map(params![pattern], |row| {
            let flags_json: String = row.get(7)?;
            let flags: Vec<String> = serde_json::from_str(&flags_json).unwrap_or_default();
            
            Ok(CachedMessage {
                uid: row.get(0)?,
                folder: row.get(1)?,
                from: row.get(2)?,
                subject: row.get(3)?,
                date: DateTime::from_timestamp(row.get(4)?, 0).unwrap_or_default(),
                body_preview: row.get(5)?,
                full_body: row.get(6)?,
                flags,
            })
        })?;

        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn add_tag(&self, uid: u32, folder: &str, tag: &str) -> Result<()> {
        self.conn.execute(
            "INSERT OR IGNORE INTO tags (message_uid, message_folder, tag) VALUES (?1, ?2, ?3)",
            params![uid, folder, tag],
        )?;
        Ok(())
    }

    pub fn remove_tag(&self, uid: u32, folder: &str, tag: &str) -> Result<()> {
        self.conn.execute(
            "DELETE FROM tags WHERE message_uid = ?1 AND message_folder = ?2 AND tag = ?3",
            params![uid, folder, tag],
        )?;
        Ok(())
    }

    pub fn get_tags(&self, uid: u32, folder: &str) -> Result<Vec<String>> {
        let mut stmt = self.conn.prepare(
            "SELECT tag FROM tags WHERE message_uid = ?1 AND message_folder = ?2",
        )?;
        
        let tags = stmt.query_map(params![uid, folder], |row| row.get(0))?;
        tags.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn search_by_tag(&self, tag: &str) -> Result<Vec<CachedMessage>> {
        let mut stmt = self.conn.prepare(
            "SELECT m.uid, m.folder, m.from_addr, m.subject, m.date, m.body_preview, m.full_body, m.flags
             FROM messages m
             JOIN tags t ON m.uid = t.message_uid AND m.folder = t.message_folder
             WHERE t.tag = ?1
             ORDER BY m.date DESC",
        )?;

        let rows = stmt.query_map(params![tag], |row| {
            let flags_json: String = row.get(7)?;
            let flags: Vec<String> = serde_json::from_str(&flags_json).unwrap_or_default();
            
            Ok(CachedMessage {
                uid: row.get(0)?,
                folder: row.get(1)?,
                from: row.get(2)?,
                subject: row.get(3)?,
                date: DateTime::from_timestamp(row.get(4)?, 0).unwrap_or_default(),
                body_preview: row.get(5)?,
                full_body: row.get(6)?,
                flags,
            })
        })?;

        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    pub fn search_with_query(&self, query: &SearchEmailsQuery, folder: &str) -> Result<Vec<CachedMessage>> {
        let mut sql = String::from(
            "SELECT uid, folder, from_addr, subject, date, body_preview, full_body, flags
             FROM messages WHERE folder = ?1"
        );
        
        let mut params: Vec<Box<dyn rusqlite::ToSql>> = vec![Box::new(folder.to_string())];
        
        // Build WHERE clause from filter
        if let Some(ref filter) = query.filter {
            let filter_sql = self.build_filter_sql(filter, &mut params);
            sql.push_str(&format!(" AND ({})", filter_sql));
        }
        
        sql.push_str(" ORDER BY date DESC");
        
        let mut stmt = self.conn.prepare(&sql)?;
        let param_refs: Vec<&dyn rusqlite::ToSql> = params.iter().map(|p| p.as_ref()).collect();
        
        let rows = stmt.query_map(param_refs.as_slice(), |row| {
            let flags_json: String = row.get(7)?;
            let flags: Vec<String> = serde_json::from_str(&flags_json).unwrap_or_default();
            
            Ok(CachedMessage {
                uid: row.get(0)?,
                folder: row.get(1)?,
                from: row.get(2)?,
                subject: row.get(3)?,
                date: DateTime::from_timestamp(row.get(4)?, 0).unwrap_or_default(),
                body_preview: row.get(5)?,
                full_body: row.get(6)?,
                flags,
            })
        })?;

        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }
    
    fn build_filter_sql(&self, filter: &SearchEmailsFilterQuery, params: &mut Vec<Box<dyn rusqlite::ToSql>>) -> String {
        use email::search_query::filter::SearchEmailsFilterQuery::*;
        
        match filter {
            Subject(s) => {
                params.push(Box::new(format!("%{}%", s)));
                format!("subject LIKE ?{}", params.len())
            }
            From(f) => {
                params.push(Box::new(format!("%{}%", f)));
                format!("from_addr LIKE ?{}", params.len())
            }
            And(left, right) => {
                let left_sql = self.build_filter_sql(left, params);
                let right_sql = self.build_filter_sql(right, params);
                format!("({} AND {})", left_sql, right_sql)
            }
            Or(left, right) => {
                let left_sql = self.build_filter_sql(left, params);
                let right_sql = self.build_filter_sql(right, params);
                format!("({} OR {})", left_sql, right_sql)
            }
            _ => "1=1".to_string(), // Unsupported filters default to match all
        }
    }

    pub fn cleanup_old_bodies(&self) -> Result<usize> {
        let cutoff = Utc::now() - Duration::days(self.config.cache_days as i64);
        let affected = self.conn.execute(
            "UPDATE messages SET full_body = NULL WHERE date < ?1 AND full_body IS NOT NULL",
            params![cutoff.timestamp()],
        )?;
        Ok(affected)
    }
}
