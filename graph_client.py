"""Microsoft Graph API client for O365 email access"""
from msgraph import GraphServiceClient
from azure.identity import DeviceCodeCredential
from dataclasses import dataclass
from datetime import datetime
import click


@dataclass
class Envelope:
    from_name: str
    from_addr: str
    subject: str
    date_ts: int


class EmailGraphClient:
    """Microsoft Graph API client matching IMAP client interface"""

    def __init__(self, client_id: str, tenant_id: str, email_addr: str, cache):
        self.email_addr = email_addr
        self.cache = cache

        # Device code flow for interactive auth
        credential = DeviceCodeCredential(
            client_id=client_id,
            tenant_id=tenant_id
        )

        scopes = ['https://graph.microsoft.com/.default']
        self.client = GraphServiceClient(credentials=credential, scopes=scopes)

        print(f'Authenticated as {email_addr} to Microsoft Graph')

    def close(self):
        pass  # No explicit close needed

    def select_folder(self, folder):
        """Map IMAP folder names to Graph well-known names"""
        self.current_folder = folder

        # Map common IMAP names to Graph well-known names
        folder_map = {
            'INBOX': 'inbox',
            'Sent': 'sentitems',
            'Drafts': 'drafts',
            'Trash': 'deleteditems',
            'Archive': 'archive'
        }
        self.graph_folder = folder_map.get(folder, folder)

    def get_unread_messages(self):
        """Get unread message IDs"""
        result = self.client.me.mail_folders.by_mail_folder_id(self.graph_folder).messages.get(
            query_parameters={'$filter': 'isRead eq false', '$select': 'id'}
        )
        return [msg.id for msg in result.value]

    def _parse_envelope(self, msg):
        """Convert Graph message to Envelope"""
        from_addr = msg.from_field.email_address.address if msg.from_field else ''
        from_name = msg.from_field.email_address.name if msg.from_field else ''
        subject = msg.subject or ''

        # Parse date
        if msg.received_date_time:
            date_ts = int(msg.received_date_time.timestamp())
        else:
            date_ts = int(datetime.now().timestamp())

        return Envelope(from_name, from_addr, subject, date_ts)

    def _get_flags(self, msg):
        """Convert Graph message properties to IMAP-style flags"""
        flags = []
        if msg.is_read:
            flags.append('\\Seen')
        if msg.flag and msg.flag.flagged:
            flags.append('\\Flagged')
        return ' '.join(flags)

    def sync_from_server(self, folder, page_size=100):
        """Sync using delta query for efficient change tracking"""
        print(f"Syncing {folder} from {self.email_addr}...")

        self.select_folder(folder)

        # Get delta link from cache if exists
        delta_link = self.cache.get_delta_link(folder)

        total_new = 0
        total_updated = 0
        total_deleted = 0

        if delta_link:
            # Incremental sync using delta
            print("Using delta sync for changes since last sync")
            result = self.client.me.mail_folders.by_mail_folder_id(
                self.graph_folder
            ).messages.delta.get(skip_token=delta_link)
        else:
            # Full sync
            print("Performing full sync")
            result = self.client.me.mail_folders.by_mail_folder_id(
                self.graph_folder
            ).messages.delta.get(
                query_parameters={
                    '$select': 'id,subject,from,sender,receivedDateTime,isRead,flag',
                    '$top': page_size
                }
            )

        # Process messages
        while True:
            for msg in result.value:
                msg_id = msg.id

                # Check if message was deleted
                if hasattr(msg, '@removed'):
                    self.cache.delete_message(msg_id, folder)
                    total_deleted += 1
                    continue

                # Parse message
                envelope = self._parse_envelope(msg)
                flags = self._get_flags(msg)

                # Check if exists in cache
                cached = self.cache.get_message(msg_id, folder)

                if not cached:
                    # New message
                    self.cache.insert_message(
                        msg_id,
                        folder,
                        envelope.from_addr,
                        envelope.from_name,
                        envelope.subject,
                        envelope.date_ts,
                        flags
                    )
                    total_new += 1
                else:
                    # Update if flags changed
                    if cached['flags'] != flags:
                        self.cache.update_flags(msg_id, folder, flags)
                        total_updated += 1

            # Check for next page
            if result.odata_next_link:
                result = self.client.me.mail_folders.by_mail_folder_id(
                    self.graph_folder
                ).messages.delta.get(skip_token=result.odata_next_link)
            else:
                break

        # Save delta link for next sync
        if result.odata_delta_link:
            self.cache.set_delta_link(folder, result.odata_delta_link)

        print(f"\nSync complete! New: {total_new}, Updated: {total_updated}, Deleted: {total_deleted}")

    def get_message_body(self, msg_id):
        """Fetch full message body"""
        msg = self.client.me.messages.by_message_id(msg_id).get(
            query_parameters={'$select': 'body'}
        )
        return msg.body.content if msg.body else ''

    def get_message_headers(self, msg_id):
        """Fetch internet message headers"""
        msg = self.client.me.messages.by_message_id(msg_id).get(
            query_parameters={'$select': 'internetMessageHeaders'}
        )
        return msg.internet_message_headers or []
