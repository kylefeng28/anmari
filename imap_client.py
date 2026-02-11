from imapclient import IMAPClient
import email
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime, timedelta
from typing import NamedTuple
import click
from contextlib import contextmanager

from utils import decode_if_bytes

FLAGS, FLAGS_b = 'FLAGS', b'FLAGS'

# Fetch the message data by UID, including flags and internal date
#  'RFC822' will read the message body and mark it as read; we don't want this
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
BODY_PEEK_HEADER = 'BODY.PEEK[HEADER]'
BODY_HEADER, BODY_HEADER_b = 'BODY[HEADER]', b'BODY[HEADER]'

ENVELOPE, ENVELOPE_b = 'ENVELOPE', b'ENVELOPE'

X_GM_LABELS, X_GM_LABELS_b = 'X-GM-LABELS', b'X-GM-LABELS'

# imaplib reference
# FETCH by UID: imap.uid('FETCH', uid_str, descriptor) -> status, data
#     status, data = imap.uid('fetch', uid_range, '(BODY.PEEK)')
#     # output: data = [('1 (UID 123 BODY[...])', b'...'), b')']
#     data is a list of tuples separated by ')' (string with a single closing parenthesis)
#     Each tuple is (message_parts, raw_email_bytes)
def decode_header_value(value):
    """Decode email header value"""
    if not value:
        return ""
    decoded = decode_header(value)
    parts = []
    for content, encoding in decoded:
        if isinstance(content, bytes):
            if encoding == 'unknown-8bit' or not encoding:
                encoding = 'utf-8'
            parts.append(content.decode(encoding, errors='ignore'))
        else:
            parts.append(content)
    return " ".join(parts)


def parse_flags(data):
    return [decode_if_bytes(flag) for flag in data[FLAGS_b]]


def parse_gm_labels(is_gmail, data):
    if is_gmail and X_GM_LABELS_b in data:
        return sorted([decode_if_bytes(label) for label in data[X_GM_LABELS_b]])
    return None

# Convert imapclient.response_types.Envelope into our own Envelope type
def parse_envelope(data):
    envelope_dto = data[ENVELOPE_b]
    from_ = str(envelope_dto.from_[0])
    from_name, from_addr, = parseaddr(from_)
    subject = envelope_dto.subject or ''
    date = envelope_dto.date

    return Envelope(from_addr, from_name, subject, date)


# Convert imapclient.response_types.Envelope into our own Envelope type
def parse_header(data):
    # Parse envelope
    msg = email.message_from_bytes(data[BODY_HEADER_b])
    # Parse email address into (name, addr)
    from_name, from_addr = parseaddr(msg.get('From', ''))
    subject = decode_header_value(msg.get('Subject', ''))
    date_str = msg.get('Date', '')

    # Parse date
    # TODO: figure out what to do if we can't parse the date.
    # Currently defaulting to serializing as 0 in sqlite
    try:
        date = email.utils.parsedate_to_datetime(date_str)
    except:
        date = None
    return Envelope(from_addr, from_name, subject, date)


class Envelope(NamedTuple):
    from_addr: str
    from_name: str
    subject: str
    date: datetime


class EmailImapClient:
    def __init__(self, host, port, email_addr, password, cache):
        self.email_addr = email_addr
        self.cache = cache
        self.client = IMAPClient(host, port=port, ssl=True)
        print(f'Logging in as {email_addr} to IMAP server {host}:{port}')
        self.client.login(email_addr, password)

        # print(f'[debug] Capabilities: {self.client.capabilities()}')

        # Gmail IMAP Capabilities:
        # ('IMAP4REV1', 'UNSELECT', 'IDLE', 'NAMESPACE', 'QUOTA', 'ID', 'XLIST', 'CHILDREN', 'X-GM-EXT-1', 'UIDPLUS', 'COMPRESS=DEFLATE', 'ENABLE', 'MOVE', 'CONDSTORE', 'ESEARCH', 'UTF8=ACCEPT', 'LIST-EXTENDED', 'LIST-STATUS', 'LITERAL-', 'SPECIAL-USE', 'APPENDLIMIT=35651584')

        self.has_move = self.client.has_capability('MOVE')

        # Check and enable CONDSTORE capability
        self.has_condstore = self.client.has_capability('CONDSTORE')
        if self.has_condstore:
            self.client.enable('CONDSTORE')
        print(f'CONDSTORE support: {self.has_condstore}')

        # Check Gmail capability (X-GM-EXT-1)
        self.is_gmail = self.client.has_capability('X-GM-EXT-1')
        print(f'Gmail support: {self.is_gmail}')

    def close(self):
        self.client.logout()

    def list_folders(self):
        """List all folders/mailboxes

        Returns:
            List of (flags, delimiter, name) tuples
        """
        folders = self.client.list_folders()
        return folders

    def select_folder(self, folder):
        """Select folder and return (uidvalidity, highestmodseq)"""
        response = self.client.select_folder(folder, readonly=True)
        uidvalidity = response[b'UIDVALIDITY']
        highestmodseq = response.get(b'HIGHESTMODSEQ', 0) if self.has_condstore else 0
        return uidvalidity, highestmodseq

    def get_unread_messages(self):
        return self.client.search('UNSEEN')

    def get_uids(self, cond='ALL'):
        """Get UIDs. e.g. ALL or a range (e.g., 'UID 123:*' or 'UID 1:500')"""
        return self.client.search(f'{cond}')

    def fetch_paginate(self, uids: list[int], page_size: int, descriptors: list[str], modifiers: list[str] = None):
        uid_pages = [uids[i:i + page_size] for i in range(0, len(uids), page_size)]
        i = 0
        print(f'Starting fetch for {len(uids)} items, using page_size={page_size}')
        for uid_page in uid_pages:
            print(f'Fetching page {i} ({len(uid_page)} items)')
            page_messages = self.client.fetch(uid_page, descriptors, modifiers)
            yield page_messages
            i += 1

    # RFC 4549 sync algorithm
    # https://www.rfc-editor.org/rfc/rfc4549#section-3
    def sync_from_server(self, folder, page_size):
        print(f"Syncing {folder} from {self.email_addr}...")

        # Step 1: Check UIDVALIDITY and HIGHESTMODSEQ
        uidvalidity, highestmodseq = self.select_folder(folder)
        print(f'Server UIDVALIDITY: {uidvalidity}, HIGHESTMODSEQ: {highestmodseq}')

        cached_state = self.cache.get_folder_state(folder)

        # Check if UIDVALIDITY changed (folder was deleted/recreated)
        if cached_state:
            cached_uidvalidity, cached_highestmodseq = cached_state
            print(f'Cached UIDVALIDITY: {cached_uidvalidity}, HIGHESTMODSEQ: {cached_highestmodseq}')

            if cached_uidvalidity != uidvalidity:
                print('⚠️  UIDVALIDITY changed! Clearing local cache and starting fresh.')
                self.cache.clear_folder_messages_for_uidvalidity_change(folder)
                cached_state = None
                cached_highestmodseq = 0
        else:
            print(f'No cached HIGHESTMODSEQ found')
            cached_highestmodseq = 0

        last_seen_uid = self.cache.get_last_seen_uid(folder)

        # Step 1: We search for all UIDs greater than the last seen UID to get new messages
        # #UID FETCH <lastseenuid+1>:* <descriptors>'
        # TODO print timestamp of last sync
        def _fetch_new_messages() -> (list[Envelope], list[int]):
            if last_seen_uid:
                print(f"Step 1: Fetching new messages since last sync (uid {last_seen_uid})")
                new_uid_list = self.get_uids(f'UID {last_seen_uid+1}:*')
            else:
                print("No cache detected. Starting full sync.")
                new_uid_list = self.get_uids('ALL')

            if len(new_uid_list) > 0:
                new_uid_list = sorted(map(int, new_uid_list))
                uid_min, uid_max = new_uid_list[0], new_uid_list[-1]
                print(f"Found {len(new_uid_list)} new messages (ranging from {uid_min} to {uid_max}).")
            else:
                print("No new messages found")
                return [], []

            # Fetch details for each new message
            fetch_items = [FLAGS, BODY_PEEK_HEADER]
            if self.is_gmail:
                fetch_items.append(X_GM_LABELS)

            for messages in self.fetch_paginate(new_uid_list, page_size, fetch_items):
                envelopes = []
                for uid, data in messages.items():
                    flags = parse_flags(data)
                    envelope = parse_header(data)
                    gm_labels = parse_gm_labels(self.is_gmail, data)

                    envelopes.append((uid, flags, envelope, gm_labels))

                yield envelopes, new_uid_list

        # Returns list of (potentially) changed messages since last since and a list of all UIDs from 1:last_seen_uid
        def _fetch_old_messages() -> (list[Envelope], list[int]):
            # Step 2: Check flag changes to old messages
            # With CONDSTORE: Use CHANGEDSINCE to only fetch changed messages
            # Without CONDSTORE: Fetch all old messages before last seen UID

            if not last_seen_uid:
                print("No old messages to check (initial sync)")
                return [], []

            # TODO print timestamp of last sync
            print(f"Step 2: Checking flag changes to old messages since last sync")

            # Fetch old uids for both non-CONDSTORE / HIGHESTMODSEQ and for expunge local cache
            # FETCH 1:<lastseenuid> FLAGS
            old_uid_list = self.get_uids(f'UID 1:{last_seen_uid}')

            fetch_items = [FLAGS]
            if self.is_gmail:
                fetch_items.append(X_GM_LABELS)

            # RFC 4549 Section 6.1: Use CONDSTORE / HIGHESTMODSEQ for efficient sync
            if self.has_condstore and cached_highestmodseq > 0:
                if highestmodseq == cached_highestmodseq:
                    print(f"HIGHESTMODSEQ unchanged ({highestmodseq}), skipping flag check")
                    return [], old_uid_list

                print(f"Using CONDSTORE to fetch changes since MODSEQ {cached_highestmodseq}")
                # FETCH 1:* (FLAGS) (CHANGEDSINCE <cached-value>)
                messages = self.client.fetch('1:*', fetch_items, ['CHANGEDSINCE', str(cached_highestmodseq)])

                print(f"Found {len(messages)} messages with flag changes")

            # No CONDSTORE: Fetch all old messages
            else:
                print(f"Checking changes to old messages since last sync (uid {last_seen_uid})")
                if not old_uid_list:
                    print("No old messages to check")
                    return [], []

                print(f"Will check {len(old_uid_list)} old messages that have potentially changed.")

                # FETCH 1:<lastseenuid> FLAGS
                messages = self.client.fetch(old_uid_list, fetch_items)

            results = []
            for uid, data in messages.items():
                flags = parse_flags(data)
                gm_labels = parse_gm_labels(self.is_gmail, data)
                results.append((uid, flags, gm_labels))

            return results, old_uid_list

        def _update_cache_new_messages(new_messages):
            print('Updating cache for new messages')

            new = 0
            for item in new_messages:
                uid, flags, envelope, gm_labels = item

                cached = self.cache.get_message(uid, folder)
                if not cached:
                    # print(f'[debug] inserting {uid}')
                    self.cache.insert_message(
                        uid,
                        folder,
                        envelope.from_addr,
                        envelope.from_name,
                        envelope.subject,
                        envelope.date,
                        flags)

                    # Store Gmail labels if available
                    if self.is_gmail and gm_labels is not None:
                        self.cache.set_gm_labels(uid, folder, gm_labels)

                    new += 1

            print(f'Added {new} new messages to cache')
            return new

        def _update_cache_old_messages(old_messages):
            print('Updating cache for old messages')

            updated = 0
            for (uid, flags, gm_labels) in old_messages:
                cached = self.cache.get_message(uid, folder)
                if cached:
                    cached_flags = cached.get_flags_as_list()
                    if cached_flags != flags:
                        print(f"Updating UID {uid} in cache")
                        print(f"  Old flags: {cached_flags}")
                        print(f"  New flags: {flags}")

                        self.cache.update_flags(uid, folder, flags)
                        updated += 1

                    # Store Gmail labels if available
                    if self.is_gmail:
                        cached_gm_labels = self.cache.get_gm_labels(uid, folder)
                        if cached_gm_labels != gm_labels:
                            print(f'Updating Gmail labels for {uid} in cache')
                            print(f'  Old labels: {cached_gm_labels}')
                            print(f'  New labels: {gm_labels}')
                            self.cache.set_gm_labels(uid, folder, gm_labels)

                else:
                    click.echo(f"UID {uid} not found in cache!")
                    print((flags, gm_labels))
                    a = self.client.fetch([uid], 'BODY.PEEK[HEADER]')
                    print(a)

            print(f'Updated {updated} messages in cache')
            return updated

        total_new = 0
        server_uids = set()  # Track all UIDs seen on server

        for new_messages_page, new_uids in _fetch_new_messages():
            total_new += _update_cache_new_messages(new_messages_page)
            server_uids.update(new_uids)

        total_updated = 0
        if last_seen_uid:
            print()
            old_messages, old_uids = _fetch_old_messages()
            total_updated += _update_cache_old_messages(old_messages)
            server_uids.update(old_uids)

        # Update cached folder state
        self.cache.set_folder_state(folder, uidvalidity, highestmodseq)

        # Detect expunged messages
        total_expunged = 0
        if last_seen_uid:
            print()
            print("Step 3: Detecting expunged messages")
            cached_uids = self.cache.get_all_uids(folder)

            uids_to_expunge = set(cached_uids) - server_uids

            for uid in uids_to_expunge:
                print(f"  Expunging UID {uid} (deleted on server)")
                self.cache.delete_message(uid, folder)
                total_expunged += 1

            if total_expunged == 0:
                print("  No expunged messages")

        if last_seen_uid:
            print(f"\nSync complete! New: {total_new}, Updated: {total_updated}, Expunged: {total_expunged}")
        else:
            print(f"\nInitial sync complete! New: {total_new}")

        # Return stats for threaded sync
        return {
            'new': total_new,
            'updated': total_updated if last_seen_uid else 0,
            'expunged': total_expunged
        }

    ################################################################################
    # WRITE, READONLY=FALSE OPERATIONS
    ################################################################################
    @contextmanager
    def select_folder_write(self, folder):
        """Temporarily select a folder as writable, then restore readonly mode."""
        self.client.select_folder(folder, readonly=False)
        try:
            yield
        finally:
            self.client.select_folder(folder, readonly=True)

    def add_flags(self, uids: list, folder: str, flags: list):
        """Add IMAP flags to messages"""
        with self.select_folder_write(folder):
            # Convert string flags to bytes
            flag_bytes = [f.encode() if isinstance(f, str) else f for f in flags]
            # print(f'[debug] add flags to uids {uids}')
            self.client.add_flags(uids, flag_bytes)

    def remove_flags(self, uids: list, folder: str, flags: list):
        """Remove IMAP flags from messages"""
        with self.select_folder_write(folder):
            flag_bytes = [f.encode() if isinstance(f, str) else f for f in flags]
            # print(f'[debug] remove flags from uids {uids}')
            self.client.remove_flags(uids, flag_bytes)

    def add_gmail_labels(self, uids: list, folder: str, labels: list):
        """Add Gmail labels to messages"""
        if not self.is_gmail:
            raise Exception("Gmail labels not supported on this server")

        with self.select_folder_write(folder):
            # print(f'[debug] add gmail labels to uids {uids}')
            self.client.add_gmail_labels(uids, labels)

    def remove_gmail_labels(self, uids: list, folder: str, labels: list):
        """Remove Gmail labels from messages"""
        if not self.is_gmail:
            raise Exception("Gmail labels not supported on this server")

        with self.select_folder_write(folder):
            # print(f'[debug] remove gmail labels from uids {uids}')
            self.client.remove_gmail_labels(uids, labels)

    def move_messages(self, uids: list, source_folder: str, dest_folder: str):
        """Move messages to another folder (COPY + DELETE + EXPUNGE)"""
        with self.select_folder_write(source_folder):
            # print(f'[debug] move uids {uids} from {source_folder} to {dest_folder}')

            if self.has_move:
                print('Server supports MOVE command')
                self.client.move(uids, dest_folder)

            else:
                print('Server does not support MOVE command; using a COPY + DELETE instead')

                # Copy to destination
                self.client.copy(uids, dest_folder)

                # Mark as deleted
                self.client.delete_messages(uids)

                # Expunge deleted messages
                self.client.expunge()

            # TODO: can optimize by preventing (delete from local cache + download message from server) by using COPYUID
