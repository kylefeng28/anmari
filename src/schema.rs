use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use email::envelope::{Envelope, Address};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedMessage {
    pub uid: u32,
    pub folder: String,
    pub from_addr: String,
    pub from_name: Option<String>,
    pub subject: String,
    pub date: DateTime<Utc>,
    pub body_preview: Option<String>,
    pub full_body: Option<String>,
    pub flags: Vec<String>,
}

impl CachedMessage {
    pub fn new(uid: u32, folder: String, msg_date: DateTime<Utc>, full_body: Option<String>, envelope: &Envelope) -> Self {
        Self {
            uid,
            folder: folder,
            from_addr: envelope.from.addr.clone(),
            from_name:  envelope.from.name.clone(),
            subject: envelope.subject.clone(),
            date: msg_date,
            // TODO truncate full_body for body_preview
            body_preview: None,
            full_body,
            flags: envelope.flags.iter().map(|f| f.to_string()).collect(),
        }
    }

    pub fn from_as_address(&self) -> Address {
        Address {
            addr: self.from_addr.clone(),
            name: self.from_name.clone()
        }
    }
}

#[derive(Debug, Clone)]
pub struct CacheConfig {
    pub db_path: String,
    pub cache_days: u32,
}

impl Default for CacheConfig {
    fn default() -> Self {
        Self {
            db_path: "email_cache.db".to_string(),
            cache_days: 90,
        }
    }
}

