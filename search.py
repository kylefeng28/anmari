import re
import shlex
from datetime import datetime, timedelta
import dateparser


def parse_search_query(query: str | list[str]) -> tuple[str, list, bool]:
    """Parse notmuch-style query into SQL WHERE clause

    Supports:
    - subject:"text" - Search in subject
    - from:"text" - Search in from_addr or from_name
    - body:"text" - Search in body_preview
    - tag:tagname - Search by tag
    - is:read / is:unread - Check read status (based on \\Seen flag)
    - date:<since>..<until> - Date range (e.g., date:2024-01-01..2024-12-31)
    - date:<date> - Specific date or relative (e.g., date:yesterday, date:"1 week ago")
    - AND, OR, NOT operators
    - Bare words search across subject and from fields

    Examples:
    - subject:"meeting" AND from:"boss@example.com"
    - from:"alice" OR from:"bob"
    - subject:"invoice" NOT from:"spam"
    - tag:newsletter
    - is:unread
    - is:read AND tag:important
    - subject:concert AND tag:events
    - date:2024-01-01..2024-12-31
    - date:yesterday
    - date:"1 week ago"..today
    
    Returns:
        (conditions, params, has_tag_filter)
    """

    # Use shlex to properly handle quoted strings
    if isinstance(query, str):
        try:
            tokens = shlex.split(query)
        except ValueError as e:
            # Fallback to regex if shlex fails
            print('Could not parse query with shlex, falling back to regex')
            tokens = re.findall(r'(\w+:"[^"]*"(?:\.\.[^"\s]*)?|\w+:[^\s]+(?:\.\.[^\s]+)?|\bAND\b|\bOR\b|\bNOT\b|\w+)', query)
    elif isinstance(query, list) or isinstance(query, tuple):
        tokens = query

    if not tokens:
        return "1=1", [], False

    conditions = []
    params = []
    has_tag_filter = False
    i = 0

    while i < len(tokens):
        token = tokens[i]
        token_upper = token.upper()

        # Handle NOT operator
        if token_upper == 'NOT':
            i += 1
            if i >= len(tokens):
                break
            cond, param, is_tag = _parse_token(tokens[i])
            conditions.append(f"NOT ({cond})")
            params.extend(param)
            has_tag_filter = has_tag_filter or is_tag
            i += 1
            continue

        # Parse current token
        cond, param, is_tag = _parse_token(token)
        conditions.append(cond)
        params.extend(param)
        has_tag_filter = has_tag_filter or is_tag

        # Check for AND/OR operator
        if i + 1 < len(tokens):
            next_token_upper = tokens[i + 1].upper()
            if next_token_upper in ('AND', 'OR'):
                conditions.append(next_token_upper)
                i += 2
                continue
        
        i += 1
        # Implicit AND between terms
        if i < len(tokens) and tokens[i].upper() not in ('AND', 'OR', 'NOT'):
            conditions.append('AND')

    return ' '.join(conditions), params, has_tag_filter


def _parse_date(date_str: str) -> int:
    """Parse date string to Unix timestamp

    Supports:
    - Absolute: 2024-01-01, 2024-01-01T10:30:00
    - Relative: yesterday, today, "1 week ago", "2 days ago"
    - Combined: "yesterday 5pm", "2024-01-01 + 1 week"
    """
    date_str = date_str.strip('"')

    # Use dateparser for flexible parsing
    parsed = dateparser.parse(date_str, settings={
        'PREFER_DATES_FROM': 'past',
        'RELATIVE_BASE': datetime.now()
    })

    if not parsed:
        raise ValueError(f"Could not parse date: {date_str}")

    # Return SQLite datetime format (since dates are stored as TEXT)
    return parsed.strftime('%Y-%m-%d %H:%M:%S')


def _parse_token(token: str) -> tuple[str, list, bool]:
    """Parse a single search token into SQL condition
    
    Returns:
        (condition, params, is_tag_filter)
    """

    # Field-specific search: field:"value"
    if ':' in token:
        field, value = token.split(':', 1)

        # Tag search
        if field == 'tag':
            return "t.tag = ?", [value], True

        # Read/unread status
        if field == 'is':
            value = value.lower()
            if value == 'read':
                return "m.flags LIKE ?", ['%\\Seen%'], False
            elif value == 'unread':
                return "m.flags NOT LIKE ?", ['%\\Seen%'], False

        # Date field with range or single date
        if field == 'date':
            # Range: date:2024-01-01..2024-12-31
            if '..' in value:
                since_str, until_str = value.split('..', 1)
                since_date = _parse_date(since_str)
                until_date = _parse_date(until_str)
                return "m.date BETWEEN ? AND ?", [since_date, until_date], False
            else:
                # Single date: treat as that day (00:00 to 23:59:59)
                date_str = _parse_date(value)
                # Extract just the date part and create range
                date_only = date_str.split(' ')[0]
                date_start = f"{date_only} 00:00:00"
                date_end = f"{date_only} 23:59:59"
                return "m.date BETWEEN ? AND ?", [date_start, date_end], False

        # Since field
        elif field == 'since':
            since_date = _parse_date(value)
            return "m.date >= ?", [since_date], False

        # shlex already removed quotes, so don't strip them again
        pattern = f"%{value}%"

        if field == 'subject':
            return "m.subject LIKE ?", [pattern], False
        elif field == 'from':
            return "(m.from_addr LIKE ? OR m.from_name LIKE ?)", [pattern, pattern], False
        elif field == 'body':
            return "m.body_preview LIKE ?", [pattern], False

    # Bare word: search in subject and from fields
    pattern = f"%{token}%"
    return "(m.subject LIKE ? OR m.from_addr LIKE ? OR m.from_name LIKE ?)", [pattern, pattern, pattern], False
