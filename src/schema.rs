use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CachedMessage {
    pub uid: u32,
    pub folder: String,
    pub from: String,
    pub subject: String,
    pub date: DateTime<Utc>,
    pub body_preview: Option<String>,
    pub full_body: Option<String>,
    pub flags: Vec<String>,
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
