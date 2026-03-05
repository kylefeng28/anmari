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

#[derive(Debug, Clone)]
struct NewMessage {
    uid: u32,
    from_addr: String,
    from_name: Option<String>,
    subject: String,
    date: String,
    flags: Vec<String>,
}

impl NewMessage {
    pub fn new(items: Vec1<MessageDataItem>) -> NewMessage {
        // Decode MIME encoded words (RFC 2047)
        use rfc2047_decoder::{Decoder, RecoverStrategy};
        let decoder = Decoder::new()
            .too_long_encoded_word_strategy(RecoverStrategy::Skip);

        let mut uid = None;
        let mut envelope_data = None;
        let mut flags = Vec::new();

        for item in items.as_ref() {
            match item {
                MessageDataItem::Uid(_uid) => {
                    uid = Some(*_uid);
                }
                MessageDataItem::Envelope(env) => {
                    let subject = match &env.subject.0 {
                        Some(s) => decoder.clone().decode(s).unwrap_or_else(|_| "(decode error)".into()),
                        None => "(no subject)".into(),
                    };

                    let (from_addr, from_name) = if !env.from.is_empty() {
                        let first = &env.from[0];
                        let mailbox = first.mailbox.0.as_ref().map(|b| String::from_utf8_lossy(b.as_ref())).unwrap_or_default();
                        let host = first.host.0.as_ref().map(|h| String::from_utf8_lossy(h.as_ref())).unwrap_or_default();
                        let addr = format!("{}@{}", mailbox, host);
                        let name = first.name.0.as_ref()
                            .and_then(|n| decoder.clone().decode(n.as_ref()).ok());
                        (addr, name)
                    } else {
                        ("unknown".to_string(), None)
                    };

                    let date = env.date.0.as_ref()
                        .map(|d| String::from_utf8_lossy(d.as_ref()).to_string())
                        .unwrap_or_else(|| "1970-01-01 00:00:00".to_string());

                    envelope_data = Some((from_addr, from_name, subject, date));
                    println!("    Subject: {}", envelope_data.as_ref().unwrap().2);
                }
                MessageDataItem::Flags(_flags) => {
                    flags = get_flags(_flags);
                }
                _ => {}
            }
        }

        let (from_addr, from_name, subject, date) = envelope_data.unwrap();
        NewMessage {
            uid: uid.unwrap().get(),
            from_addr,
            from_name,
            subject,
            date,
            flags,
        }
    }
}

#[derive(Debug, Clone)]
struct FlagUpdate {
    uid: u32,
    flags: Vec<String>,
}

impl FlagUpdate {
    pub fn new(items: Vec1<MessageDataItem>) -> FlagUpdate {
        let mut uid = None;
        let mut flags = Vec::new();

        for item in items.as_ref() {
            if let MessageDataItem::Uid(_uid) = item {
                uid = Some(_uid)
            }
            if let MessageDataItem::Flags(_flags) = item {
                flags = get_flags(_flags);
            }
        }

        FlagUpdate {
            uid: uid.unwrap().get(),
            flags,
        }
    }
}

fn get_flags(flags: &Vec<FlagFetch>) -> Vec<String> {
    flags.iter().map(|f| match f {
        FlagFetch::Flag(flag) => format!("{}", flag),
        FlagFetch::Recent => "\\Recent".to_string(),
    }).collect()
}

pub struct Syncer<'a> {
    client: &'a mut ImapClient,
    cache: &'a EmailCache,
}

impl<'a> Syncer<'a> {
    pub fn new(client: &'a mut ImapClient, cache: &'a EmailCache) -> Self {
        Self { client, cache }
    }

    pub fn sync_folder(&mut self, folder: &str, use_fallback: bool, dry_run: bool) -> Result<(), Box<dyn std::error::Error>> {
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
        let last_seen_uid = self.cache.get_last_seen_uid(folder)?;

        // Determine if we should use CONDSTORE
        let cached_highestmodseq = cached_state.as_ref().map(|s| s.highestmodseq).unwrap_or(0);
        let mut use_condstore = highestmodseq > 0 && cached_highestmodseq > 0 && last_seen_uid.map(|x| x > 0).unwrap_or(false);

        if use_condstore && use_fallback {
            println!("Using fallback due to --fallback flag even though CONDSTORE support is detected\n");
            use_condstore = false;
        }

        let (total_new, total_updated, total_expunged) = self.sync(folder, last_seen_uid, use_condstore, cached_highestmodseq, highestmodseq, dry_run)?;

        // Update folder state
        // TODO update uidvalidity, highestmodseq

        println!("\n=== Sync complete: New: {}, Updated: {}, Expunged: {} ===", 
                 total_new, total_updated, total_expunged);
        Ok(())
    }

    fn sync(
        &mut self,
        folder: &str,
        last_seen_uid: Option<u32>,
        use_condstore: bool,
        cached_highestmodseq: u64,
        current_highestmodseq: u64,
        dry_run: bool,
    ) -> Result<(usize, usize, usize), Box<dyn std::error::Error>> {

        if use_condstore {
            println!("Using CONDSTORE-aware sync (cached MODSEQ: {}, current: {})\n", 
                cached_highestmodseq, current_highestmodseq);
        }

        let last_seen_uid = last_seen_uid.unwrap_or(1);

        // Step 1: Fetch new messages
        let new_messages = self.fetch_new_messages(folder, last_seen_uid)?;
        let total_new = new_messages.len();

        // Step 2: Fetch flag changes to old messages
        let flag_updates = self.fetch_flag_updates(
            folder,
            last_seen_uid,
            use_condstore,
            cached_highestmodseq,
            current_highestmodseq)?;
        let total_updated = flag_updates.len();

        // Step 3: Detect expunged
        let server_uids = self.get_server_uids(1, last_seen_uid)?;
        let expunged_uids = self.detect_expunged_messages(folder, &server_uids)?;
        let total_expunged = expunged_uids.len();

        println!();

        // Update cache
        let prefix = if dry_run {
            println!("[DRY RUN - not updating cache]");
            "[DRY RUN] "
        } else {
            ""
        };

        let mut total_new_cache = 0;
        for msg in new_messages {
            if !dry_run {
                self.cache.insert_message(
                    msg.uid,
                    folder,
                    &msg.from_addr,
                    msg.from_name.as_deref(),
                    &msg.subject,
                    &msg.date,
                    &msg.flags,
                )?;
            }
            total_new_cache += 1;
        }
        println!("{}Added {} messages to cache", prefix, total_new_cache);

        let mut total_updated_cache = 0;
        for flag_update in flag_updates {
            if !dry_run {
                self.cache.update_message_flags(
                    flag_update.uid,
                    folder,
                    &flag_update.flags,
                )?;
            }
            total_updated_cache += 1;
        }
        println!("{}Updated {} messages in cache", prefix, total_updated_cache);

        let mut total_expunged_cache = 0;
        for expunged_uid in expunged_uids {
            if !dry_run {
                self.cache.delete_message(
                    expunged_uid,
                    folder,
                )?;
            }
            total_expunged_cache += 1;
        }
        println!("{}Deleted {} messages in cache", prefix, total_expunged_cache);

        println!();

        Ok((total_new, total_updated, total_expunged))
    }

    fn fetch_new_messages(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
    ) -> Result<Vec<NewMessage>, Box<dyn std::error::Error>> {
        println!("Step 1: Fetching new messages since last sync (UID {})", last_seen_uid);

        let start_uid = last_seen_uid + 1;
        let sequence_set = SequenceSet::try_from(format!("{}:*", start_uid).as_str())?;

        let mut new_messages = Vec::new();

        let fetch_items = MacroOrMessageDataItemNames::Macro(
            io_imap::types::fetch::Macro::All,
        );

        let fetched = self.client.fetch(sequence_set, fetch_items, true)?;

        for (_seq, items) in fetched {
            new_messages.push(NewMessage::new(items));
        }

        println!("  Fetched {} new messages\n", new_messages.len());
        Ok(new_messages)
    }

    fn fetch_flag_updates(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
        use_condstore: bool,
        cached_highestmodseq: u64,
        current_highestmodseq: u64,
    ) -> Result<Vec<FlagUpdate>, Box<dyn std::error::Error>> {
        if use_condstore {
            if current_highestmodseq == cached_highestmodseq {
                println!("Step 2: HIGHESTMODSEQ unchanged ({}), skipping flag check", current_highestmodseq);
                println!("  (All cached messages are still valid)\n");
                return Ok(Vec::new());
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
    ) -> Result<Vec<FlagUpdate>, Box<dyn std::error::Error>> {
        println!("Step 2: Using CONDSTORE CHANGEDSINCE to fetch changes since MODSEQ {}", cached_highestmodseq);

        let sequence_set = SequenceSet::try_from("1:*")?;
        let fetch_items = MacroOrMessageDataItemNames::MessageDataItemNames(
            vec![io_imap::types::fetch::MessageDataItemName::Flags].try_into()?
        );

        let fetched = self.client.fetch_with_changedsince(sequence_set, fetch_items, cached_highestmodseq)?;
        println!("  Found {} messages with flag changes", fetched.len());

        let mut updates = Vec::new();

        for (_seq, items) in fetched {
            updates.push(FlagUpdate::new(items));
        }

        println!("  Updated {} messages in cache\n", updates.len());
        Ok(updates)
    }

    /// No CONDSTORE: Fetch all old messages
    fn fetch_flag_updates_fallback(
        &mut self,
        folder: &str,
        last_seen_uid: u32,
    ) -> Result<Vec<FlagUpdate>, Box<dyn std::error::Error>> {
        println!("Step 2: Fetching flags for existing messages (UIDs 1:{})", last_seen_uid);

        if last_seen_uid == 0 {
            println!("  No existing messages to check\n");
            return Ok(Vec::new());
        }

        let sequence_set = SequenceSet::try_from(format!("1:{}", last_seen_uid).as_str())?;
        let fetch_items = MacroOrMessageDataItemNames::MessageDataItemNames(
            vec![io_imap::types::fetch::MessageDataItemName::Flags].try_into()?
        );

        let fetched = self.client.fetch(sequence_set, fetch_items, true)?;
        println!("  Checking flags for {} messages", fetched.len());

        let mut updates = Vec::new();

        for (_seq, items) in fetched {
            let flag_update = FlagUpdate::new(items);
            let FlagUpdate { uid, flags: server_flags } = flag_update.clone();

            if let Some(cached_msg) = self.cache.get_message(uid, folder)? {
                let cached_flags = cached_msg.get_flags_as_list();
                if cached_flags != server_flags {
                    println!(
                        "  UID {} flags changed: {:?} -> {:?}",
                        uid, cached_flags, server_flags
                    );

                    updates.push(flag_update);
                }
            }
        }

        Ok(updates)
    }

    fn get_server_uids(&mut self, start_uid: u32, end_uid: u32) -> Result<HashSet<NonZeroU32>, Box<dyn std::error::Error>> {
        use io_imap::types::search::SearchKey;
        // let criteria = vec![SearchKey::All].try_into()?;
        let sequence_set = SequenceSet::try_from(format!("{}:{}", start_uid, end_uid).as_str())?;
        let criteria = vec![SearchKey::SequenceSet(sequence_set)].try_into()?;
        let uids = self.client.search_uid(criteria)?;
        Ok(HashSet::from_iter(uids.into_iter()))
    }

    fn detect_expunged_messages(
        &self,
        folder: &str,
        server_uids: &HashSet<NonZeroU32>,
    ) -> Result<Vec<u32>, Box<dyn std::error::Error>> {
        println!("Step 3: Detecting expunged messages");

        let cached_uids = self.cache.get_all_uids(folder)?;
        let cached_uids_set: HashSet<NonZeroU32> = cached_uids.into_iter().collect();

        let expunged: Vec<u32> = cached_uids_set
            .difference(server_uids)
            .map(|uid| uid.get())
            .collect();

        for uid in &expunged {
            println!("  Expunged UID {} (deleted on server)", uid);
        }

        Ok(expunged)
    }
}
