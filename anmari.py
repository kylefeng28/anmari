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
from utils import decode_if_bytes


DEFAULT_FOLDER = 'INBOX'
DEFAULT_CACHE_DAYS = 90

# Commands
@click.group()
def cli():
    """Anmari - Email cache with selective body storage"""
    pass


@cli.group()
def cache():
    pass


@cache.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--days', type=int, help='Number of days to clean up (e.g. 3 will clean up the last 3 days)')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help="Folder to clean up")
@click.option('--all-folders', is_flag=True, help='Sync all folders')
def clear(account: int, days: int, folder: str, all_folders: bool):
    """
    Clean up the most recent messages from cache
    """

    # Initialize cache and email client
    config = AccountConfig(account)
    cache = EmailCache(account, config.get('cache_days', DEFAULT_CACHE_DAYS))
    count = cache.cleanup_recent(days, folder, all_folders, interactive=True)

    if count > 0:
        click.echo(f"Deleted {count} messages from the most {days} days")
    else:
        click.echo(f"No messages newer than {days} days found")


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder to sync')
@click.option('--page-size', type=int, default=100, help='Page size for fetching')
@click.option('--all-folders', is_flag=True, help='Sync all folders')
def sync(account: int, folder: str, page_size: int, all_folders: bool):
    """Sync emails from IMAP to local cache"""

    # Initialize cache and email client
    config = AccountConfig(account)
    cache = EmailCache(account, config.get('cache_days', DEFAULT_CACHE_DAYS))
    imap_host, imap_port, email_addr = config.get('imap_host'), config.get('imap_port'), config.get('email')
    password = config.get_password()
    email_client = EmailImapClient(imap_host, imap_port, email_addr, password, cache)

    if all_folders:
        # Get list of all folders
        folders = email_client.list_folders()
        folder_names = [name for flags, delimiter, name in folders]

        click.echo(f"Syncing {len(folder_names)} folders...")
        for folder_name in folder_names:
            click.echo(f"\n{'='*60}")
            click.echo(f"Syncing folder: {folder_name}")
            click.echo('='*60)
            try:
                email_client.sync_from_server(folder_name, page_size)
            except Exception as e:
                click.echo(f"Error syncing {folder_name}: {e}", err=True)
                continue
    else:
        email_client.sync_from_server(folder, page_size)

    email_client.close()


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder to search')
@click.option('--limit', '-l', type=int, default=20, help='Limit results')
@click.option('--all', is_flag=True, help='Show all results')
@click.argument('query', nargs=-1, required=True)
def search(account: int, folder: str, limit: int, all: bool, query: tuple):
    """Search emails in local cache"""
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime
    
    # Initialize cache
    config = AccountConfig(account)
    cache = EmailCache(account, config.get('cache_days', 90))

    # Search
    results = cache.search(folder, query)

    display_limit = len(results) if all else limit

    # Create table
    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="red", width=8)
    table.add_column("FLAGS", width=6)
    table.add_column("SUBJECT", style="green", no_wrap=False)
    table.add_column("FROM", style="blue", width=35)
    table.add_column("DATE", style="yellow", width=20)
    table.add_column("", width=20)

    for msg in results[:display_limit]:
        # Format flags
        flags = "*" if "\\Seen" not in msg.flags else ""

        # Format from
        from_display = msg.from_name if msg.from_name else msg.from_addr

        # Format date
        try:
            date_obj = datetime.fromisoformat(msg.date)
            date_str = date_obj.strftime("%Y-%m-%d %H:%M")
        except:
            date_str = str(msg.date)

        # Truncate subject if too long
        subject = msg.subject
        if len(subject) > 60:
            subject = subject[:57] + "..."

        # Labels
        labels = '\n'.join(cache.get_gm_labels(msg.uid, folder))

        # Tags
        tags = '+' + ', +'.join(cache.get_tags(msg.uid, folder))

        table.add_row(
            str(msg.uid),
            flags,
            subject,
            from_display,
            date_str,
            '\n\n'.join([labels, tags]),
        )

    console.print(f"\nFound {len(results)} messages in cache:")
    console.print(table)

    if len(results) > display_limit:
        console.print(f"... and {len(results) - display_limit} more")


@cli.command()
def repl():
    # TODO Prevent repl from invoking a new repl instance
    anmari_repl(cli)


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder to apply tags')
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


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
def folders(account: int):
    """List all folders/mailboxes"""
    config = AccountConfig(account)

    # Initialize email client
    imap_host, imap_port, email_addr = config.get('imap_host'), config.get('imap_port'), config.get('email')
    password = config.get_password()
    cache = EmailCache(account, config.get('cache_days', 90))
    email_client = EmailImapClient(imap_host, imap_port, email_addr, password, cache)

    folders_list = email_client.list_folders()

    click.echo(f"Folders for {email_addr}:")
    for flags, delimiter, name in folders_list:
        flag_str = ', '.join([decode_if_bytes(f) for f in flags])
        click.echo(f"  {name}")
        if flag_str:
            click.echo(f"    Flags: {flag_str}")

    click.echo(f"\nTotal: {len(folders_list)} folders")

    email_client.close()


if __name__ == '__main__':
    cli()
