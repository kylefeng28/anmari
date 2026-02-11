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
from action_queue import ActionQueue, QueuedAction
from sync_manager import SyncManager
from repl import repl as anmari_repl, PipeContext
from utils import decode_if_bytes


DEFAULT_FOLDER = 'INBOX'
DEFAULT_CACHE_DAYS = 90

SEEN = '\\Seen'

GMAIL_ALL_MAIL = '[Gmail]/All Mail'

pass_pipe_ctx = click.make_pass_decorator(PipeContext, ensure=True)


# Commands
@click.group()
def cli():
    """Anmari - Email cache with selective body storage"""
    pass


@cli.group()
def cache():
    pass

def init_config(account):
    return AccountConfig(account)


def init_cache(account, config):
    cache = EmailCache(account, config.get('cache_days', DEFAULT_CACHE_DAYS))
    return cache


def init_email_client(account, config, cache):
    return EmailImapClient(
        host=config.get('imap_host'),
        port=config.get('imap_port'),
        email_addr=config.get('email'),
        password=config.get_password(),
        cache=cache)


@cache.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--days', type=int, help='Number of days to clean up (e.g. 3 will clean up the last 3 days)')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help="Folder to clean up")
@click.option('--all-folders', is_flag=True, help='Sync all folders')
def clear(account: int, days: int, folder: str, all_folders: bool):
    """
    Clean up the most recent messages from cache
    """

    # Initialize cache
    config = init_config(account)
    cache = init_cache(account, config)
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
@click.option('--workers', '-n', type=int, default=4, help='Number of concurrent workers for --all-folders')
def sync(account: int, folder: str, page_size: int, all_folders: bool, workers: int):
    """Sync emails from IMAP to local cache"""

    # Initialize email client
    config = init_config(account)
    cache = init_cache(account, config)
    email_client = init_email_client(account, config, cache)

    if all_folders:
        # Get list of all folders
        folders = email_client.list_folders()
        folder_names = [name for flags, delimiter, name in folders if b'\\HasNoChildren' in flags ]

        click.echo(f"Syncing {len(folder_names)} folders with {workers} workers...")

        # Use threaded sync manager
        sync_manager = SyncManager(max_workers=workers)
        sync_manager.sync_all_folders(config, account, folder_names, page_size)

        # Print summary
        sync_manager.print_summary()
    else:
        email_client.sync_from_server(folder, page_size)

    email_client.close()


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder to search')
@click.option('--limit', '-l', type=int, default=20, help='Limit results')
@click.option('--all', is_flag=True, help='Show all results')
@click.argument('query', nargs=-1, required=True)
@pass_pipe_ctx
def search(pipe_ctx: PipeContext, account: int, folder: str, limit: int, all: bool, query: tuple):
    """Search emails in local cache"""
    from rich.console import Console
    from rich.table import Table
    from datetime import datetime

    pipe_ctx.query = query

    # Initialize cache
    config = init_config(account)
    cache = init_cache(account, config)

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
        tags += msg.flags

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

    # Initialize cache
    config = init_config(account)
    cache = init_cache(account, config)

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
    # Initialize email client
    config = init_config(account)
    email_client = init_email_client(account, config, cache=None)

    folders_list = email_client.list_folders()

    click.echo(f"Folders for {config.get('email')}:")
    for flags, delimiter, name in folders_list:
        flag_str = ', '.join([decode_if_bytes(f) for f in flags])
        click.echo(f"  {name}")
        if flag_str:
            click.echo(f"    Flags: {flag_str}")

    click.echo(f"\nTotal: {len(folders_list)} folders")

    email_client.close()

# Action Queue Commands
@cli.group()
def queue():
    """Manage action queue (git-like staging for IMAP operations)"""
    pass


def _queue_move(account: int, folder: str, to: str, query: tuple):
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    # Search to get message count
    results = cache.search(folder, query)

    # Queue action
    action_id = action_queue.queue_action(
        query=query,
        folder=folder,
        action_type='move',
        action_data={'dest': to},
        message_count=len(results)
    )

    click.echo(f'Queued action #{action_id}: {action_queue.get_action(action_id).describe()}')


@queue.command('move')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Source folder')
@click.option('--to', required=True, help='Destination folder')
@click.argument('query', nargs=-1, required=True)
def queue_move(account: int, folder: str, to: str, query: tuple):
    """Queue move operation"""
    _queue_move(account, folder, to, query)

@queue.command('archive')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Source folder')
@click.argument('query', nargs=-1, required=True)
def queue_archive(account: int, folder: str, query: tuple):
    """Queue archive operation (Gmail only)"""
    _queue_move(account, folder, GMAIL_ALL_MAIL, query)


def _queue_flag(account: int, folder: str, add: tuple, remove: tuple, query: tuple):
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    results = cache.search(folder, query)

    # Normalize flags (add backslash if not present)
    def normalize_flag(f):
        return f if f.startswith('\\') else f'\\{f}'

    if add:
        flags = [normalize_flag(f) for f in add]
        action_id = action_queue.queue_action(
            query=query,
            folder=folder,
            action_type='add_flag',
            action_data={'flags': flags},
            message_count=len(results)
        )

    if remove:
        flags = [normalize_flag(f) for f in remove]
        action_id = action_queue.queue_action(
            query=query,
            folder=folder,
            action_type='remove_flag',
            action_data={'flags': flags},
            message_count=len(results)
        )

    click.echo(f'Queued action #{action_id}: {action_queue.get_action(action_id).describe()}')


@queue.command('flag')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder')
@click.option('--add', multiple=True, help='Flags to add (e.g., Seen, Flagged)')
@click.option('--remove', multiple=True, help='Flags to remove')
@click.argument('query', nargs=-1, required=True)
def queue_flag(account: int, folder: str, add: tuple, remove: tuple, query: tuple):
    """Queue flag operation"""
    _queue_flag(account, folder, add, remove, query)


@queue.command('markread')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder')
@click.argument('query', nargs=-1, required=True)
@pass_pipe_ctx
def queue_markread(ctx, account: int, folder: str, query: tuple):
    """Alias for: queue flag --add \\Seen"""
    _queue_flag(account, folder, [SEEN], [], query)


@queue.command('markunread')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder')
@click.argument('query', nargs=-1, required=True)
def queue_markunread(account: int, folder: str, query: tuple):
    """Alias for: queue flag --remove \\Seen"""
    _queue_flag(account, folder, [], [SEEN], query)


@queue.command('label')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--folder', '-f', default=DEFAULT_FOLDER, help='Folder')
@click.option('--add', multiple=True, help='Labels to add')
@click.option('--remove', multiple=True, help='Labels to remove')
@click.argument('query', nargs=-1, required=True)
def queue_label(account: int, folder: str, add: tuple, remove: tuple, query: tuple):
    """Queue Gmail label operation"""
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    results = cache.search(folder, query)

    if add:
        action_id = action_queue.queue_action(
            query=query,
            folder=folder,
            action_type='add_label',
            action_data={'labels': list(add)},
            message_count=len(results)
        )

    if remove:
        action_id = action_queue.queue_action(
            query=query,
            folder=folder,
            action_type='remove_label',
            action_data={'labels': list(remove)},
            message_count=len(results)
        )

    click.echo(action_queue.get_action(action_id).describe())


@queue.command('clear')
@click.option('--account', '-a', default=0, help='Account index')
def queue_clear(account: int):
    """Clear all pending actions"""
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    action_queue.clear_pending()
    click.echo("Cleared all pending actions")


@queue.command('undo')
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--count', '-n', default=1, help='Number of actions to undo')
def queue_undo(account: int, count: int):
    """Undo last N pending actions"""
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    removed = action_queue.undo_last(count)
    click.echo(f"Removed {removed} action(s) from queue")


@queue.command('status')
@click.option('--account', '-a', default=0, help='Account index')
def queue_status(account: int):
    """Show pending actions in queue"""
    from rich.console import Console
    from rich.table import Table

    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    actions = action_queue.get_pending_actions()

    if not actions:
        click.echo("No pending actions")
        return

    console = Console()
    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="cyan", width=6)
    table.add_column("Action", style="green")
    table.add_column("Created", style="yellow", width=20)

    for action in actions:
        table.add_row(
            str(action.id),
            action.describe(),
            action.created_at
        )

    console.print(f"\nPending Actions ({len(actions)}):")
    console.print(table)


@cli.command()
@click.option('--account', '-a', default=0, help='Account index')
@click.option('--dry-run', is_flag=True, help='Preview without executing')
@click.option('--id', 'action_id', type=int, help='Apply specific action by ID')
def apply(account: int, dry_run: bool, action_id: Optional[int]):
    """Apply pending actions to IMAP server"""
    config = init_config(account)
    cache = init_cache(account, config)
    action_queue = ActionQueue(cache)

    # Get actions to apply
    if action_id:
        action = action_queue.get_action(action_id)
        if not action:
            click.echo(f"Action #{action_id} not found", err=True)
            return
        actions = [action]
    else:
        actions = action_queue.get_pending_actions()

    if not actions:
        click.echo("No pending actions to apply")
        return

    if dry_run:
        click.echo("DRY RUN - No changes will be made\n")
    else:
        click.confirm(f'This will run {len(actions)} actions. Are you sure you want to proceed?', abort=True)

    # Connect to IMAP
    if config.get('provider') != 'imap':
        raise click.UsageError('Can only handle IMAP!')
    email_client = init_email_client(account, config, cache)

    affected_folders = set()

    succeeded, failed, skipped = 0, 0, 0
    modified = 0
    for action in actions:
        click.echo(f"[{action.id}] {action.describe()}")

        try:
            # Re-run query to get current UIDs
            results = cache.search(action.folder, action.query)
            uids = [msg.uid for msg in results]

            if not uids:
                click.echo(f"  ⚠️  No messages match query anymore, skipping")
                skipped += action_queue.mark_applied(action.id)
                continue

            if len(uids) != action.message_count:
                click.echo(f"  ⚠️  Number of messages matching query previously ({action.message_count}) differs from current ({len(uids)})")
                if not click.confirm('Do you still want to proceed? '):
                    skipped += action_queue.mark_applied(action.id)
                    continue

            if dry_run:
                click.echo(f"  ✓ Dry run '{action.action_type}' on {len(uids)} messages in folder {action.folder}")
                succeeded += 1
                continue

            # Execute action
            if action.action_type == 'move':
                email_client.move_messages(uids, action.folder, action.action_data['dest'])
                affected_folders.add(action.folder)
                affected_folders.add(action.action_data['dest'])
                click.echo(f"  ✓ Moved {len(uids)} messages")

            elif action.action_type == 'add_flag':
                email_client.add_flags(uids, action.folder, action.action_data['flags'])
                affected_folders.add(action.folder)
                click.echo(f"  ✓ Added flags to {len(uids)} messages")

            elif action.action_type == 'remove_flag':
                email_client.remove_flags(uids, action.folder, action.action_data['flags'])
                affected_folders.add(action.folder)
                click.echo(f"  ✓ Removed flags from {len(uids)} messages")

            elif action.action_type == 'add_label':
                email_client.add_gmail_labels(uids, action.folder, action.action_data['labels'])
                affected_folders.add(action.folder)
                click.echo(f"  ✓ Added labels to {len(uids)} messages")

            elif action.action_type == 'remove_label':
                email_client.remove_gmail_labels(uids, action.folder, action.action_data['labels'])
                affected_folders.add(action.folder)
                click.echo(f"  ✓ Removed labels from {len(uids)} messages")

            succeeded += action_queue.mark_applied(action.id)
            modified += len(uids)

        except Exception as e:
            click.echo(f"  ✗ Failed: {e}", err=True)
            import traceback
            traceback.print_exc()
            action_queue.mark_failed(action.id)

    if not dry_run and affected_folders:
        click.echo(f"\nSyncing {len(affected_folders)} affected folder(s)...")
        for folder in affected_folders:
            try:
                email_client.sync_from_server(folder, page_size=100)
            except Exception as e:
                click.echo(f"  ✗ Failed to sync {folder}: {e}", err=True)

    email_client.close()

    click.echo()
    click.echo(f'Done! Total actions: {len(actions)}, Skipped: {skipped}, Succeeded: {succeeded}, Failed: {failed}')
    click.echo(f'Total messages modified: {modified}')


if __name__ == '__main__':
    cli()
