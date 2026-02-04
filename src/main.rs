use anmari::{AccountConfig, Config};
use anyhow::Result;
use clap::{Parser, Subcommand};

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
        } => {
            let mut config = Config::load()?;
            
            let account = AccountConfig {
                email: email.clone(),
                imap_host,
                imap_port,
                cache_days,
            };
            
            config.accounts.push(account);
            config.save()?;
            
            println!("Added account: {}", email);
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
