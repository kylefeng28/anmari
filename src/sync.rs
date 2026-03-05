use std::collections::HashSet;

use io_imap::{
    coroutines::{fetch::*, select::*},
    types::{
        flag::FlagFetch,
        fetch::{MacroOrMessageDataItemNames, MessageDataItem},
        mailbox::Mailbox,
        sequence::SequenceSet,
    },
};

use crate::{cache::EmailCache, imap::ImapClient};

pub struct Syncer<'a> {
    client: &'a mut ImapClient,
    cache: &'a EmailCache,
}

impl<'a> Syncer<'a> {
    pub fn new(client: &'a mut ImapClient, cache: &'a EmailCache) -> Self {
        Self { client, cache }
    }

    pub fn sync_folder(&mut self, folder: &str) -> Result<(), Box<dyn std::error::Error>> {
        println!("Syncing folder {}...\n", folder);

        // Select the folder
        let mailbox = Mailbox::try_from(folder.to_string())?;
        let select_data = self.client.select(mailbox)?;

        println!(
            "Selected folder - EXISTS: {:?}, UIDVALIDITY: {:?}\n",
            select_data.exists, select_data.uid_validity
        );

        // Get last seen UID from cache
        let last_seen_uid = self.cache.get_last_seen_uid(folder)?;

        let last_seen_uid = match last_seen_uid {
            None => {
                println!("No cache detected. Starting full sync.");
                1
            },
            Some(last_seen_uid) => last_seen_uid
        };

        // Step 1: Fetch new messages
        let (_, total_new) = self.fetch_new_messages(last_seen_uid)?;

        // Step 2: Fetch flag updates
        let (updated_uids, total_updated)  = self.fetch_flag_updates(folder, last_seen_uid)?;

        // Step 3: Detect expunged messages
        let (_, total_expunged) = self.detect_expunged_messages(folder, last_seen_uid, &updated_uids)?;

        println!("\n=== Sync complete: New: {}, Updated: {}, Expunged: {} ===", total_new, total_updated, total_expunged);
        Ok(())
    }

    fn fetch_new_messages(
        &mut self,
        last_seen_uid: u32,
    ) -> Result<(HashSet<u32>, usize), Box<dyn std::error::Error>> {
        println!("Step 1: Fetching new messages since last sync (UID {})", last_seen_uid);

        let start_uid = last_seen_uid + 1;
        let sequence_set = SequenceSet::try_from(format!("{}:*", start_uid).as_str())?;
        let fetch_items = MacroOrMessageDataItemNames::Macro(
            io_imap::types::fetch::Macro::Fast,
        );

        let new_messages = self.client.fetch(sequence_set, fetch_items, true)?;
        println!("  Found {} new messages", new_messages.len());

        let mut new_uids = HashSet::new();
        let mut new_count = 0;

        for (uid, items) in &new_messages {
            new_uids.insert(uid.get());
            new_count += 1;
            println!("  New message: UID {}", uid);

            for item in items.as_ref() {
                if let MessageDataItem::Envelope(env) = item {
                    let subject = match &env.subject.0 {
                        Some(s) => String::from_utf8_lossy(s.as_ref()),
                        None => "".into(),
                    };
                    println!("    Subject: {}", subject);
                    // TODO insert into cache
                }
            }
        }

        println!();
        Ok((new_uids, new_count))
    }

    fn fetch_flag_updates(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
    ) -> Result<(HashSet<u32>, usize), Box<dyn std::error::Error>> {
        println!("Step 2: Fetching flag updates for existing messages (UIDs 1:{})", last_seen_uid);

        let sequence_set = SequenceSet::try_from(format!("1:{}", last_seen_uid).as_str())?;
        let fetch_items = MacroOrMessageDataItemNames::MessageDataItemNames(
            vec![io_imap::types::fetch::MessageDataItemName::Flags].try_into()?
        );

        let flag_updates = self.client.fetch(sequence_set, fetch_items, true)?;
        println!("  Checking flags for {} messages", flag_updates.len());

        let mut fetched_uids = HashSet::new();
        let mut updated_count = 0;

        for (uid, items) in &flag_updates {
            // Extract actual UID from response
            let mut actual_uid = uid.get();
            for item in items.as_ref() {
                if let MessageDataItem::Uid(uid) = item {
                    actual_uid = uid.get();
                    break;
                }
            }

            fetched_uids.insert(actual_uid);

            for item in items.as_ref() {
                if let MessageDataItem::Flags(flags) = item {
                    if let Some(cached_msg) = self.cache.get_message(actual_uid, folder)? {
                        let cached_flags = cached_msg.get_flags_as_list();
                        let server_flags: Vec<String> = flags
                            .iter()
                            .map(|flag| {
                                match flag {
                                    FlagFetch::Flag(f) => format!("{}", f),
                                    FlagFetch::Recent => "\\Recent".to_string(),
                                }
                            })
                            .collect();

                        if cached_flags != server_flags {
                            updated_count += 1;
                            println!(
                                "  Updating UID {} in cache (flags changed:)\n    {:?} -> {:?}",
                                actual_uid, cached_flags, server_flags
                            );
                            // TODO update in cache
                        }
                    }
                }
            }
        }

        println!("  Total flag changes detected: {}\n", updated_count);
        Ok((fetched_uids, updated_count))
    }

    fn detect_expunged_messages(
        &self,
        folder: &str,
        last_seen_uid: u32,
        fetched_uids: &HashSet<u32>,
    ) -> Result<(HashSet<u32>, usize), Box<dyn std::error::Error>> {
        println!("Step 3: Detecting expunged messages");

        // A message is expunged if:
        // 1. It exists in the cache
        // 2. Its UID <= last_seen_uid (it's not a new message we haven't synced yet)
        // 3. It's NOT in fetched_uids (server didn't return it)

        let mut expunged_uids = HashSet::new();
        let mut expunged_count = 0;

        for uid in 1..=last_seen_uid {
            if let Some(cached_msg) = self.cache.get_message(uid, folder)? {
                if !fetched_uids.contains(&uid) {
                    expunged_uids.insert(uid);
                    expunged_count += 1;
                    println!(
                        "  Expunged UID {} (deleted on server): {} - {}",
                        uid, cached_msg.from_addr, cached_msg.subject
                    );
                    // TODO delete from cache
                }
            }
        }

        println!("  Expunged messages: {}", expunged_count);

        Ok((expunged_uids, expunged_count))
    }
}
