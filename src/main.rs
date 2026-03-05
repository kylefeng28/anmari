use clap::{Parser, Subcommand};
use log::{info};
use env_logger;

mod config;
mod imap;
mod cache;
mod sync;

#[derive(Parser)]
#[command(name = "anmari")]
#[command(about = "Email search and tagging system with cache and IMAP support", long_about = None)]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Sync emails from IMAP to local cache
    Sync {
        /// Specific folder to sync
        #[arg(long)]
        folder: Option<String>,

        /// Sync all folders
        #[arg(long)]
        all_folders: bool,

        /// Page size for fetching
        #[arg(long, default_value = "100")]
        page_size: usize,
    },

    /// Search emails in local cache
    Search {
        /// Search query
        query: String,
    },

    /// List all folders/mailboxes
    Folders,

    /// Apply local tags to messages matching a query
    Tag {
        /// Tag operations and search query (e.g., +work -inbox from:boss)
        args: Vec<String>,
    },

    /// Clean up the most recent messages from cache
    Cleanup,

    /// Interactive REPL
    Repl,

    /// Queue IMAP operations
    Queue {
        #[command(subcommand)]
        action: QueueAction,
    },

    /// Show pending actions in queue
    Status,

    /// Apply pending actions to IMAP server
    Apply {
        /// Preview without executing
        #[arg(long)]
        dry_run: bool,
    },

}

#[derive(Subcommand)]
enum QueueAction {
    /// Move messages to folder
    Move {
        /// Destination folder
        #[arg(long)]
        to: String,

        /// Search query
        query: Vec<String>,
    },

    /// Archive messages (move to [Gmail]/All Mail)
    Archive {
        /// Search query
        query: Vec<String>,
    },

    /// Add or remove IMAP flags
    Flag {
        /// Add flag
        #[arg(long)]
        add: Option<String>,

        /// Remove flag
        #[arg(long)]
        remove: Option<String>,

        /// Search query
        query: Vec<String>,
    },

    /// Add or remove Gmail labels
    Label {
        /// Add label
        #[arg(long)]
        add: Option<String>,

        /// Remove label
        #[arg(long)]
        remove: Option<String>,

        /// Search query
        query: Vec<String>,
    },

    /// Mark messages as read
    Markread {
        /// Search query
        query: Vec<String>,
    },

    /// Mark messages as unread
    Markunread {
        /// Search query
        query: Vec<String>,
    },

    /// Clear all pending actions
    Clear,

    /// Undo last N actions
    Undo {
        /// Number of actions to undo
        #[arg(long, default_value = "1")]
        count: usize,
    },
}

fn main() {
    let cli = Cli::parse();
    env_logger::init();

    // Load config
    let config = match config::Config::load() {
        Ok(cfg) => cfg,
        Err(e) => {
            eprintln!("Error loading config: {}", e);
            std::process::exit(1);
        }
    };

    // Get first account (default)
    let account = match config.get_account(0) {
        Some(acc) => acc,
        None => {
            eprintln!("Error: No accounts configured");
            std::process::exit(1);
        }
    };

    info!("Using account: {}", account.email);

    // Initialize cache
    let cache = match cache::EmailCache::new(0) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error initializing cache: {}", e);
            std::process::exit(1);
        }
    };

    println!("Cache initialized");

    // Test get_message with a real message
    let uid = 1;
    match cache.get_message(uid, "INBOX") {
        Ok(Some(msg)) => {
            println!("Found cached message:");
            println!("  uid: {}", msg.uid);
            println!("  folder: {}", msg.folder);
            println!("  from: {}", msg.from_addr);
            println!("  subject: {}", msg.subject);
            println!("  flags: {}", msg.flags);
        }
        Ok(None) => {
            println!("No message found with uid={} in INBOX", uid);
        }
        Err(e) => {
            println!("Error querying message: {}", e);
        }
    }

    // Test get_folder_state
    match cache.get_folder_state("INBOX") {
        Ok(Some(state)) => {
            println!("\nFolder state for INBOX:");
            println!("  uidvalidity: {}", state.uidvalidity);
            println!("  highestmodseq: {}", state.highestmodseq);
        }
        Ok(None) => {
            println!("\nNo folder state found for INBOX");
        }
        Err(e) => {
            println!("\nError querying folder state: {}", e);
        }
    }

    // Connect to IMAP
    let mut client = match imap::ImapClient::connect(account) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error connecting to IMAP: {}", e);
            std::process::exit(1);
        }
    };

    client.print_status_debug();

    match cli.command {
        Commands::Sync { folder, all_folders, page_size } => {
            info!("Sync: folder={:?}, all_folders={}, page_size={}", 
                     folder, all_folders, page_size);

            let folder_to_sync = folder.as_deref().unwrap_or("INBOX");
            let mut syncer = sync::Syncer::new(&mut client, &cache);

            match syncer.sync_folder(folder_to_sync) {
                Ok(_) => info!("Sync completed successfully"),
                Err(e) => eprintln!("Sync error: {}", e),
            }
        }
        Commands::Search { query } => {
            info!("Search: {}", query);
        }
        Commands::Tag { args } => {
            info!("Tag: {:?}", args);
        }
        Commands::Queue { action } => {
            match action {
                QueueAction::Move { to, query } => {
                    info!("Queue move to '{}': {:?}", to, query);
                }
                QueueAction::Archive { query } => {
                    info!("Queue archive: {:?}", query);
                }
                QueueAction::Flag { add, remove, query } => {
                    info!("Queue flag: add={:?}, remove={:?}, query={:?}", add, remove, query);
                }
                QueueAction::Label { add, remove, query } => {
                    info!("Queue label: add={:?}, remove={:?}, query={:?}", add, remove, query);
                }
                QueueAction::Markread { query } => {
                    info!("Queue markread: {:?}", query);
                }
                QueueAction::Markunread { query } => {
                    info!("Queue markunread: {:?}", query);
                }
                QueueAction::Clear => {
                    info!("Queue clear");
                }
                QueueAction::Undo { count } => {
                    info!("Queue undo: count={}", count);
                }
            }
        }
        Commands::Status => {
            info!("Status");
        }
        Commands::Apply { dry_run } => {
            info!("Apply: dry_run={}", dry_run);
        }
        Commands::Folders => {
            info!("Folders");
        }
        Commands::Cleanup => {
            info!("Cleanup");
        }
        Commands::Repl => {
            info!("REPL");
        }
    }
}
