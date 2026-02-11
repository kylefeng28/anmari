#!/usr/bin/env python3
"""
Simple IMAP server that proxies requests to Microsoft Graph API.
Usage: python imap_graph_proxy.py
"""

import asyncio
import time
from peewee import *
from msgraph import GraphServiceClient
from msgraph.generated.users.item.mail_folders.item.messages.messages_request_builder import MessagesRequestBuilder
from kiota_abstractions.base_request_configuration import RequestConfiguration
from auth_credential import AuthCodeCredential, DummyCredential

# Database setup
db = SqliteDatabase('imap_cache.db')


class BaseModel(Model):
    class Meta:
        database = db


class UIDMap(BaseModel):
    folder_id = CharField()
    uid = IntegerField()
    graph_id = CharField()
    received_date = CharField()

    class Meta:
        primary_key = CompositeKey('folder_id', 'uid')
        indexes = (
            (('folder_id', 'graph_id'), True),
        )


class UIDValidity(BaseModel):
    folder_id = CharField(primary_key=True)
    validity = IntegerField()


class UIDCache:
    """Persistent cache for UID<->Graph ID mapping and UIDVALIDITY"""

    def __init__(self):
        db.create_tables([UIDMap, UIDValidity])

    def get_uidvalidity(self, folder_id):
        """Get or create UIDVALIDITY for folder"""
        validity_obj = UIDValidity.get_or_none(UIDValidity.folder_id == folder_id)
        if validity_obj:
            return validity_obj.validity

        validity = int(time.time())
        UIDValidity.create(folder_id=folder_id, validity=validity)
        return validity

    def get_uid_for_graph_id(self, folder_id, graph_id):
        """Get existing UID for a Graph ID, or None if not cached"""
        uid_map = UIDMap.get_or_none(
            (UIDMap.folder_id == folder_id) & (UIDMap.graph_id == graph_id)
        )
        return uid_map.uid if uid_map else None

    def get_next_uid(self, folder_id):
        """Get the next available UID for this folder"""
        max_uid = UIDMap.select(fn.MAX(UIDMap.uid)).where(
            UIDMap.folder_id == folder_id
        ).scalar() or 0
        return max_uid + 1

    def assign_uid(self, folder_id, graph_id, received_date, uid):
        """Assign a specific UID to a Graph ID"""
        UIDMap.get_or_create(
            folder_id=folder_id,
            uid=uid,
            defaults={'graph_id': graph_id, 'received_date': received_date}
        )

    def get_graph_id(self, folder_id, uid):
        """Get Graph ID from UID"""
        uid_map = UIDMap.get_or_none(
            (UIDMap.folder_id == folder_id) & (UIDMap.uid == uid)
        )
        return uid_map.graph_id if uid_map else None

    def get_all_uids(self, folder_id):
        """Get all UIDs for a folder, sorted"""
        return {
            row.uid: row.graph_id 
            for row in UIDMap.select().where(UIDMap.folder_id == folder_id).order_by(UIDMap.uid)
        }


class GraphEmailClient:
    def __init__(self, client: GraphServiceClient, cache: UIDCache):
        self.client = client
        self.cache = cache
        self.selected_folder = None
        self.folder_map = {}  # IMAP name -> Graph ID
        self.msg_map = {}     # UID -> Graph message ID

    async def list_folders(self):
        return await self.client.me.mail_folders.get()

    async def load_folders(self):
        """Load folder list from Graph API"""
        if self.folder_map:
            debug(f'already loaded')
            return

        self.folder_map = {}
        folders = await self.list_folders()
        debug(f'loading folders: {[(folder.display_name, folder.id) for folder in folders.value]}')
        for folder in folders.value:
            name = folder.display_name
            # Map common names to IMAP standard
            if name.lower() == "inbox":
                name = "INBOX"
            elif name.lower() == "sent items":
                name = "Sent"
            elif name.lower() == "deleted items":
                name = "Trash"
            elif name.lower() == "drafts":
                name = "Drafts"

            self.folder_map[name] = folder.id

        debug('folder_map')
        debug(self.folder_map)

    async def select_folder(self, folder_name):
        if folder_name not in self.folder_map:
            raise Exception('Folder not found')

        self.selected_folder = self.folder_map[folder_name]
        await self.load_messages(self.selected_folder)

        uidvalidity = self.cache.get_uidvalidity(self.selected_folder)
        uidnext = self.cache.get_next_uid(self.selected_folder)
        msg_count = len(self.msg_map)

        return uidvalidity, uidnext, msg_count

    async def load_messages(self, folder_id):
        """Load messages from folder and assign UIDs using cache"""
        query_params = MessagesRequestBuilder.MessagesRequestBuilderGetQueryParameters(
            select="id,from,receivedDateTime",
            orderby=["receivedDateTime asc"],
            top=100
        )
        request_configuration = RequestConfiguration(query_parameters=query_params)

        messages = await self.client.me.mail_folders.by_mail_folder_id(folder_id).messages.get(
            request_configuration=request_configuration
        )

        # Start with cached UIDs
        self.msg_map = self.cache.get_all_uids(folder_id)
        next_uid = self.cache.get_next_uid(folder_id)
        debug(f'assigning uids starting from {next_uid}')

        # Process messages in order (oldest first)
        for msg in messages.value:
            # Check if we already have a UID for this message
            existing_uid = self.cache.get_uid_for_graph_id(folder_id, msg.id)

            if existing_uid:
                # Already cached, use existing UID
                debug(f'existing message, uid {msg.id}')
                self.msg_map[existing_uid] = msg.id
            else:
                debug(f'new message, uid {next_uid}')
                # New message, assign next UID
                received_date = msg.received_date_time.isoformat() if msg.received_date_time else ""
                self.cache.assign_uid(folder_id, msg.id, received_date, next_uid)
                self.msg_map[next_uid] = msg.id
                next_uid += 1

    def parse_uids_range(self, uids_str):
        # Parse range if provided: 1:* or A:* or A:B
        if ":" in uids_str:
            all_uids = sorted(self.msg_map.keys())
            range_str = uids_str
            start, end = range_str.split(":")
            start_uid = int(start)

            if start_uid == 1 and end == "*":
                uids = all_uids
            elif end == "*":
                uids = [uid for uid in all_uids if uid >= start_uid]
            else:
                end_uid = int(end)
                uids = [uid for uid in all_uids if start_uid <= uid <= end_uid]

        elif isinstance(uids_str, str):
            return [int(uid) for uid in uids_str.split(' ')]

        return uids_str

    # Return tuple of (uid, message)
    async def fetch(self, uid_str, items):
        uids = self.parse_uids_range(uid_str)

        messages = []
        for uid in uids:
            if uid not in self.msg_map:
                continue

            msg_id = self.msg_map[uid]

            # Fetch message details
            query_params = RequestConfiguration(
                query_parameters={
                    "$select": "id,subject,from,isRead,flag,receivedDateTime"
                }
            )

            msg = await self.client.me.messages.by_message_id(msg_id).get(
                request_configuration=query_params
            )

            messages.append((uid, msg))

        return messages

    async def update_flags(self, uid, operation, flags):
        if uid not in self.msg_map:
            raise Exception("Message not found")

        # Update message properties
        update = {}
        if "\\SEEN" in flags:
            update["isRead"] = "+" in operation
        if "\\FLAGGED" in flags:
            update["flag"] = {"flagStatus": "flagged" if "+" in operation else "notFlagged"}

        await self.client.me.messages.by_message_id(msg_id).patch(update)

    async def handle_move(self, uid, dest_folder):
        if uid not in self.msg_map:
            raise Exception("Message not found")

        if dest_folder not in self.folder_map:
            raise Exception("Destination folder not found")

        msg_id = self.msg_map[uid]
        dest_folder_id = self.folder_map[dest_folder]

        # Move message via Graph API
        await self.client.me.messages.by_message_id(msg_id).move.post(
            {"destinationId": dest_folder_id}
        )

        return ok(tag, "MOVE completed")


# IMAP response helpers
def ok(tag, msg):
    return f"{tag} OK {msg}\r\n"

def no(tag, msg):
    return f"{tag} NO {msg}\r\n"

def untagged(data):
    return f"* {data}\r\n"


def debug(s):
    print(f'[DEBUG] {s}')

class IMAPGraphProxy:
    def __init__(self, client: GraphEmailClient, cache: UIDCache):
        self.client = client

    async def load_messages(self, folder_id):

        # Start with cached UIDs
        self.msg_map = self.cache.get_all_uids(folder_id)
        next_uid = self.cache.get_next_uid(folder_id)

        # Process messages in order (oldest first)
        for msg in messages.value:
            # Check if we already have a UID for this message
            existing_uid = self.cache.get_uid_for_graph_id(folder_id, msg.id)

            if existing_uid:
                # Already cached, use existing UID
                self.msg_map[existing_uid] = msg.id
            else:
                # New message, assign next UID
                received_date = msg.received_date_time.isoformat() if msg.received_date_time else ""
                self.cache.assign_uid(folder_id, msg.id, received_date, next_uid)
                self.msg_map[next_uid] = msg.id
                next_uid += 1

    async def handle_capability(self, tag):
        response = untagged("CAPABILITY IMAP4rev1 AUTH=PLAIN")
        response += ok(tag, "CAPABILITY completed")
        return response

    async def handle_login(self, tag, args):
        # This is a proxy from localhost to the Microsoft APIs, so it should be fine
        # to skip validating credentials
        return ok(tag, "LOGIN completed")

    async def handle_authenticate(self, tag, args):
        return ok(tag, "AUTHENTICATE completed\r\n")

    async def handle_list(self, tag, args):
        await self.client.load_folders()

        response = ""
        for name in self.client.folder_map.keys():
            response += untagged(f'LIST () "/" "{name}"')
        response += ok(tag, "LIST completed")
        return response

    async def handle_select(self, tag, args):
        folder_name = args[0].strip('"')

        try:
            uidvalidity, uidnext, msg_count = await self.client.select_folder(folder_name)
        except Exception as e:
            debug(e)
            return no(tag, "Folder not found")

        response = untagged(f"{msg_count} EXISTS")
        response += untagged("0 RECENT")
        response += untagged(f"OK [UIDVALIDITY {uidvalidity}]")
        response += untagged(f"OK [UIDNEXT {uidnext}]")
        response += untagged("FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
        response += ok(tag, "[READ-WRITE] SELECT completed")
        return response

    async def handle_search(self, tag, args):
        # SEARCH UID 1:* or SEARCH ALL
        uids = self.client.parse_uids_range(args[0])

        uid_list = " ".join(str(uid) for uid in uids)
        response = untagged(f"SEARCH {uid_list}")
        response += ok(tag, "SEARCH completed")
        return response

    async def handle_fetch(self, tag, args, send_response):
        # Parse: FETCH 1:* (FLAGS UID) or FETCH 1 (BODY[])
        uids = self.client.parse_uids_range(args[0])
        items = " ".join(args[1:]).strip("()").upper()

        messages = await self.client.fetch(uids, items)

        for (uid, msg) in messages:
            msg_parts = []

            debug(f'fetching message {uid}: {msg}')

            # Build flags
            flags = []
            if msg.is_read:
                flags.append("\\Seen")
            if msg.flag and msg.flag.flag_status == "flagged":
                flags.append("\\Flagged")

            flags_str = " ".join(flags) if flags else ""

            if "UID" in items or "FLAGS" in items:
                msg_parts.append(f'UID {uid} FLAGS ({flags_str})')

            has_header, has_body = False, False

            if "HEADERS" in items or "BODY" in items:
                has_header = True

            if "BODY[]" in items or "BODY.PEEK[]" in items:
                has_header = True
                has_body = True

            if has_header:
                header_list = [
                    # ['From', f'{msg.from_.email_address.name} <msg.from_.email_address.address>'],
                    ['From', f'someone <someone@outlook.com>'],
                    ['To', f'me@outlook.com'],
                    ['Subject', msg.subject],
                    # TODO use imaplib.Time2Internaldate
                    # ['Date', msg.received_date_time.strftime('%d-%b-%Y')],
                    ['Date', '01-01-2026'],
                ]
                headers = '\r\n'.join([f'{k}: {v}' for (k, v) in header_list]) + '\r\n\r\n'

            if has_body:
                # Get full MIME message
                # mime_content = await self.client.client.me.messages.by_message_id(msg_id).content.get()
                # body = mime_content.decode('utf-8', errors='ignore')

                body = (
                    'hello 1\r\n'
                    'hello 2\r\n'
                    'hello 3\r\n'
                    'hello 4\r\n'
                )

            # Write msg_parts
            if has_header and has_body:
                headers_and_body = headers + '\r\n' + body
                msg_parts.append(f'BODY[] {{{len(headers_and_body)}}}\r\n' + headers_and_body)
            elif has_header:
                msg_parts.append(f'BODY[HEADER] {{{len(headers)}}}\r\n' + headers)

            msg_response = untagged(f'{uid} FETCH (' + ' '.join(msg_parts) + ')')
            debug(f'asdf asdf {uid}')
            await send_response(msg_response)

        return ok(tag, "FETCH completed")

    async def handle_store(self, tag, args):
        # STORE uid +FLAGS (\Seen)
        uid = int(args[0])
        operation = args[1]  # +FLAGS or -FLAGS
        flags = " ".join(args[2:]).strip("()").upper()

        try:
            self.client.update_flags(uid, operation, flags)
        except:
            return no(tag, str(e))

        return ok(tag, "STORE completed")

    async def handle_move(self, tag, args):
        # MOVE uid destination_folder
        uid = int(args[0])
        dest_folder = args[1].strip('"')

        try:
            self.client.move(uid, dest_folder)
        except Exception as e:
            return no(tag, str(e))

        return ok(tag, "MOVE completed")

    async def handle_logout(self, tag):
        response = untagged("BYE IMAP4rev1 Server logging out")
        response += ok(tag, "LOGOUT completed")
        return response


def initialize_graph_client():
    # Initialize Graph client
    # Option 2: Use cached token (after first auth)
    # credential = AuthCodeCredential(
    #     client_id="YOUR_CLIENT_ID",
    #     tenant_id="YOUR_TENANT_ID",
    #     redirect_uri="http://localhost:8080"
    # )

    credential = DummyCredential()

    scopes = ["https://graph.microsoft.com/.default offline_access"]
    client = GraphServiceClient(credential, scopes)

    return client

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, proxy: IMAPGraphProxy):
    # Send greeting
    writer.write(b"* OK IMAP4rev1 Graph Proxy Ready\r\n")
    await writer.drain()

    try:
        while True:
            line = await reader.readline()
            if not line:
                break

            line = line.decode('utf-8').strip()
            print(f"<< {line}")

            parts = line.split(None, 3)
            if len(parts) < 2:
                continue

            tag = parts[0]

            if parts[1].upper() == 'UID':
                parts = [tag] + parts[2:]
            command = parts[1].upper()
            args = parts[2].split() if len(parts) > 2 else []

            response = ""

            async def send_response(response):
                print(f">> {response}")
                writer.write(response.encode())
                await writer.drain()

            if command == "CAPABILITY":
                response = await proxy.handle_capability(tag)
            elif command == "LOGIN":
                response = await proxy.handle_login(tag, args)
            elif command == "AUTHENTICATE":
                response = await proxy.handle_authenticate(tag, args)
            elif command == "LIST":
                response = await proxy.handle_list(tag, args)
            elif command == "SELECT":
                response = await proxy.handle_select(tag, args)
            elif command == "SEARCH":
                response = await proxy.handle_search(tag, args)
            elif command == "FETCH":
                response = await proxy.handle_fetch(tag, args, send_response)
            elif command == "STORE":
                response = await proxy.handle_store(tag, args)
            elif command == "MOVE":
                response = await proxy.handle_move(tag, args)
            elif command == "LOGOUT":
                response = await proxy.handle_logout(tag)
                writer.write(response.encode())
                await writer.drain()
                break
            else:
                response = no(tag, f"{command} not implemented")

            if response:
                await send_response(response)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    graph_client = initialize_graph_client()
    cache = UIDCache()
    client = GraphEmailClient(graph_client, cache)
    proxy = IMAPGraphProxy(client, cache)

    await client.load_folders()
    await client.select_folder('INBOX')

    async def client_handler(reader, writer):
        await handle_client(reader, writer, proxy)

    server = await asyncio.start_server(client_handler, '127.0.0.1', 1143)

    print("IMAP Graph Proxy listening on 127.0.0.1:1143")
    print("Configure your email client to connect to localhost:1143")
    print(f"Cache database: imap_cache.db")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
