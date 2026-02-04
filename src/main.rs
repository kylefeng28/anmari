use anmari::{AccountConfig, CacheConfig, CachedMessage, Config, EmailCache};
use anyhow::{Context, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Parser, Subcommand};
use email::{
    account::config::{passwd::PasswordConfig, AccountConfig as EmailAccountConfig},
    backend::{Backend, BackendBuilder},
    envelope::{
        Address,
        list::{ListEnvelopes, ListEnvelopesOptions}, Envelope
    },
    imap::{
        config::{ImapAuthConfig, ImapConfig},
        ImapContext, ImapContextBuilder,
    },
    search_query::{filter::SearchEmailsFilterQuery, SearchEmailsQuery},
    tls::Encryption,
};
use secret::Secret;
use std::sync::Arc;

fn init_cache(account: usize, cache_days: u32) -> Result<EmailCache> {
    let cache_config = CacheConfig {
        db_path: format!("anmari_{}.db", account),
        cache_days,
    };
    EmailCache::new(cache_config)
}

async fn init_imap(account_config: &AccountConfig) -> Result<Backend<ImapContext>> {
    let auth = if let Some(ref password) = account_config.password {
        ImapAuthConfig::Password(PasswordConfig(Secret::new_raw(password.clone())))
    } else {
        anyhow::bail!("No password configured for account");
    };

    let email_config = Arc::new(EmailAccountConfig {
        email: account_config.email.clone(),
        ..Default::default()
    });

    let imap_config = Arc::new(ImapConfig {
        host: account_config.imap_host.clone(),
        port: account_config.imap_port,
        encryption: Some(Encryption::Tls(Default::default())),
        login: account_config.email.clone(),
        auth,
        ..Default::default()
    });

    let imap_ctx = ImapContextBuilder::new(email_config.clone(), imap_config.clone());
    let backend = BackendBuilder::new(email_config, imap_ctx)
        .build()
        .await?;
    Ok(backend)
}

async fn list_envelopes(
    backend: &Backend<ImapContext>,
    folder: &str,
    page: usize,
    page_size: usize,
    query: Option<SearchEmailsQuery>,
) -> Result<Vec<Envelope>> {
    let envelopes = backend.list_envelopes(
        folder,
        ListEnvelopesOptions {
            page,
            page_size,
            query,
        },
    ).await?;
    Ok(envelopes.to_vec())
}

#[derive(Parser)]
#[command(name = "anmari")]
#[command(about = "Email cache with selective body storage", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Add a new email account
    AddAccount {
        /// Email address
        #[arg(short, long)]
        email: String,

        /// IMAP host
        #[arg(short = 's', long)]
        imap_host: String,

        /// IMAP port
        #[arg(short, long, default_value = "993")]
        imap_port: u16,

        /// Days to cache full message bodies
        #[arg(short, long, default_value = "90")]
        cache_days: u32,

        /// Password (optional)
        #[arg(short = 'w', long)]
        password: Option<String>,
    },

    /// Sync emails from IMAP to local cache
    Sync {
        /// Account index (from list-accounts)
        #[arg(short, long, default_value = "0")]
        account: usize,

        /// Folder to sync
        #[arg(short, long, default_value = "INBOX")]
        folder: String,

        /// Page size for fetching
        #[arg(long, default_value = "100")]
        page_size: usize,
    },

    /// Search emails using notmuch-style queries
    Search {
        /// Account index (from list-accounts)
        #[arg(short, long, default_value = "0")]
        account: usize,

        /// Search query (e.g., "subject:test and from:example.com")
        query: String,

        /// Search on server instead of local cache
        #[arg(short, long)]
        server: bool,

        /// Folder to search
        #[arg(short, long, default_value = "INBOX")]
        folder: String,

        /// Page number (0-indexed)
        #[arg(short, long, default_value = "0")]
        page: usize,

        /// Auto-paginate through all pages
        #[arg(long)]
        auto_paginate: bool,

        /// Page size
        #[arg(long, default_value = "100")]
        page_size: usize,
        
        /// Show all results (no limit)
        #[arg(long, conflicts_with = "limit")]
        all: bool,
        
        /// Limit number of results to display
        #[arg(short, long, default_value = "20")]
        limit: usize,
    },

    /// List configured accounts
    ListAccounts,

    /// Show config file path
    ConfigPath,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();

    match cli.command {
        Commands::AddAccount {
            email,
            imap_host,
            imap_port,
            cache_days,
            password,
        } => {
            let mut config = Config::load()?;

            let account = AccountConfig {
                email: email.clone(),
                imap_host,
                imap_port,
                cache_days,
                password,
            };

            config.accounts.push(account);
            config.save()?;

            println!("Added account: {}", email);
        }

        Commands::Sync { account, folder, page_size } => {
            let config = Config::load()?;
            let account_config = config.accounts.get(account).context("Account not found")?;

            let cache = init_cache(account, account_config.cache_days)?;
            let backend = init_imap(account_config).await?;

            println!("Syncing {} from {}...", folder, account_config.email);

            let cutoff_date = Utc::now() - Duration::days(account_config.cache_days as i64);
            let mut page_num = 0;
            let mut total_cached = 0;

            loop {
                let envelopes = list_envelopes(&backend, &folder, page_num, page_size, None).await?;

                if envelopes.is_empty() {
                    break;
                }

                println!("Processing page {} ({} messages)...", page_num, envelopes.len());

                if envelopes.is_empty() {
                    break;
                }

                let is_last_page = envelopes.len() < page_size;

                for envelope in &envelopes {
                    let uid: u32 = envelope.id.parse().unwrap_or(0);

                    if cache.get_message(uid, &folder)?.is_some() {
                        continue;
                    }

                    let msg_date = DateTime::from_timestamp(envelope.date.timestamp(), 0)
                        .unwrap_or_else(|| Utc::now());

                    let full_body = if msg_date > cutoff_date {
                        // TODO get full message, should be queued in a separate thread probably
                        None
                    } else {
                        None
                    };

                    let cached_msg = CachedMessage::new(uid, folder.clone(), msg_date, full_body, envelope);

                    cache.insert_message(&cached_msg)?;
                    total_cached += 1;
                }

                if is_last_page {
                    break;
                }

                page_num += 1;
            }

            println!("\nSync complete! Cached {} new messages", total_cached);
        }

        Commands::Search { account, query, server, folder, page, auto_paginate, page_size, all, limit } => {
            let config = Config::load()?;
            let account_config = config.accounts.get(account).context("Account not found")?;
            
            let display_limit = if all { usize::MAX } else { limit };

            if server {
                let backend = init_imap(account_config).await?;

                let search_query = match query.parse::<SearchEmailsQuery>() {
                    Ok(q) => q,
                    Err(_) => SearchEmailsQuery {
                        filter: Some(SearchEmailsFilterQuery::Subject(query.clone())),
                        sort: None,
                    }
                };

                if auto_paginate {
                    let mut current_page = 0;
                    let mut total_found = 0;
                    let mut displayed = 0;

                    loop {
                        let envelopes = list_envelopes(&backend, &folder, current_page, page_size, Some(search_query.clone())).await?;

                        if envelopes.is_empty() {
                            break;
                        }

                        let is_last_page = envelopes.len() < page_size;

                        for envelope in &envelopes {
                            if displayed >= display_limit {
                                break;
                            }
                            println!("  [{}] {:?} - {}", envelope.id, envelope.from, envelope.subject);
                            displayed += 1;
                        }

                        total_found += envelopes.len();

                        if displayed >= display_limit || is_last_page {
                            break;
                        }

                        current_page += 1;
                    }

                    println!("\nShowing {} of {} messages", displayed, total_found);
                } else {
                    let envelopes = list_envelopes(&backend, &folder, page, page_size, Some(search_query)).await?;

                    println!("Found {} messages (page {}):", envelopes.len(), page);
                    for envelope in envelopes.iter().take(display_limit) {
                        println!("  [{}] {:?} - {}", envelope.id, envelope.from, envelope.subject);
                    }
                    
                    if envelopes.len() > display_limit {
                        println!("  ... and {} more", envelopes.len() - display_limit);
                    }
                }
            } else {
                let cache = init_cache(account, account_config.cache_days)?;

                let search_query = match query.parse::<SearchEmailsQuery>() {
                    Ok(q) => q,
                    Err(_) => SearchEmailsQuery {
                        filter: Some(SearchEmailsFilterQuery::Subject(query.clone())),
                        sort: None,
                    }
                };

                let results = cache.search_with_query(&search_query, &folder)?;

                println!("Found {} messages in cache:", results.len());
                for msg in results.iter().take(display_limit) {
                    println!("  [{}] {} - {}", msg.uid, msg.from_addr, msg.subject);
                }

                if results.len() > display_limit {
                    println!("  ... and {} more", results.len() - display_limit);
                }
            }
        }

        Commands::ListAccounts => {
            let config = Config::load()?;

            if config.accounts.is_empty() {
                println!("No accounts configured");
            } else {
                println!("Configured accounts:");
                for (i, account) in config.accounts.iter().enumerate() {
                    println!("  [{}] {}", i, account.email);
                    println!("      IMAP: {}:{}", account.imap_host, account.imap_port);
                    println!("      Cache: {} days", account.cache_days);
                }
            }
        }

        Commands::ConfigPath => {
            let path = Config::config_path()?;
            println!("{}", path.display());
        }
    }

    Ok(())
}

