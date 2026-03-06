use fast_rich::prelude::*;
use fast_rich::{
    table::ColumnWidth,
    style::{Style, Color},
};

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

        let subject = if msg.subject.len() > 60 {
            format!("{}...", &msg.subject[..57])
        } else {
            msg.subject.clone()
        };

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
