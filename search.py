import re
from datetime import datetime, timedelta
import dateparser


def parse_search_query(query: str) -> tuple[str, list]:
    """Parse notmuch-style query into SQL WHERE clause

    Supports:
    - subject:"text" - Search in subject
    - from:"text" - Search in from_addr or from_name
    - body:"text" - Search in body_preview
    - date:<since>..<until> - Date range (e.g., date:2024-01-01..2024-12-31)
    - date:<date> - Specific date or relative (e.g., date:yesterday, date:"1 week ago")
    - AND, OR, NOT operators
    - Bare words search across subject and from fields

    Examples:
    - subject:"meeting" AND from:"boss@example.com"
    - from:"alice" OR from:"bob"
    - subject:"invoice" NOT from:"spam"
    - date:2024-01-01..2024-12-31
    - date:yesterday
    - date:"1 week ago"..today
    """

    # Tokenize: field:"value", operators, bare words
    # Updated regex to handle date ranges with ..
    tokens = re.findall(r'(\w+:"[^"]*"(?:\.\.[^"\s]*)?|\w+:[^\s]+(?:\.\.[^\s]+)?|\bAND\b|\bOR\b|\bNOT\b|\w+)', query)

    if not tokens:
        return "1=1", []

    conditions = []
    params = []
    i = 0

    while i < len(tokens):
        token = tokens[i]

        # Handle NOT operator
        if token == 'NOT':
            i += 1
            if i >= len(tokens):
                break
            cond, param = _parse_token(tokens[i])
            conditions.append(f"NOT ({cond})")
            params.extend(param)
            i += 1
            continue

        # Parse current token
        cond, param = _parse_token(token)
        conditions.append(cond)
        params.extend(param)

        # Check for AND/OR operator
        if i + 1 < len(tokens) and tokens[i + 1] in ('AND', 'OR'):
            conditions.append(tokens[i + 1])
            i += 2
        else:
            i += 1
            # Implicit AND between terms
            if i < len(tokens) and tokens[i] not in ('AND', 'OR', 'NOT'):
                conditions.append('AND')

    return ' '.join(conditions), params


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

    return int(parsed.timestamp())


def _parse_token(token: str) -> tuple[str, list]:
    """Parse a single search token into SQL condition"""
    # Field-specific search: field:"value"
    if ':' in token:
        field, value = token.split(':', 1)

        # Date field with range or single date
        if field == 'date':
            # Range: date:2024-01-01..2024-12-31
            if '..' in value:
                since_str, until_str = value.split('..', 1)
                since_ts = _parse_date(since_str)
                until_ts = _parse_date(until_str)
                return "date BETWEEN ? AND ?", [since_ts, until_ts]
            else:
                # Single date: treat as that day (00:00 to 23:59:59)
                date_ts = _parse_date(value)
                date_start = datetime.fromtimestamp(date_ts).replace(hour=0, minute=0, second=0)
                date_end = datetime.fromtimestamp(date_ts).replace(hour=23, minute=59, second=59)
                return "date BETWEEN ? AND ?", [int(date_start.timestamp()), int(date_end.timestamp())]

        # Since field
        elif field == 'since':
            since_ts = _parse_date(value)
            return "date >= ?", [since_ts]

        value = value.strip('"')
        pattern = f"%{value}%"

        if field == 'subject':
            return "subject LIKE ?", [pattern]
        elif field == 'from':
            return "(from_addr LIKE ? OR from_name LIKE ?)", [pattern, pattern]
        elif field == 'body':
            return "body_preview LIKE ?", [pattern]

    # Bare word: search in subject and from fields
    pattern = f"%{token}%"
    return "(subject LIKE ? OR from_addr LIKE ? OR from_name LIKE ?)", [pattern, pattern, pattern]
