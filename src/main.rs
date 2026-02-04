use anmari::{AccountConfig, CacheConfig, CachedMessage, Config, EmailCache};
use anyhow::{Context, Result};
use chrono::{DateTime, Duration, Utc};
use clap::{Parser, Subcommand};
use email::{
    account::config::{passwd::PasswordConfig, AccountConfig as EmailAccountConfig},
    backend::BackendBuilder,
    envelope::list::{ListEnvelopes, ListEnvelopesOptions},
    imap::{
        config::{ImapAuthConfig, ImapConfig},
        ImapContextBuilder,
    },
    search_query::{filter::SearchEmailsFilterQuery, SearchEmailsQuery},
    tls::Encryption,
};
use secret::Secret;
use std::sync::Arc;

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
    },
    
    /// Search emails using notmuch-style queries
    Search {
        /// Account index (from list-accounts)
        #[arg(short, long, default_value = "0")]
        account: usize,
        
        /// Search query (e.g., "subject:test and from:example.com")
        query: String,
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
        
        Commands::Sync { account, folder } => {
            let config = Config::load()?;
            
            let account_config = config
                .accounts
                .get(account)
                .context("Account not found")?;
            
            // Initialize cache
            let cache_config = CacheConfig {
                db_path: format!("anmari_{}.db", account),
                cache_days: account_config.cache_days,
            };
            let cache = EmailCache::new(cache_config)?;
            
            // Build IMAP config
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
            
            println!("Syncing {} from {}...", folder, account_config.email);
            
            // Fetch all envelopes
            let envelopes = backend.list_envelopes(
                &folder,
                ListEnvelopesOptions {
                    page: 0,
                    page_size: 1000,
                    query: None,
                },
            ).await?;
            
            println!("Found {} messages", envelopes.len());
            
            let cutoff_date = Utc::now() - Duration::days(account_config.cache_days as i64);

            let mut total_cached = 0;
            for (i, envelope) in envelopes.iter().enumerate() {
                let uid: u32 = envelope.id.parse().unwrap_or(0);
                
                // Check if already cached
                if cache.get_message(uid, &folder)?.is_some() {
                    continue;
                }
                
                let msg_date = DateTime::from_timestamp(envelope.date.timestamp(), 0)
                    .unwrap_or_else(|| Utc::now());
                
                // For now, only store headers. Full body fetching can be added later
                let full_body = if msg_date > cutoff_date {
                    // TODO: Fetch full body for recent messages
                    None
                } else {
                    None
                };
                
                let cached_msg = CachedMessage {
                    uid,
                    folder: folder.clone(),
                    from: format!("{:?}", envelope.from),
                    subject: envelope.subject.clone(),
                    date: msg_date,
                    body_preview: None,
                    full_body,
                    flags: envelope.flags.iter().map(|f| f.to_string()).collect(),
                };
                
                cache.insert_message(&cached_msg)?;
                total_cached += 1;
                
                if (i + 1) % 10 == 0 {
                    print!("\rCached {}/{} messages", i + 1, envelopes.len());
                }

            }

            println!("\nSync complete!");
            print!("\rCached {} new messages out of {} total messages", total_cached, envelopes.len());
        }
        
        Commands::Search { account, query } => {
            let config = Config::load()?;
            
            let account_config = config
                .accounts
                .get(account)
                .context("Account not found")?;
            
            // Build IMAP config
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
            
            // Initialize IMAP context and backend
            let imap_ctx = ImapContextBuilder::new(email_config.clone(), imap_config.clone());
            let backend = BackendBuilder::new(email_config, imap_ctx)
                .build()
                .await?;

            // Try to parse query, if it fails, treat as simple subject search
            let search_query = match query.parse::<SearchEmailsQuery>() {
                Ok(q) => q,
                Err(_) => {
                    // Fallback: treat as subject search
                    SearchEmailsQuery {
                        filter: Some(SearchEmailsFilterQuery::Subject(query.clone())),
                        sort: None,
                    }
                }
            };
            
            let envelopes = backend.list_envelopes(
                "INBOX",
                ListEnvelopesOptions {
                    page: 0,
                    page_size: 100,
                    query: Some(search_query),
                },
            ).await?;
            
            println!("Found {} messages:", envelopes.len());
            for envelope in envelopes.iter().take(20) {
                println!("  [{}] {:?} - {}", 
                    envelope.id, 
                    envelope.from, 
                    envelope.subject
                );
            }
            
            if envelopes.len() > 20 {
                println!("  ... and {} more", envelopes.len() - 20);
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

