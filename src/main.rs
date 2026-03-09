mod cache;
mod cli;
mod config;
mod display;
mod imap;
mod repl;
mod search;
mod sync;

fn main() {
    crate::cli::cli();
}
