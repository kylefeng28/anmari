import imaplib
import re
import email
from email.header import decode_header
from email.utils import parseaddr
from datetime import datetime, timedelta
from dataclasses import dataclass
import click


# Fetch the message data by UID, including flags and internal date
#  'RFC822' will read the message body and mark it as read; we don't want this
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
# 'RFC822.HEADER' or 'BODY.PEEK[HEADER]' just fetches headers
FETCH_HEADER_DESCRIPTOR = '(FLAGS BODY.PEEK[HEADER])'

# imaplib reference
# FETCH by UID: imap.uid('FETCH', uid_str, descriptor) -> status, data
#     status, data = imap.uid('fetch', uid_range, '(BODY.PEEK)')
#     # output: data = [('1 (UID 123 BODY[...])', b'...'), b')']
#     data is a list of tuples separated by ')' (string with a single closing parenthesis)
#     Each tuple is (message_parts, raw_email_bytes)

# IMAP operations
def connect_imap(host: str, port: int, email_addr: str, password: str):
    """Connect to IMAP server"""
    ssl = imaplib.ssl.SSLContext()
    ssl.load_default_certs()
    imap = imaplib.IMAP4_SSL(host=host, port=port, ssl_context=ssl)
    print(f'Logging in as {email_addr} to IMAP server {host}:{port}')
    imap.login(email_addr, password)
    return imap


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


def parse_uid_flags(metadata_str):
    # \ $
    match = re.search(r'UID\s+(?P<uid>\d+)(\s?FLAGS \((?P<flags>[\\\$\s\w]*)\))', metadata_str)
    uid = match.group('uid')
    flags = match.group('flags')
    # can also use imaplib.ParseFlags()
    return uid, flags

def parse_envelope(raw_email):
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
        self.imap = connect_imap(host, port, email_addr, password)
        self.cache = cache

    def close(self):
        self.imap.close()
        self.imap.logout()

    def select_folder(self, folder):
        self.imap.select(folder, readonly=True)

    def get_unread_messages(self):
        status, data = self.imap.search(None, 'UNSEEN')
        unread_message_nums = data[0].split()
        return unread_message_nums

    def get_uids(self, cond):
        status, data = self.imap.uid('SEARCH', None, cond)
        if status != 'OK':
            raise f'Error getting uids for {cond}'
        return [int(uid) for uid in data[0].split()]

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
            new_uid_list = self.get_uids(f'UID {last_seen_uid+1}:*')

            if len(new_uid_list) > 0:
                print(f"Found {len(new_uid_list)} new messages.")
                new_last_seen_uid = int(new_uid_list[-1]) # The highest UID is the new last seen
            else:
                print("No new messages found")

            # Fetch details for each new message
            envelopes = []

            status, data = self.imap.uid('FETCH', f'{last_seen_uid+1}:*', FETCH_HEADER_DESCRIPTOR)
            if status != 'OK':
                click.echo(f"Could not fetch new messages. status={status}, data={data}", err=True)
                raise 'Error fetching new messages'

            for msg_tuple in data:  # loop through list of tuples
                if not isinstance(msg_tuple, tuple):
                    continue

                # Parse flags
                part = msg_tuple[0].decode('utf-8', errors='ignore')
                uid, flags = parse_uid_flags(part)

                # Parse envelope
                raw_email = msg_tuple[1]
                envelope = parse_envelope(raw_email)
                envelopes.append((uid, flags, envelope))

            return envelopes

        def _fetch_old_messages():
            # Step 2: We search for all UIDs before the last seen UID to get flag changes to old messages
            # UID FETCH 1:<lastseenuid> FLAGS
            # TODO print timestamp of last sync
            # This is assuming no CONDSTORE support
            print(f"Step 2: Checking changes to old messages since last sync (uid {last_seen_uid})")

            status, data = self.imap.uid('FETCH', f'1:{last_seen_uid}', 'FLAGS')
            if status != 'OK':
                click.echo(f"Could not fetch old messages. status={status}, data={data}", err=True)
                raise 'Error fetching old messages'

            results = []
            for metadata_str in data:
                # Parse flags
                part = metadata_str.decode('utf-8', errors='ignore')
                uid, flags = parse_uid_flags(part)
                results.append((uid, flags))

            print(f"Will check {len(results)} old messages that have potentially changed.")
            return results

        new_messages = _fetch_new_messages()
        total_new = len(new_messages)

        print('Updating cache for new')
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

        old_messages = _fetch_old_messages()
        total_updated = 0

        print('Updating cache for old')
        for (uid, flags) in old_messages:
            cached = self.cache.get_message(uid, folder)
            if cached:
                if cached['flags'] != flags:
                    print(f"Updating UID {uid} in cache with")
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
