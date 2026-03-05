use log::{debug};
use std::{collections::HashMap, net::TcpStream, num::{NonZeroU32, NonZeroU64}, sync::Arc};

use io_imap::{
    context::ImapContext,
    coroutines::{
        greeting_with_capability::{GetImapGreetingWithCapability, GetImapGreetingWithCapabilityResult},
        login::{ImapLogin, ImapLoginParams, ImapLoginResult},
        select::{ImapSelect, ImapSelectResult, SelectData},
        fetch::{ImapFetch, ImapFetchResult},
        search::{ImapSearch, ImapSearchResult},
    },
    types::{
        command::FetchModifier,
        core::Vec1,
        fetch::{MacroOrMessageDataItemNames, MessageDataItem},
        mailbox::Mailbox,
        search::SearchKey,
        sequence::SequenceSet,
    },
};
use io_stream::runtimes::std::handle;
use rustls::{ClientConfig, ClientConnection, StreamOwned};
use rustls_platform_verifier::ConfigVerifierExt;
use secrecy::SecretString;

use crate::config::Account;

pub struct ImapClient {
    context: ImapContext,
    stream: StreamOwned<ClientConnection, TcpStream>,
    highestmodseq: u64,
}

impl ImapClient {
    pub fn connect(account: &Account) -> Result<Self, Box<dyn std::error::Error>> {
        let mut context = ImapContext::new();

        // Connect via TLS
        let stream = TcpStream::connect((&account.imap_host as &str, account.imap_port))?;
        let server_name = account.imap_host.clone().try_into()?;
        let config = ClientConfig::with_platform_verifier()?;
        let conn = ClientConnection::new(Arc::new(config), server_name)?;
        let mut stream = StreamOwned::new(conn, stream);

        // Get greeting and capability
        let mut arg = None;
        let mut coroutine = GetImapGreetingWithCapability::new(context);

        loop {
            match coroutine.resume(arg.take()) {
                GetImapGreetingWithCapabilityResult::Ok { context: ctx, .. } => {
                    context = ctx;
                    break;
                }
                GetImapGreetingWithCapabilityResult::Io { io } => {
                    arg = Some(handle(&mut stream, io)?)
                }
                GetImapGreetingWithCapabilityResult::Err { err, .. } => {
                    return Err(format!("Greeting error: {}", err).into())
                }
            }
        }

        // Login
        let password = account.password.as_ref()
            .ok_or("No password configured")?;

        let mut arg = None;
        let params = ImapLoginParams::new(&account.email, SecretString::from(password.clone()))?;
        let mut coroutine = ImapLogin::new(context, params);

        loop {
            match coroutine.resume(arg.take()) {
                ImapLoginResult::Ok { context: ctx } => {
                    context = ctx;
                    break;
                }
                ImapLoginResult::Io { io } => arg = Some(handle(&mut stream, io)?),
                ImapLoginResult::Err { err, .. } => {
                    return Err(format!("Login error: {}", err).into())
                }
            }
        }

        Ok(Self { context, stream, highestmodseq: 0 })
    }

    pub fn print_status_debug(&self) {
        debug!("Connected and authenticated!");
        // debug!("Capabilities: {:#?}", self.context.capability);
        debug!("Authenticated: {}", self.context.authenticated);
    }

    pub fn has_capability(&self, cap: &str) -> bool {
        self.context.capability.iter().any(|c| {
            let cap_str = format!("{:?}", c);
            cap_str.contains(cap)
        })
    }

    pub fn get_highestmodseq(&self) -> u64 {
        self.highestmodseq
    }

    pub fn select(
        &mut self,
        mailbox: Mailbox<'static>,
    ) -> Result<SelectData, Box<dyn std::error::Error>> {
        use io_imap::codec::CommandCodec;
        use io_imap::coroutines::send::{SendImapCommand, SendImapCommandResult};
        use io_imap::types::command::{Command, CommandBody};
        use io_imap::types::response::{Code, Data, StatusBody, StatusKind};

        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());

        // TODO io-imap ImapSelect::new doesn't have CONDSTORE support yet
        // use imap_codec/imap_types directly for now
        /*
        let mut coroutine = ImapSelect::new(context, mailbox);
        loop {
            match coroutine.resume(arg.take()) {
                ImapSelectResult::Ok { context, data } => {
                    self.context = context;
                    // TODO: Extract HIGHESTMODSEQ from response codes
                    self.highestmodseq = 10295646; // TODO
                    return Ok(data);
                },

                ImapSelectResult::Io { io } => arg = Some(handle(&mut self.stream, io)?),
                ImapSelectResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Select error: {}", err).into())
                },
            }
        }
        */

        let body = CommandBody::Select {
            mailbox: mailbox.clone(),
            parameters: Default::default(),
        };

        let mut ctx = context;
        let command = Command::new(ctx.generate_tag(), body)?;
        let mut coroutine = SendImapCommand::new(ctx, CommandCodec::new(), command);

        loop {
            let (context, data, untagged, tagged, bye) = match coroutine.resume(arg.take()) {
                SendImapCommandResult::Ok { context, data, untagged, tagged, bye, .. } => {
                    (context, data, untagged, tagged, bye)
                }
                SendImapCommandResult::Io { io } => {
                    arg = Some(handle(&mut self.stream, io)?);
                    continue;
                }
                SendImapCommandResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Select error: {}", err).into());
                }
            };

            if let Some(bye) = bye {
                self.context = context;
                return Err(format!("Server sent BYE: {}", bye.text).into());
            }

            let Some(tagged) = tagged else {
                self.context = context;
                return Err("No tagged response".into());
            };

            // Parse response data
            let mut select_data = SelectData::default();

            for d in data {
                match d {
                    Data::Flags(flags) => select_data.flags = Some(flags),
                    Data::Exists(count) => select_data.exists = Some(count),
                    Data::Recent(count) => select_data.recent = Some(count),
                    _ => {}
                }
            }

            // Extract HIGHESTMODSEQ and other codes from untagged responses
            for StatusBody { kind, code, .. } in untagged {
                if let StatusKind::Ok = kind {
                    match code {
                        Some(Code::Unseen(seq)) => select_data.unseen = Some(seq),
                        Some(Code::PermanentFlags(flags)) => select_data.permanent_flags = Some(flags),
                        Some(Code::UidNext(uid)) => select_data.uid_next = Some(uid),
                        Some(Code::UidValidity(uid)) => select_data.uid_validity = Some(uid),
                        Some(Code::HighestModSeq(modseq)) => {
                            self.highestmodseq = modseq.get();
                        }
                        _ => {}
                    }
                }
            }

            self.context = context;

            return match tagged.body.kind {
                StatusKind::Ok => Ok(select_data),
                StatusKind::No => Err(format!("SELECT NO: {}", tagged.body.text).into()),
                StatusKind::Bad => Err(format!("SELECT BAD: {}", tagged.body.text).into()),
            };
        }
    }

    pub fn fetch(
        &mut self,
        sequence_set: SequenceSet,
        items: MacroOrMessageDataItemNames<'static>,
        uid: bool,
    ) -> Result<HashMap<NonZeroU32, Vec1<MessageDataItem<'static>>>, Box<dyn std::error::Error>>
    {
        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());
        let mut coroutine = ImapFetch::new(context, sequence_set, items, uid);

        loop {
            match coroutine.resume(arg.take()) {
                ImapFetchResult::Ok { context, data } => {
                    self.context = context;
                    return Ok(data);
                }
                ImapFetchResult::Io { io } => arg = Some(handle(&mut self.stream, io)?),
                ImapFetchResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Fetch error: {}", err).into())
                }
            }
        }
    }

    pub fn fetch_with_changedsince(
        &mut self,
        sequence_set: SequenceSet,
        items: MacroOrMessageDataItemNames<'static>,
        modseq: u64,
    ) -> Result<HashMap<NonZeroU32, Vec1<MessageDataItem<'static>>>, Box<dyn std::error::Error>>
    {
        // TODO io-imap ImapFetch::new doesn't have CONDSTORE support yet
        // use imap_codec/imap_types directly for now
        use io_imap::codec::CommandCodec;
        use io_imap::coroutines::send::{SendImapCommand, SendImapCommandResult};
        use io_imap::types::command::{Command, CommandBody};

        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());

        // Build FETCH command with CHANGEDSINCE modifier
        let modseq_nz = NonZeroU64::new(modseq).ok_or("Invalid MODSEQ")?;
        let modifiers = vec![FetchModifier::ChangedSince(modseq_nz)].try_into()?;

        let body = CommandBody::Fetch {
            sequence_set,
            macro_or_item_names: items,
            modifiers,
            uid: true,
        };

        let mut ctx = context;
        let command = Command::new(ctx.generate_tag(), body)?;
        let mut coroutine = SendImapCommand::new(ctx, CommandCodec::new(), command);

        loop {
            let (context, data, _untagged, tagged, bye) = match coroutine.resume(arg.take()) {
                SendImapCommandResult::Ok { context, data, untagged, tagged, bye, .. } => {
                    (context, data, untagged, tagged, bye)
                }
                SendImapCommandResult::Io { io } => {
                    arg = Some(handle(&mut self.stream, io)?);
                    continue;
                }
                SendImapCommandResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Fetch error: {}", err).into());
                }
            };

            if let Some(bye) = bye {
                self.context = context;
                return Err(format!("Server sent BYE: {}", bye.text).into());
            }

            let Some(tagged) = tagged else {
                self.context = context;
                return Err("No tagged response".into());
            };

            use io_imap::types::response::{Data, StatusKind};
            let mut output: HashMap<NonZeroU32, Vec<MessageDataItem<'static>>> = HashMap::new();

            for d in data {
                if let Data::Fetch { seq, items } = d {
                    output.entry(seq).or_default().extend(items.into_iter());
                }
            }

            self.context = context;

            return match tagged.body.kind {
                StatusKind::Ok => Ok(output
                    .into_iter()
                    .map(|(key, val)| (key, Vec1::unvalidated(val)))
                    .collect()),
                StatusKind::No => Err(format!("FETCH NO: {}", tagged.body.text).into()),
                StatusKind::Bad => Err(format!("FETCH BAD: {}", tagged.body.text).into()),
            };
        }
    }

    pub fn search_uid(&mut self, criteria: Vec1<SearchKey<'static>>) -> Result<Vec<NonZeroU32>, Box<dyn std::error::Error>> {
        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());
        let mut coroutine = ImapSearch::new(context, criteria, true);

        loop {
            match coroutine.resume(arg.take()) {
                ImapSearchResult::Ok { context, ids } => {
                    self.context = context;
                    return Ok(ids);
                }
                ImapSearchResult::Io { io } => arg = Some(handle(&mut self.stream, io)?),
                ImapSearchResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Search error: {}", err).into());
                }
            }
        }
    }
}
