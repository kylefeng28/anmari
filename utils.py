from datetime import datetime, timezone
import dateparser

def decode_if_bytes(maybe_bytes):
    if isinstance(maybe_bytes, bytes):
        return maybe_bytes.decode()
    else:
        return str(maybe_bytes)

def format_datetime_sqlite(dt: datetime) -> str:
    # Format datetime as UTC YYYY-MM-DD H:M:S without Z (zulu) or timestamp
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def parse_datestr(date_str: str) -> datetime:
    """Parse date string to datetime object

    Supports:
    - Absolute: 2024-01-01, 2024-01-01T10:30:00
    - Relative: yesterday, today, "1 week ago", "2 days ago"
    - Combined: "yesterday 5pm", "2024-01-01 + 1 week"
    """
    """Use dateparser for flexible parsing for natural language.
    e.g. 3 days ago, last month, last week"""

    parsed = dateparser.parse(date_str, settings={
        'PREFER_DATES_FROM': 'past',
        'RELATIVE_BASE': datetime.now()
    })

    if not parsed:
        raise ValueError(f"Could not parse date: {date_str}")

    return parsed
