from imapclient import IMAPClient
import email
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime, timedelta
from typing import NamedTuple
import click

FLAGS, FLAGS_b = 'FLAGS', b'FLAGS'

# Fetch the message data by UID, including flags and internal date
#  'RFC822' will read the message body and mark it as read; we don't want this
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
BODY_PEEK_HEADER = 'BODY.PEEK[HEADER]'
BODY_HEADER, BODY_HEADER_b = 'BODY[HEADER]', b'BODY[HEADER]'

ENVELOPE, ENVELOPE_b = 'ENVELOPE', b'ENVELOPE'

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
            parts.append(content.decode(encoding or 'utf-8', errors='ignore'))
        else:
            parts.append(content)
    return " ".join(parts)


def decode_if_bytes(maybe_bytes):
    if isinstance(maybe_bytes, bytes):
        return maybe_bytes.decode()
    else:
        return str(maybe_bytes)


def parse_flags(data):
    return [decode_if_bytes(flag) for flag in data[FLAGS_b]]


# Convert imapclient.response_types.Envelope into our own Envelope type
def parse_envelope(data):
    envelope_dto = data[ENVELOPE_b]
    from_ = envelope_dto.from_
    from_addr, from_name = None, None
    if from_:
        from_addr, from_name, = from_[0].host, from_[0].mailbox
    subject = envelope_dto.subject or ''
    date_ts = envelope_dto.date

    return Envelope(from_name, from_addr, subject, date_ts)


class Envelope(NamedTuple):
    from_addr: str
    from_name: str
    subject: str
    date_ts: datetime


class EmailImapClient:
    def __init__(self, host, port, email_addr, password, cache):
        self.email_addr = email_addr
        self.cache = cache
        self.client = IMAPClient(host, port=port, ssl=True)
        print(f'Logging in as {email_addr} to IMAP server {host}:{port}')
        self.client.login(email_addr, password)

    def close(self):
        self.client.logout()

    def select_folder(self, folder):
        self.client.select_folder(folder, readonly=True)

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

        last_seen_uid = self.cache.get_last_seen_uid(folder)

        self.select_folder(folder)

        # Step 1: We search for all UIDs greater than the last seen UID to get new messages
        # #UID FETCH <lastseenuid+1>:* <descriptors>'
        # TODO print timestamp of last sync
        def _fetch_new_messages():
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
                return []

            # Fetch details for each new message
            for messages in self.fetch_paginate(new_uid_list, page_size, [FLAGS, ENVELOPE]):
                envelopes = []
                for uid, data in messages.items():
                    flags = parse_flags(data)
                    envelope = parse_envelope(data)
                    envelopes.append((uid, flags, envelope))

                yield envelopes

        def _fetch_old_messages():
            # Step 2: We search for all UIDs before the last seen UID to get flag changes to old messages
            # UID FETCH 1:<lastseenuid> FLAGS
            # TODO print timestamp of last sync
            # This is assuming no CONDSTORE support
            print(f"Step 2: Checking changes to old messages since last sync (uid {last_seen_uid})")

            old_uid_list = self.get_uids(f'UID 1:{last_seen_uid}')

            if not old_uid_list:
                print("No old messages to check")
                return []

            messages = self.client.fetch(old_uid_list, [FLAGS])

            results = []
            for uid, data in messages.items():
                flags = parse_flags(data)
                results.append((uid, flags))

            print(f"Will check {len(results)} old messages that have potentially changed.")
            return results

        def _update_cache_new_messages(new_messages):
            print('Updating cache for new messages')

            new = 0
            for (uid, flags, envelope) in new_messages:
                cached = self.cache.get_message(uid, folder)
                if not cached:
                    print(f'[debug] inserting {uid}')
                    self.cache.insert_message(
                        uid,
                        folder,
                        envelope.from_addr,
                        envelope.from_name,
                        envelope.subject,
                        envelope.date_ts,
                        flags)
                    new += 1

            print(f'Added {new} new messages to cache')
            return new

        def _update_cache_old_messages(old_messages):
            print('Updating cache for old messages')

            updated = 0
            for (uid, flags) in old_messages:
                cached = self.cache.get_message(uid, folder)
                if cached:
                    cached_flags = cached.get_flags_as_list()
                    if cached_flags != flags:
                        print(f"Updating UID {uid} in cache")
                        print(f"Old flags: {cached_flags}")
                        print(f"New flags: {flags}")

                        self.cache.update_flags(uid, folder, flags)
                        updated += 1
                else:
                    click.echo(f"UID {uid} not found in cache!")
                    print(flags)

            print(f'Added {updated} new messages to cache')
            return updated

        total_new = 0
        for new_messages_page in _fetch_new_messages():
            total_new += _update_cache_new_messages(new_messages_page)

        if last_seen_uid:
            total_updated = 0
            old_messages = _fetch_old_messages()
            total_updated += _update_cache_old_messages(old_messages)

        # Detect expunged
        total_expunged = 0
        #     if last_seen_uid:
        #         cached_uids = self.cache.get_all_uids(folder)
        #         for uid in cached_uids:
        #             if uid not in server_uids:
        #                 self.cache.delete_message(uid, folder)
        #                 total_expunged += 1


        if last_seen_uid:
            print(f"\nSync complete! New: {total_new}, Updated: {total_updated}, Expunged: {total_expunged}")
        else:
            print(f"\nInitial sync complete! New: {total_new}")
