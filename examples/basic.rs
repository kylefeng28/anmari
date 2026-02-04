use anyhow::Result;
use chrono::Utc;
use email_cache::{CachedMessage, CacheConfig, EmailCache};

fn main() -> Result<()> {
    // Initialize cache
    let config = CacheConfig {
        db_path: "test_cache.db".to_string(),
        cache_days: 90,
    };
    let cache = EmailCache::new(config)?;

    // Example: Insert a message
    let msg = CachedMessage {
        uid: 1,
        folder: "INBOX".to_string(),
        from: "sender@example.com".to_string(),
        subject: "Test Email".to_string(),
        date: Utc::now(),
        body_preview: Some("This is a preview...".to_string()),
        full_body: Some("Full email body here".to_string()),
        flags: vec!["\\Seen".to_string()],
    };
    cache.insert_message(&msg)?;

    // Add tags
    cache.add_tag(1, "INBOX", "important")?;
    cache.add_tag(1, "INBOX", "work")?;

    // Search
    let results = cache.search("Test")?;
    println!("Found {} messages", results.len());

    // Search by tag
    let tagged = cache.search_by_tag("important")?;
    println!("Found {} important messages", tagged.len());

    // Get tags for a message
    let tags = cache.get_tags(1, "INBOX")?;
    println!("Tags: {:?}", tags);

    // Cleanup old bodies
    let cleaned = cache.cleanup_old_bodies()?;
    println!("Cleaned {} old message bodies", cleaned);

    Ok(())
}
