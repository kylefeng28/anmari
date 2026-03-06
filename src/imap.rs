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

        Ok(Self { context, stream })
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

    pub fn select(
        &mut self,
        mailbox: Mailbox<'static>,
    ) -> Result<SelectData, Box<dyn std::error::Error>> {
        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());
        let mut coroutine = ImapSelect::new(context, mailbox);

        loop {
            match coroutine.resume(arg.take()) {
                ImapSelectResult::Ok { context, data } => {
                    self.context = context;
                    return Ok(data);
                },

                ImapSelectResult::Io { io } => arg = Some(handle(&mut self.stream, io)?),
                ImapSelectResult::Err { context, err } => {
                    self.context = context;
                    return Err(format!("Select error: {}", err).into())
                },
            }
        }
    }

    fn _fetch(
        &mut self,
        sequence_set: SequenceSet,
        items: MacroOrMessageDataItemNames<'static>,
        modifiers: Vec<FetchModifier>,
    ) -> Result<HashMap<NonZeroU32, Vec1<MessageDataItem<'static>>>, Box<dyn std::error::Error>>
    {
        let mut arg = None;
        let context = std::mem::replace(&mut self.context, ImapContext::new());
        let mut coroutine = ImapFetch::new(context, sequence_set, items, modifiers, true);

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

    pub fn fetch(
        &mut self,
        sequence_set: SequenceSet,
        items: MacroOrMessageDataItemNames<'static>,
    ) -> Result<HashMap<NonZeroU32, Vec1<MessageDataItem<'static>>>, Box<dyn std::error::Error>>
    {
        self._fetch(sequence_set, items, Vec::new())
    }

    pub fn fetch_with_changedsince(
        &mut self,
        sequence_set: SequenceSet,
        items: MacroOrMessageDataItemNames<'static>,
        modseq: u64,
    ) -> Result<HashMap<NonZeroU32, Vec1<MessageDataItem<'static>>>, Box<dyn std::error::Error>>
    {
        // Build FETCH command with CHANGEDSINCE modifier
        let modseq_nz = NonZeroU64::new(modseq).ok_or("Invalid MODSEQ")?;
        let modifiers = vec![FetchModifier::ChangedSince(modseq_nz)].try_into()?;

        self._fetch(sequence_set, items, modifiers)
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
