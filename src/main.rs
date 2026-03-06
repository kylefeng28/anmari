use clap::{Parser, Subcommand};
use log::{info, debug};
use env_logger;

mod config;
mod imap;
mod cache;
mod sync;
mod display;
mod search;

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

        /// Fallback sync (regardless of whether CONDSTORE is enabled or not)
        #[arg(long)]
        fallback: bool,

        /// Dry run (if turned on, do not update cache)
        #[arg(long)]
        dry_run: bool
    },

    /// Search emails in local cache
    Search {
        /// Search query
        query: String,

        /// Limit number of results
        #[arg(short, long, default_value_t = 20)]
        limit: usize,

        /// Show all results
        #[arg(short, long)]
        all: bool,
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

fn init_imap_client(account: &config::Account) -> imap::ImapClient {
    let client = match imap::ImapClient::connect(account) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("Error connecting to IMAP: {}", e);
            std::process::exit(1);
        }
    };

    client.print_status_debug();

    client
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

    info!("Cache initialized");

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

    match cli.command {
        Commands::Sync { folder, all_folders, page_size, fallback, dry_run } => {
            info!("Sync: folder={:?}, all_folders={}, page_size={}", 
                     folder, all_folders, page_size);

            let mut client = init_imap_client(account);

            let folder_to_sync = folder.as_deref().unwrap_or("INBOX");
            let mut syncer = sync::Syncer::new(&mut client, &cache);

            match syncer.sync_folder(folder_to_sync, fallback, page_size, dry_run) {
                Ok(_) => info!("Sync completed successfully"),
                Err(e) => eprintln!("Sync error: {}", e),
            }
        }
        Commands::Search { query, limit, all } => {
            let results = cache.search("INBOX", &query).ok().unwrap();
            display::display_messages_table(&results, limit, all);
        }
        Commands::Tag { args } => {
            debug!("Tag: {:?}", args);

            let mut tags_to_add = Vec::new();
            let mut tags_to_remove = Vec::new();
            let mut query_parts = Vec::new();

            for part in &args {
                if let Some(tag) = part.strip_prefix('+') {
                    tags_to_add.push(tag.to_string());
                } else if let Some(tag) = part.strip_prefix('-') {
                    tags_to_remove.push(tag.to_string());
                } else {
                    query_parts.push(part.as_str());
                }
            }

            if tags_to_add.is_empty() && tags_to_remove.is_empty() {
                eprintln!("Error: No tags specified. Use +tag to add, -tag to remove");
            } else {
                let query = query_parts.join(" ");
                let results = cache.search("INBOX", &query).unwrap();
                let count = cache.tag_messages(&results, &tags_to_add, &tags_to_remove).unwrap();
                let add_str = if !tags_to_add.is_empty() { format!("+{}", tags_to_add.join(", +")) } else { String::new() };
                let remove_str = if !tags_to_remove.is_empty() { format!("-{}", tags_to_remove.join(", -")) } else { String::new() };
                println!("Tagged {} messages with {}", count, [add_str, remove_str].iter().filter(|s| !s.is_empty()).cloned().collect::<Vec<_>>().join(" "));
            }
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
