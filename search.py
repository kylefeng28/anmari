import re

def parse_search_query(query: str) -> tuple[str, list]:
    """Parse notmuch-style query into SQL WHERE clause

    Supports:
    - subject:"text" - Search in subject
    - from:"text" - Search in from_addr or from_name
    - body:"text" - Search in body_preview
    - AND, OR, NOT operators
    - Bare words search across subject and from fields

    Examples:
    - subject:"meeting" AND from:"boss@example.com"
    - from:"alice" OR from:"bob"
    - subject:"invoice" NOT from:"spam"
    """

    # Tokenize: field:"value", operators, bare words
    tokens = re.findall(r'(\w+:"[^"]*"|\bAND\b|\bOR\b|\bNOT\b|\w+)', query)

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

def _parse_token(token: str) -> tuple[str, list]:
    """Parse a single search token into SQL condition"""
    # Field-specific search: field:"value"
    if ':' in token:
        field, value = token.split(':', 1)
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
