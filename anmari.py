#!/usr/bin/env python3
"""
Anmari - Email cache with selective body storage
"""

from typing import Optional, List, Tuple
import click

from datetime import datetime

from config import AccountConfig
from cache import EmailCache
from imap_client import EmailImapClient


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
@click.argument('query')
def search(account: int, folder: str, limit: int, all: bool, query: str):
    """Search emails in local cache"""
    config = AccountConfig(account)

    # Initialize cache
    cache = EmailCache(account, config.get('cache_days', 90))

    # Search
    results = cache.search(folder, query)

    display_limit = len(results) if all else limit

    click.echo(f"Found {len(results)} messages in cache:")
    for msg in results[:display_limit]:
        date = datetime.fromtimestamp(msg['date']).strftime('%Y-%m-%d')
        from_display = msg['from_name'] if msg['from_name'] else msg['from_addr']
        click.echo(f"  [{msg['uid']}] {date} {from_display} - {msg['subject']}")

    if len(results) > display_limit:
        click.echo(f"  ... and {len(results) - display_limit} more")


if __name__ == '__main__':
    cli()
