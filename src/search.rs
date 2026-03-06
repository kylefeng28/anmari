use std::collections::HashSet;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum TableRequired {
    Tag,
    Label,
}

#[derive(Debug)]
pub struct ParsedQuery {
    pub conditions: String,
    pub params: Vec<String>,
    pub join_clauses: String,
}

/// Parse notmuch-style query into SQL WHERE clause
///
/// Supports:
/// - subject:"text" - Search in subject
/// - from:"text" - Search in from_addr or from_name
/// - body:"text" - Search in body_preview
/// - tag:tagname - Search by local tag
/// - label:labelname - Search by Gmail label
/// - is:read / is:unread - Check read status (based on \\Seen flag)
/// - date:<since>..<until> - Date range (e.g., date:2024-01-01..2024-12-31)
/// - date:<date> - Specific date or relative (e.g., date:yesterday, date:"1 week ago")
/// - AND, OR, NOT operators
/// - Bare words search across subject and from fields
///
/// Examples:
/// - subject:"meeting" AND from:"boss@example.com"
/// - from:"alice" OR from:"bob"
/// - subject:"invoice" NOT from:"spam"
/// - tag:newsletter
/// - label:INBOX
/// - label:Important AND is:unread
/// - is:unread
/// - is:read AND tag:important
/// - subject:concert AND tag:events
/// - date:2024-01-01..2024-12-31
/// - date:yesterday
/// - date:"1 week ago"..today
pub fn parse_search_query(query: &str) -> ParsedQuery {
    let tokens = shell_words::split(query).unwrap_or_else(|_| {
        // Fallback to simple split on whitespace
        query.split_whitespace().map(String::from).collect()
    });

    if tokens.is_empty() {
        return ParsedQuery {
            conditions: "1=1".to_string(),
            params: vec![],
            join_clauses: String::new(),
        };
    }

    let mut conditions = Vec::new();
    let mut params = Vec::new();
    let mut tables_required = HashSet::new();

    let mut i = 0;
    while i < tokens.len() {
        let token = &tokens[i];
        let token_upper = token.to_uppercase();

        // Handle NOT operator
        if token_upper == "NOT" {
            i += 1;
            if i >= tokens.len() {
                break;
            }
            let (cond, mut param, tables) = parse_token(&tokens[i]);
            conditions.push(format!("NOT ({})", cond));
            params.append(&mut param);
            tables_required.extend(tables);
            i += 1;
            continue;
        }

        // Parse current token
        let (cond, mut param, tables) = parse_token(token);
        conditions.push(cond);
        params.append(&mut param);
        tables_required.extend(tables);

        // Check for AND/OR operator
        if i + 1 < tokens.len() {
            let next_token_upper = tokens[i + 1].to_uppercase();
            if next_token_upper == "AND" || next_token_upper == "OR" {
                conditions.push(next_token_upper);
                i += 2;
                continue;
            }
        }

        i += 1;
        // Implicit AND between terms
        if i < tokens.len() && !["AND", "OR", "NOT"].contains(&tokens[i].to_uppercase().as_str()) {
            conditions.push("AND".to_string());
        }
    }

    let join_clauses: Vec<&str> = tables_required.iter().map(|table| {
        match table {
            TableRequired::Tag => "LEFT JOIN tags t ON m.uid = t.uid AND m.folder = t.folder",
            TableRequired::Label => "LEFT JOIN gm_labels l ON m.uid = l.uid AND m.folder = l.folder",
        }
    }).collect();

    ParsedQuery {
        conditions: conditions.join(" "),
        params,
        join_clauses: join_clauses.join("\n"),
    }
}

fn parse_token(token: &str) -> (String, Vec<String>, HashSet<TableRequired>) {
    let mut tables = HashSet::new();

    // Field-specific search: field:value
    if let Some(colon_pos) = token.find(':') {
        let field = &token[..colon_pos];
        let value = &token[colon_pos + 1..];

        match field {
            "uid" => {
                if let Some(range_pos) = value.find("..") {
                    let start = &value[..range_pos];
                    let end = &value[range_pos + 2..];
                    return (
                        "m.uid BETWEEN ? AND ?".to_string(),
                        vec![start.to_string(), end.to_string()],
                        tables,
                    );
                } else {
                    return ("m.uid = ?".to_string(), vec![value.to_string()], tables);
                }
            }
            "tag" => {
                tables.insert(TableRequired::Tag);
                return ("t.tag = ?".to_string(), vec![value.to_string()], tables);
            }
            "label" => {
                tables.insert(TableRequired::Label);
                return ("l.label = ?".to_string(), vec![value.to_string()], tables);
            }
            "is" => {
                let value_lower = value.to_lowercase();
                if value_lower == "read" {
                    return ("m.flags LIKE ?".to_string(), vec!["%\\Seen%".to_string()], tables);
                } else if value_lower == "unread" {
                    return ("m.flags NOT LIKE ?".to_string(), vec!["%\\Seen%".to_string()], tables);
                }
            }
            "date" => {
                if let Some(range_pos) = value.find("..") {
                    let since_str = &value[..range_pos];
                    let until_str = &value[range_pos + 2..];
                    let since_date = parse_date(since_str);
                    let until_date = parse_date(until_str);
                    return (
                        "m.date BETWEEN ? AND ?".to_string(),
                        vec![since_date, until_date],
                        tables,
                    );
                } else {
                    let date_str = parse_date(value);
                    let date_only = date_str.split(' ').next().unwrap_or(&date_str);
                    let date_start = format!("{} 00:00:00", date_only);
                    let date_end = format!("{} 23:59:59", date_only);
                    return (
                        "m.date BETWEEN ? AND ?".to_string(),
                        vec![date_start, date_end],
                        tables,
                    );
                }
            }
            "since" => {
                let since_date = parse_date(value);
                return ("m.date >= ?".to_string(), vec![since_date], tables);
            }
            "subject" => {
                let pattern = format!("%{}%", value);
                return ("m.subject LIKE ?".to_string(), vec![pattern], tables);
            }
            "from" => {
                let pattern = format!("%{}%", value);
                return (
                    "(m.from_addr LIKE ? OR m.from_name LIKE ?)".to_string(),
                    vec![pattern.clone(), pattern],
                    tables,
                );
            }
            "body" => {
                let pattern = format!("%{}%", value);
                return ("m.body_preview LIKE ?".to_string(), vec![pattern], tables);
            }
            _ => {}
        }
    }

    // Bare word: search in subject and from fields
    let pattern = format!("%{}%", token);
    (
        "(m.subject LIKE ? OR m.from_addr LIKE ? OR m.from_name LIKE ?)".to_string(),
        vec![pattern.clone(), pattern.clone(), pattern],
        tables,
    )
}

fn parse_date(date_str: &str) -> String {
    use chrono::Local;
    use chrono_english::{parse_date_string, Dialect};

    parse_date_string(date_str, Local::now(), Dialect::Us)
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_else(|_| date_str.to_string())
}
