use clap::{ValueEnum};
use std::fmt;
use fast_rich::prelude::*;
use fast_rich::{
    table::ColumnWidth,
    style::{Style, Color},
};

#[derive(Debug, Clone, Default, ValueEnum)]
pub enum OutputFormat {
    #[default]
    Table,
    Json,
}

impl fmt::Display for OutputFormat {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OutputFormat::Table => write!(f, "table"),
            OutputFormat::Json => write!(f, "json"),
        }
    }
}

fn truncate(s: &str, max_chars: usize) -> String {
    // Check for chars().count() instead of len(), and slice using char_indices() instead of
    // s[..cut] since there might be a Unicode character (i.e. emoji)
    if s.chars().count() > max_chars {
        let cut = max_chars - 3;
        let end = s.char_indices().nth(cut).map(|(i, _)| i).unwrap_or(s.len());
        format!("{}...", &s[..end])
    } else {
        s.to_string()
    }
}

pub fn display_messages_json(messages: &[crate::cache::CachedMessage], limit: usize, show_all: bool) {
    let display_limit = if show_all { messages.len() } else { limit.min(messages.len()) };
    println!("{}", serde_json::to_string_pretty(&messages[..display_limit]).unwrap());
}

pub fn display_messages_table(messages: &[crate::cache::CachedMessage], limit: usize, show_all: bool) {
    let console = Console::new();
    let mut table = Table::new();

    table.add_column(Column::new("ID").style(Style::new().foreground(Color::Red)).width(ColumnWidth::Fixed(8)));
    table.add_column(Column::new("FLAGS").width(ColumnWidth::Fixed(6)));
    table.add_column(Column::new("SUBJECT").style(Style::new().foreground(Color::Green)));
    table.add_column(Column::new("FROM").style(Style::new().foreground(Color::Blue)).width(ColumnWidth::Fixed(35)));
    table.add_column(Column::new("DATE").style(Style::new().foreground(Color::Yellow)).width(ColumnWidth::Fixed(20)));

    let display_limit = if show_all { messages.len() } else { limit.min(messages.len()) };

    for msg in &messages[..display_limit] {
        let flags = if msg.flags.contains(&"\\Seen".to_string()) { "" } else { "*" };

        let from_display = msg.from_name.as_ref()
            .filter(|n| !n.is_empty())
            .unwrap_or(&msg.from_addr);

        let subject = truncate(&msg.subject, 60);

        let date_str = if msg.date.len() >= 30 {
            &msg.date[..30]
        } else {
            &msg.date
        };

        table.add_row_strs(&[
            &msg.uid.to_string(),
            flags,
            &subject,
            from_display,
            date_str,
        ]);
    }

    console.println(&format!("\nFound {} messages in cache:", messages.len()));
    console.print_renderable(&table);

    if messages.len() > display_limit {
        console.println(&format!("... and {} more", messages.len() - display_limit));
    }
}

#[cfg(test)]
mod tests {
    use super::truncate;

    #[test]
    fn test_truncate_unicode_emoji() {
        // Each emoji is multiple bytes; naive byte slicing at index 57 (💰) would panic
        let s = "Cursor revenue leaks 📈, Anthropic risks $60B round 💰, Claude outage 💻";
        let result = truncate(s, 60);
        assert!(result.ends_with("..."));
        assert!(result.chars().count() <= 60);
        assert!(std::str::from_utf8(result.as_bytes()).is_ok());
        assert_eq!(result, "Cursor revenue leaks 📈, Anthropic risks $60B round 💰, Cla...");
    }

    #[test]
    fn test_truncate_short_string() {
        let s = "short";
        assert_eq!(truncate(s, 60), "short");
    }
}
