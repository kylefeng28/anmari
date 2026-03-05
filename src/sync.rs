use std::collections::HashSet;

use std::num::NonZeroU32;
use io_imap::{
    types::{
        core::Vec1,
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
        println!("=== Syncing folder: {} ===\n", folder);

        // Select the folder
        let mailbox = Mailbox::try_from(folder.to_string())?;
        let select_data = self.client.select(mailbox)?;

        let uidvalidity = select_data.uid_validity.map(|u| u.get()).unwrap_or(0);
        let highestmodseq = self.client.get_highestmodseq();

        println!(
            "Selected folder - EXISTS: {:?}, UIDVALIDITY: {}, HIGHESTMODSEQ: {}\n",
            select_data.exists, uidvalidity, highestmodseq
        );

        // Get cached folder state
        let cached_state = self.cache.get_folder_state(folder)?;
        let cached_uidvalidity = cached_state.as_ref().map(|s| s.uidvalidity).unwrap_or(0);

        // Check if UIDVALIDITY changed (requires full resync)
        if cached_uidvalidity != 0 && cached_uidvalidity != uidvalidity {
            println!("UIDVALIDITY changed! Need full resync (not implemented yet)");
            return Ok(());
        }

        // Get last seen UID from cache
        let last_seen_uid = self.cache.get_last_seen_uid(folder)?.unwrap_or(0);

        // Determine if we should use CONDSTORE
        let cached_highestmodseq = cached_state.as_ref().map(|s| s.highestmodseq).unwrap_or(0);
        // let use_condstore = highestmodseq > 0 && cached_highestmodseq > 0 && last_seen_uid > 0;
        let use_condstore = false;

        let (total_new, total_updated, total_expunged) = self.sync(folder, last_seen_uid, use_condstore, cached_highestmodseq, highestmodseq)?;

        // Update folder state
        // TODO update uidvalidity, highestmodseq

        println!("\n=== Sync complete: New: {}, Updated: {}, Expunged: {} ===", 
                 total_new, total_updated, total_expunged);
        Ok(())
    }

    fn sync(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
        use_condstore: bool,
        cached_highestmodseq: u64,
        current_highestmodseq: u64,
    ) -> Result<(usize, usize, usize), Box<dyn std::error::Error>> {

        if use_condstore {
            println!("Using CONDSTORE-aware sync (cached MODSEQ: {}, current: {})\n", 
                cached_highestmodseq, current_highestmodseq);
        }

        // Step 1: Fetch new messages
        let (_new_message_uids, total_new) = self.fetch_new_messages(last_seen_uid)?;

        // Step 2: Fetch flag changes to old messages
        let (_updated_message_uids, total_updated) = self.fetch_flag_updates(
            folder,
            last_seen_uid,
            use_condstore,
            cached_highestmodseq,
            current_highestmodseq)?;

        // Step 3: Detect expunged
        let server_uids = self.get_server_uids()?;
        let (_, total_expunged) = self.detect_expunged_messages(folder, &server_uids)?;

        Ok((total_new, total_updated, total_expunged))
    }

    fn get_uid(items: Vec1<MessageDataItem>) -> Option<NonZeroU32> {
        for item in items {
            if let MessageDataItem::Uid(uid) = item {
                return Some(uid);
            }
        }
        None
    }

    fn fetch_new_messages(
        &mut self,
        last_seen_uid: u32,
    ) -> Result<(HashSet<NonZeroU32>, usize), Box<dyn std::error::Error>> {
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

        for (seq, items) in new_messages {
            let uid = Self::get_uid(items.clone()).unwrap();
            new_uids.insert(uid);
            new_count += 1;
            println!("  New message {}: UID {} ({:?})", seq, uid, items);

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
        use_condstore: bool,
        cached_highestmodseq: u64,
        current_highestmodseq: u64,
    ) -> Result<(HashSet<NonZeroU32>, usize), Box<dyn std::error::Error>> {
        // Step 2: Check flag changes to old messages
        // With CONDSTORE: Use CHANGEDSINCE to only fetch changed messages
        // Without CONDSTORE: Fetch all old messages before last seen UID, then check if flags changed
        if use_condstore {
            if current_highestmodseq == cached_highestmodseq {
                println!("Step 2: HIGHESTMODSEQ unchanged ({}), skipping flag check", current_highestmodseq);
                println!("  (All cached messages are still valid)\n");
                Ok((HashSet::new(), 0))
            } else {
                self.fetch_flag_updates_changedsince(folder, cached_highestmodseq)
            }
        } else {
            self.fetch_flag_updates_fallback(folder, last_seen_uid)
        }
    }

    /// RFC 4549 Section 6.1: Use CONDSTORE / HIGHESTMODSEQ for efficient sync
    fn fetch_flag_updates_changedsince(
        &mut self,
        folder: &str,
        cached_highestmodseq: u64,
    ) -> Result<(HashSet<NonZeroU32>, usize), Box<dyn std::error::Error>> {
        println!("Step 2: Using CONDSTORE CHANGEDSINCE to fetch changes since MODSEQ {}", cached_highestmodseq);

        let sequence_set = SequenceSet::try_from("1:*")?;
        let fetch_items = MacroOrMessageDataItemNames::MessageDataItemNames(
            vec![io_imap::types::fetch::MessageDataItemName::Flags].try_into()?
        );

        let flag_updates = self.client.fetch_with_changedsince(sequence_set, fetch_items, cached_highestmodseq)?;
        println!("  Found {} messages with flag changes", flag_updates.len());

        let mut updated_uids = HashSet::new();
        let mut updated_count = 0;

        for (_seq, items) in flag_updates {
            let uid = Self::get_uid(items).unwrap();
            updated_uids.insert(uid);
            updated_count += 1;
        }

        // TODO update cache

        Ok((updated_uids, updated_count))
    }

    /// No CONDSTORE: Fetch all old messages
    fn fetch_flag_updates_fallback(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
    ) -> Result<(HashSet<NonZeroU32>, usize), Box<dyn std::error::Error>> {
        println!("Step 2: Fetching flags for existing messages (UIDs 1:{})", last_seen_uid);

        let sequence_set = SequenceSet::try_from(format!("1:{}", last_seen_uid).as_str())?;
        let fetch_items = MacroOrMessageDataItemNames::MessageDataItemNames(
            vec![io_imap::types::fetch::MessageDataItemName::Flags].try_into()?
        );

        let flag_updates = self.client.fetch(sequence_set, fetch_items, true)?;
        println!("  Checking flags for {} messages", flag_updates.len());

        let mut updated_uids = HashSet::new();
        let mut updated_count = 0;

        for (_seq, items) in flag_updates {
            let uid = Self::get_uid(items.clone()).unwrap();
            for item in items.as_ref() {
                if let MessageDataItem::Flags(flags) = item {
                    if let Some(cached_msg) = self.cache.get_message(uid.get(), folder)? {
                        let cached_flags = cached_msg.get_flags_as_list();
                        let server_flags: Vec<String> = flags
                            .iter()
                            .map(|flag| match flag {
                                FlagFetch::Flag(f) => format!("{}", f),
                                FlagFetch::Recent => "\\Recent".to_string(),
                            })
                            .collect();

                        if cached_flags != server_flags {
                            updated_count += 1;
                            println!(
                                "  UID {} flags changed: {:?} -> {:?}",
                                uid, cached_flags, server_flags
                            );
                            // TODO update cache
                        }
                    }
                }
            }
            updated_uids.insert(uid);
            updated_count += 1;
        }

        println!("  Total flag changes detected: {}\n", updated_count);

        Ok((updated_uids, updated_count))
    }

    fn get_server_uids(&mut self) -> Result<HashSet<NonZeroU32>, Box<dyn std::error::Error>> {
        let criteria = vec![io_imap::types::search::SearchKey::All].try_into()?;
        let uids = self.client.search_uid(criteria)?;
        Ok(HashSet::from_iter(uids.into_iter()))
    }

    fn detect_expunged_messages(
        &self,
        folder: &str,
        server_uids: &HashSet<NonZeroU32>,
    ) -> Result<(HashSet<NonZeroU32>, usize), Box<dyn std::error::Error>> {
        println!("Step 3: Detecting expunged messages");

        let mut expunged_uids = HashSet::new();
        let mut expunged_count = 0;

        let cached_uids = self.cache.get_all_uids(folder)?;
        let cached_uids = HashSet::from_iter(cached_uids.into_iter());
        let diff: Vec<_> = cached_uids.difference(&server_uids).collect();

        for &uid in diff {
            expunged_uids.insert(uid);
            expunged_count += 1;
            println!("  Expunged UID {} (deleted on server)", uid);
            // TODO delete from cache
        }

        println!("  Expunged messages: {}", expunged_count);

        Ok((expunged_uids, expunged_count))
    }
}
