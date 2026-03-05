use log::{debug};
use std::{net::TcpStream, sync::Arc};

use io_imap::{
    context::ImapContext,
    coroutines::{greeting_with_capability::*, login::*},
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
        debug!("Capabilities: {:#?}", self.context.capability);
        debug!("Authenticated: {}", self.context.authenticated);
    }
}
