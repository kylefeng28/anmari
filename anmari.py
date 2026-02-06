#!/usr/bin/env python3
"""
Anmari - Email cache with selective body storage
"""

from typing import Optional, List, Tuple
import click

from datetime import datetime
from email.utils import formataddr

from config import AccountConfig
from cache import EmailCache
from imap_client import EmailImapClient
from repl import repl as anmari_repl


# Commands
@click.group()
def cli():
    """Anmari - Email cache with selective body storage"""
    pass


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default='INBOX', help='Folder to sync')
@click.option('--page-size', default=100, help='Page size for fetching')
def sync(account: int, folder: str, page_size: int):
    """Sync emails from IMAP to local cache"""
    config = AccountConfig(account)

    # Initialize cache and email client
    cache = EmailCache(account, config.get('cache_days', 90))
    imap_host, imap_port, email_addr = config.get('imap_host'), config.get('imap_port'), config.get('email')
    password = config.get_password()
    email_client = EmailImapClient(imap_host, imap_port, email_addr, password, cache)

    email_client.sync_from_server(folder, page_size)


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default='INBOX', help='Folder to search')
@click.option('--limit', '-l', default=20, help='Limit results')
@click.option('--all', is_flag=True, help='Show all results')
@click.argument('query', nargs=-1, required=True)
def search(account: int, folder: str, limit: int, all: bool, query: tuple):
    """Search emails in local cache"""
    config = AccountConfig(account)

    # Initialize cache
    cache = EmailCache(account, config.get('cache_days', 90))

    # Search
    results = cache.search(folder, query)

    display_limit = len(results) if all else limit

    click.echo(f"Found {len(results)} messages in cache:")
    for msg in results[:display_limit]:
        date = msg.date
        from_display = f'{formataddr((msg.from_name, msg.from_addr))}' if msg.from_name else msg.from_addr
        gm_labels = cache.get_gm_labels(msg.uid, folder)
        display = f"  [{msg.uid}] {date} {from_display} - {msg.subject}"
        if gm_labels:
            display += f"  [labels: {gm_labels}]"
        click.echo(display)

    if len(results) > display_limit:
        click.echo(f"  ... and {len(results) - display_limit} more")


@cli.command()
def repl():
    # TODO Prevent repl from invoking a new repl instance
    anmari_repl(cli)


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default='INBOX', help='Folder to apply tags')
@click.argument('tags_and_query', nargs=-1, required=True)
def tag(account: int, folder: str, tags_and_query: tuple):
    """Apply local tags to messages matching a query

    Usage: tag [--] +tag1 -tag2 <query>

    Examples:
      tag +newsletter from:Instagram
      tag +important -inbox subject:meeting
      tag -- -spam +inbox from:boss
      tag -- -actionable +reference from:"Bank of America" subject:"transaction exceeds"
    
    Note: Use -- before query if it starts with - to prevent option parsing.
    """
    config = AccountConfig(account)
    cache = EmailCache(account, config.get('cache_days', 90))

    # Parse tags and query
    tags_to_add = []
    tags_to_remove = []
    query_parts = []

    for part in tags_and_query:
        if part.startswith('+'):
            tags_to_add.append(part[1:])
        elif part.startswith('-'):
            tags_to_remove.append(part[1:])
        else:
            query_parts.append(part)

    if not tags_to_add and not tags_to_remove:
        click.echo("Error: No tags specified. Use +tag to add, -tag to remove", err=True)
        return

    if not query_parts:
        click.echo("Error: No search query specified", err=True)
        return

    search_results = cache.search(folder, query_parts)

    # Apply tags
    count = cache.tag_messages(search_results, tags_to_add, tags_to_remove or None)

    add_str = f"+{', +'.join(tags_to_add)}" if tags_to_add else ""
    remove_str = f"-{', -'.join(tags_to_remove)}" if tags_to_remove else ""
    tags_str = f"{add_str} {remove_str}".strip()

    # TODO display number of messages that had their tags actually changed
    click.echo(f"Tagged {count} messages with {tags_str}")


if __name__ == '__main__':
    cli()
