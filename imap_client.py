from imapclient import IMAPClient
import email
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime, timedelta
from dataclasses import dataclass
import click

FLAGS = 'FLAGS'

# Fetch the message data by UID, including flags and internal date
#  'RFC822' will read the message body and mark it as read; we don't want this
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
BODY_PEEK_HEADER = 'BODY.PEEK[HEADER]'
BODY_HEADER = 'BODY[HEADER]'

ENVELOPE = 'ENVELOPE'

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
    return [decode_if_bytes(flag) for flag in data[b'FLAGS']]


def parse_envelope(data, descriptor_name):
    raw_email = data[descriptor_name.encode()]

    # Parse envelope
    msg = email.message_from_bytes(raw_email)
    # Parse email address into (name, addr)
    from_name, from_addr = parseaddr(msg.get('From', ''))
    subject = decode_header_value(msg.get('Subject', ''))
    date_str = msg.get('Date', '')

    # Parse date
    try:
        date_tuple = email.utils.parsedate_to_datetime(date_str)
        date_ts = int(date_tuple.timestamp())
    except _:
        date_ts = int(datetime.now().timestamp())

    return Envelope(from_name, from_addr, subject, date_ts)

@dataclass
class Envelope:
    from_name: str
    from_addr: str
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

    def get_uids(self, uid_range):
        """Get UIDs in range (e.g., '123:*' or '1:500')"""
        return self.client.search(f'UID {uid_range}')

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
            print(f"Step 1: Fetching new messages since last sync (uid {last_seen_uid})")

            new_last_seen_uid = None
            new_uid_list = self.get_uids(f'{last_seen_uid+1}:*')

            if len(new_uid_list) > 0:
                print(f"Found {len(new_uid_list)} new messages.")
                new_last_seen_uid = int(new_uid_list[-1]) # The highest UID is the new last seen
            else:
                print("No new messages found")
                return []

            # Fetch details for each new message
            envelopes = []
            messages = self.client.fetch(new_uid_list, [FLAGS, BODY_PEEK_HEADER])

            for uid, data in messages.items():
                # Get flags
                flags = ' '.join(flag.decode() if isinstance(flag, bytes) else str(flag) 
                               for flag in data[b'FLAGS'])

                # Parse envelope
                envelope = parse_envelope(data, BODY_HEADER)
                envelopes.append((uid, flags, envelope))

            return envelopes

        def _fetch_old_messages():
            # Step 2: We search for all UIDs before the last seen UID to get flag changes to old messages
            # UID FETCH 1:<lastseenuid> FLAGS
            # TODO print timestamp of last sync
            # This is assuming no CONDSTORE support
            print(f"Step 2: Checking changes to old messages since last sync (uid {last_seen_uid})")

            old_uid_list = self.get_uids(f'1:{last_seen_uid}')

            if not old_uid_list:
                print("No old messages to check")
                return []

            messages = self.client.fetch(old_uid_list, ['FLAGS'])

            results = []
            for uid, data in messages.items():
                flags = ' '.join(flag.decode() if isinstance(flag, bytes) else str(flag) 
                               for flag in data[b'FLAGS'])
                results.append((uid, flags))

            print(f"Will check {len(results)} old messages that have potentially changed.")
            return results

        new_messages = _fetch_new_messages()

        print('Updating cache for new')
        total_new = 0
        for (uid, flags, envelope) in new_messages:
            cached = self.cache.get_message(uid, folder)
            if not cached:
                print(f'inserting {uid}')
                self.cache.insert_message(
                    uid,
                    folder,
                    envelope.from_addr,
                    envelope.from_name,
                    envelope.subject,
                    envelope.date_ts,
                    flags)
                total_new += 1
        print(f'Added {total_new} new messages to cache')

        old_messages = _fetch_old_messages()
        total_updated = 0

        print('Updating cache for old')
        for (uid, flags) in old_messages:
            cached = self.cache.get_message(uid, folder)
            if cached:
                if cached['flags'] != flags:
                    print(f"Updating UID {uid} in cache")
                    print(f"Old flags: {cached['flags']}")
                    print(f"New flags: {flags}")

                    # self.cache.update_flags(uid, folder, flags)
                    total_updated += 1
            else:
                click.echo(f"UID {uid} not found in cache!")
                self.cache.insert_message(
                    uid,
                    folder,
                    envelope.from_addr,
                    envelope.from_name,
                    envelope.subject,
                    envelope.date_ts,
                    flags)

        # # Detect expunged
        total_expunged = 0
        #     if last_seen_uid:
        #         cached_uids = self.cache.get_all_uids(folder)
        #         for uid in cached_uids:
        #             if uid not in server_uids:
        #                 self.cache.delete_message(uid, folder)
        #                 total_expunged += 1


        print(f"\nSync complete! New: {total_new}, Updated: {total_updated}, Expunged: {total_expunged}")
