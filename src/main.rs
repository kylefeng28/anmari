use clap::{Parser, Subcommand};

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

    match cli.command {
        Commands::Sync { folder, all_folders, page_size } => {
            println!("Sync: folder={:?}, all_folders={}, page_size={}", 
                     folder, all_folders, page_size);
        }
        Commands::Search { query } => {
            println!("Search: {}", query);
        }
        Commands::Tag { args } => {
            println!("Tag: {:?}", args);
        }
        Commands::Queue { action } => {
            match action {
                QueueAction::Move { to, query } => {
                    println!("Queue move to '{}': {:?}", to, query);
                }
                QueueAction::Archive { query } => {
                    println!("Queue archive: {:?}", query);
                }
                QueueAction::Flag { add, remove, query } => {
                    println!("Queue flag: add={:?}, remove={:?}, query={:?}", add, remove, query);
                }
                QueueAction::Label { add, remove, query } => {
                    println!("Queue label: add={:?}, remove={:?}, query={:?}", add, remove, query);
                }
                QueueAction::Markread { query } => {
                    println!("Queue markread: {:?}", query);
                }
                QueueAction::Markunread { query } => {
                    println!("Queue markunread: {:?}", query);
                }
                QueueAction::Clear => {
                    println!("Queue clear");
                }
                QueueAction::Undo { count } => {
                    println!("Queue undo: count={}", count);
                }
            }
        }
        Commands::Status => {
            println!("Status");
        }
        Commands::Apply { dry_run } => {
            println!("Apply: dry_run={}", dry_run);
        }
        Commands::Folders => {
            println!("Folders");
        }
        Commands::Cleanup => {
            println!("Cleanup");
        }
        Commands::Repl => {
            println!("REPL");
        }
    }
}
