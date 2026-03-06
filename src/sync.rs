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
                        .and_then(|d| {
                            let s = String::from_utf8_lossy(d.as_ref());
                            chrono::DateTime::parse_from_rfc2822(s.trim()).ok()
                        })
                        .map(|dt| dt.with_timezone(&chrono::Utc).format("%Y-%m-%d %H:%M:%S").to_string())
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

    pub fn sync_folder(
        &mut self,
        folder: &str,
        cache_days: u32,
        use_fallback: bool,
        page_size: usize,
        dry_run: bool,
    ) -> Result<(), Box<dyn std::error::Error>> {
        println!("=== Syncing folder: {} ===\n", folder);

        // Select the folder
        let mailbox = Mailbox::try_from(folder.to_string())?;
        let select_data = self.client.select_readonly(mailbox)?;

        let uidvalidity = select_data.uid_validity.map(|u| u.get()).unwrap_or(0);
        let highestmodseq = select_data.highest_mod_seq.map(|u| u.get()).unwrap_or(0);

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

        let (total_new, total_updated, total_expunged) = self.sync_messages(
            folder,
            last_seen_uid,
            use_condstore,
            cached_highestmodseq,
            highestmodseq,
            page_size,
            dry_run,
        )?;

        // Step 4: Fetch message bodies for recent messages
        let total_bodies = self.sync_message_bodies(folder, cache_days, page_size, dry_run)?;

        // Update cached folder state
        if !dry_run {
            self.cache.set_folder_state(folder, uidvalidity, highestmodseq)?;
        }

        println!("\n=== Sync complete: New: {}, Updated: {}, Expunged: {}, Bodies: {} ===", 
                 total_new, total_updated, total_expunged, total_bodies);
        Ok(())
    }

    fn sync_messages(
        &mut self,
        folder: &str,
        last_seen_uid: Option<u32>,
        use_condstore: bool,
        cached_highestmodseq: u64,
        current_highestmodseq: u64,
        page_size: usize,
        dry_run: bool,
    ) -> Result<(usize, usize, usize), Box<dyn std::error::Error>> {

        if use_condstore {
            println!("Using CONDSTORE-aware sync (cached MODSEQ: {}, current: {})\n", 
                cached_highestmodseq, current_highestmodseq);
        }

        let prefix = if dry_run {
            println!("[DRY RUN - not updating cache]");
            "[DRY RUN] "
        } else {
            ""
        };

        // Step 1: Fetch new messages
        let mut total_new = 0;
        // Need to borrow cache as immutable here since fetch_new_messages_page() uses &mut self
        let cache = self.cache;

        // Fetch messages and update cache by page
        for page_result in self.fetch_new_messages_page(folder, last_seen_uid.unwrap_or(1), page_size) {
            let new_messages_page = page_result?;

            let mut page_new = 0;
            let tx = cache.transaction()?;
            for msg in new_messages_page {
                if let None = cache.get_message(msg.uid, folder)? {
                    if !dry_run {
                        cache.insert_message(
                            msg.uid,
                            folder,
                            &msg.from_addr,
                            msg.from_name.as_deref(),
                            &msg.subject,
                            &msg.date,
                            &msg.flags,
                        )?;
                    }
                    page_new += 1;
                }
            }
            total_new += page_new;
            tx.commit()?;
            println!("  {}Added {} new messages to cache", prefix, page_new);
        }
        println!();
        println!("Fetched {} new messages\n", total_new);

        let last_seen_uid = match last_seen_uid {
            Some(uid) => uid,
            None => {
                println!("(Initial sync, skipping flag update and expunged check)");
                return Ok((total_new, 0, 0))
            },
        };

        // Step 2: Fetch flag changes to old messages
        let flag_updates = self.fetch_flag_updates(
            folder,
            last_seen_uid,
            use_condstore,
            cached_highestmodseq,
            current_highestmodseq)?;
        let total_updated = flag_updates.len();

        let mut total_updated_cache = 0;
        let tx = self.cache.transaction()?;
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
        tx.commit()?;
        println!("{}Updated {} messages in cache", prefix, total_updated_cache);
        println!();

        // Step 3: Detect expunged
        let server_uids = self.get_server_uids(1, last_seen_uid)?;
        let expunged_uids = self.detect_expunged_messages(folder, &server_uids)?;
        let total_expunged = expunged_uids.len();

        let mut total_expunged_cache = 0;
        let tx = cache.transaction()?;
        for expunged_uid in expunged_uids {
            if !dry_run {
                cache.delete_message(
                    expunged_uid,
                    folder,
                )?;
            }
            total_expunged_cache += 1;
        }
        tx.commit()?;
        println!("{}Deleted {} messages in cache", prefix, total_expunged_cache);
        println!();

        Ok((total_new, total_updated, total_expunged))
    }

    // Search for all UIDs greater than the last seen UID to get new messages
    // IMAP command: UID FETCH <lastseenuid+1>:* (ENVELOPE)
    // Returns a lazy iterator of fetched pages
    fn fetch_new_messages_page(
        &mut self,
        _folder: &str,
        last_seen_uid: u32,
        page_size: usize,
    ) -> Box<dyn Iterator<Item=Result<Vec<NewMessage>, Box<dyn std::error::Error>>> + '_> {
        println!("Step 1: Fetching new messages since last sync (UID {})", last_seen_uid);

        let start_uid = last_seen_uid + 1;
        let sequence_set = match SequenceSet::try_from(format!("{}:*", start_uid).as_str()) {
            Ok(s) => s,
            Err(e) => return Box::new(std::iter::once(Err(e.into()))),
        };

        // Get all new UIDs
        use io_imap::types::search::SearchKey;
        let criteria = match vec![SearchKey::Uid(sequence_set)].try_into() {
            Ok(c) => c,
            Err(e) => return Box::new(std::iter::once(Err(Box::new(e) as Box<dyn std::error::Error>))),
        };

        let new_uids = match self.client.search_uid(criteria) {
            Ok(uids) => uids,
            Err(e) => return Box::new(std::iter::once(Err(e))),
        };

        if new_uids.is_empty() {
            println!("  No new messages\n");
            return Box::new(std::iter::empty());
        }

        let uid_min = new_uids.iter().min().unwrap();
        let uid_max = new_uids.iter().max().unwrap();
        println!("  Found {} new messages (ranging from {} to {})", new_uids.len(), uid_min, uid_max);

        // Convert chunks to owned Vec<Vec<NonZeroU32>> so we don't borrow new_uids
        let uid_pages: Vec<Vec<_>> = new_uids
            .chunks(page_size)
            .map(|chunk| chunk.to_vec())
            .collect();

        let pages_iter = uid_pages.into_iter()
            .enumerate()
            .map(move |(page_num, chunk)| {
                println!("  Fetching page {} ({} messages)", page_num, chunk.len());

                let sequence_set = SequenceSet::try_from(
                    chunk.iter().map(|u| u.to_string()).collect::<Vec<_>>().join(",").as_str()
                )?;

                let fetch_items = MacroOrMessageDataItemNames::Macro(
                    io_imap::types::fetch::Macro::All,
                );

                let fetched = self.client.fetch(sequence_set, fetch_items)?;

                let mut new_messages_page = Vec::new();
                for (_seq, items) in fetched {
                    new_messages_page.push(NewMessage::new(items));
                }

                println!("  Page {}: Fetched {} new messages", page_num, new_messages_page.len());

                Ok(new_messages_page)
            });

        Box::new(pages_iter)
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
        _folder: &str,
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

        let fetched = self.client.fetch(sequence_set, fetch_items)?;
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

    fn sync_message_bodies(
        &mut self,
        folder: &str,
        cache_days: u32,
        page_size: usize,
        dry_run: bool,
    ) -> Result<usize, Box<dyn std::error::Error>> {
        println!("Step 4: Fetching message bodies for recent messages (last {} days)", cache_days);

        let uids_without_bodies = self.cache.get_messages_without_bodies(folder, cache_days)?;

        if uids_without_bodies.is_empty() {
            return Ok(0);
        }

        println!("  Fetching {} message bodies...", uids_without_bodies.len());

        let prefix = if dry_run { "[DRY RUN] " } else { "" };
        let mut total_fetched = 0;

        // Fetch bodies in pages
        for (page_num, chunk) in uids_without_bodies.chunks(page_size).enumerate() {
            println!("  Fetching page {} ({} messages)", page_num, chunk.len());

            let uid_strings: Vec<String> = chunk.iter().map(|u| u.to_string()).collect();
            let sequence_set = SequenceSet::try_from(uid_strings.join(",").as_str())?;

            // Fetch BODY.PEEK[TEXT] for the message bodies
            use io_imap::types::fetch::Section;
            let items = MacroOrMessageDataItemNames::MessageDataItemNames(vec![
                io_imap::types::fetch::MessageDataItemName::Uid,
                io_imap::types::fetch::MessageDataItemName::BodyExt {
                    section: Some(Section::Text(None)),
                    partial: None,
                    peek: true,
                },
            ].try_into()?);

            let responses = self.client.fetch(sequence_set, items)?;

            let tx = self.cache.transaction()?;
            for (_seq, response) in responses {
                if let Some((uid, body)) = Self::extract_body_from_response(response) {
                    if !dry_run {
                        self.cache.store_body(uid, folder, &body)?;
                    }
                    total_fetched += 1;
                }
            }
            tx.commit()?;
            println!("  Page {}: Fetched {} new message bodies", page_num, chunk.len());
            println!("  {}Added {} new message bodies to cache", prefix, chunk.len());
        }

        println!("Fetched {} total message bodies\n", total_fetched);
        Ok(total_fetched)
    }

    fn extract_body_from_response(items: Vec1<MessageDataItem>) -> Option<(u32, String)> {
        let mut uid = None;
        let mut body = None;

        for item in items.as_ref() {
            match item {
                MessageDataItem::Uid(_uid) => {
                    uid = Some(_uid.get());
                }
                MessageDataItem::BodyExt { data, .. } => {
                    // data is NString, which has .0 field that is Option<IString>
                    if let Some(literal) = &data.0 {
                        body = Some(String::from_utf8_lossy(literal.as_ref()).to_string());
                    }
                }
                _ => {}
            }
        }

        uid.and_then(|u| body.map(|b| (u, b)))
    }
}
