import re
import shlex
from utils import parse_datestr, format_datetime_sqlite

NONE = set()
TAG = 'TAG'

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
        (conditions, params, join_clauses)
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
    tables_required = set()

    i = 0
    while i < len(tokens):
        token = tokens[i]
        token_upper = token.upper()

        # Handle NOT operator
        if token_upper == 'NOT':
            i += 1
            if i >= len(tokens):
                break
            cond, param, tables = _parse_token(tokens[i])
            conditions.append(f"NOT ({cond})")
            params.extend(param)
            tables_required.update(tables)
            i += 1
            continue

        # Parse current token
        cond, param, tables = _parse_token(token)
        conditions.append(cond)
        params.extend(param)
        tables_required.update(tables)

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

    join_clauses = []

    if TAG in tables_required:
        join_clauses.append('LEFT JOIN tags t ON m.uid = t.uid AND m.folder = t.folder')

    return ' '.join(conditions), params, '\n'.join(join_clauses)


def _parse_date(date_str: str) -> str:
    parsed = parse_datestr(date_str)
    return format_datetime_sqlite(parsed)


def _parse_token(token: str) -> tuple[str, list, str]:
    """Parse a single search token into SQL condition

    Returns:
        (condition, params, tables)
    """

    # Field-specific search: field:"value"
    if ':' in token:
        field, value = token.split(':', 1)

        # Tag search
        if field == 'tag':
            return "t.tag = ?", [value], set([TAG])

        # Read/unread status
        if field == 'is':
            value = value.lower()
            if value == 'read':
                return "m.flags LIKE ?", ['%\\Seen%'], NONE
            elif value == 'unread':
                return "m.flags NOT LIKE ?", ['%\\Seen%'], NONE

        # Date field with range or single date
        if field == 'date':
            # Range: date:2024-01-01..2024-12-31
            if '..' in value:
                since_str, until_str = value.split('..', 1)
                since_date = _parse_date(since_str)
                until_date = _parse_date(until_str)
                return "m.date BETWEEN ? AND ?", [since_date, until_date], NONE
            else:
                # Single date: treat as that day (00:00 to 23:59:59)
                date_str = _parse_date(value)
                # Extract just the date part and create range
                date_only = date_str.split(' ')[0]
                date_start = f"{date_only} 00:00:00"
                date_end = f"{date_only} 23:59:59"
                return "m.date BETWEEN ? AND ?", [date_start, date_end], NONE

        # Since field
        elif field == 'since':
            since_date = _parse_date(value)
            return "m.date >= ?", [since_date], NONE

        # shlex already removed quotes, so don't strip them again
        pattern = f"%{value}%"

        if field == 'subject':
            return "m.subject LIKE ?", [pattern], NONE
        elif field == 'from':
            return "(m.from_addr LIKE ? OR m.from_name LIKE ?)", [pattern, pattern], NONE
        elif field == 'body':
            return "m.body_preview LIKE ?", [pattern], NONE

    # Bare word: search in subject and from fields
    pattern = f"%{token}%"
    return "(m.subject LIKE ? OR m.from_addr LIKE ? OR m.from_name LIKE ?)", [pattern, pattern, pattern], NONE
