#!/usr/bin/env python3
"""
Simple IMAP server that proxies requests to Microsoft Graph API.
Usage: python imap_graph_proxy.py
"""

import asyncio
import email
from email.parser import BytesParser
from msgraph import GraphServiceClient
from azure.identity import DeviceCodeCredential
from kiota_abstractions.base_request_configuration import RequestConfiguration

# IMAP response helpers
def ok(tag, msg="OK"):
    return f"{tag} {msg}\r\n"

def no(tag, msg="NO"):
    return f"{tag} {msg}\r\n"

def untagged(data):
    return f"* {data}\r\n"

class IMAPGraphProxy:
    def __init__(self, client: GraphServiceClient):
        self.client = client
        self.selected_folder = None
        self.folder_map = {}  # IMAP name -> Graph ID
        self.msg_map = {}     # UID -> Graph message ID
        self.uid_counter = 1

    async def load_folders(self):
        """Load folder list from Graph API"""
        folders = await self.client.me.mail_folders.get()
        self.folder_map = {}

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

    async def load_messages(self, folder_id):
        """Load messages from folder and assign UIDs"""
        query_params = RequestConfiguration(
            query_parameters={
                "$select": "id,subject,from,receivedDateTime,isRead",
                "$orderby": "receivedDateTime desc",
                "$top": 100
            }
        )

        messages = await self.client.me.mail_folders.by_mail_folder_id(folder_id).messages.get(
            request_configuration=query_params
        )

        self.msg_map = {}
        self.uid_counter = 1

        for msg in messages.value:
            self.msg_map[self.uid_counter] = msg.id
            self.uid_counter += 1

    async def handle_capability(self, tag):
        response = untagged("CAPABILITY IMAP4rev1 AUTH=PLAIN")
        response += ok(tag, "CAPABILITY completed")
        return response

    async def handle_login(self, tag, args):
        # In real implementation, validate credentials
        return ok(tag, "LOGIN completed")

    async def handle_list(self, tag, args):
        await self.load_folders()

        response = ""
        for name in self.folder_map.keys():
            response += untagged(f'LIST () "/" "{name}"')
        response += ok(tag, "LIST completed")
        return response

    async def handle_select(self, tag, args):
        folder_name = args[0].strip('"')

        if folder_name not in self.folder_map:
            return no(tag, "Folder not found")

        self.selected_folder = self.folder_map[folder_name]
        await self.load_messages(self.selected_folder)

        msg_count = len(self.msg_map)
        response = untagged(f"{msg_count} EXISTS")
        response += untagged("0 RECENT")
        response += untagged("FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)")
        response += ok(tag, "[READ-WRITE] SELECT completed")
        return response

    async def handle_fetch(self, tag, args):
        # Parse: FETCH 1:* (FLAGS UID) or FETCH 1 (BODY[])
        seq_set = args[0]
        items = " ".join(args[1:]).strip("()").upper()

        response = ""

        # Simple range parsing (1:* means all)
        if seq_set == "1:*":
            uids = list(self.msg_map.keys())
        else:
            uids = [int(seq_set)]

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

            # Build flags
            flags = []
            if msg.is_read:
                flags.append("\\Seen")
            if msg.flag and msg.flag.flag_status == "flagged":
                flags.append("\\Flagged")

            flags_str = " ".join(flags) if flags else ""

            if "UID" in items or "FLAGS" in items:
                response += untagged(f'{uid} FETCH (UID {uid} FLAGS ({flags_str}))')

            if "BODY[]" in items or "BODY.PEEK[]" in items:
                # Get full MIME message
                mime_content = await self.client.me.messages.by_message_id(msg_id).content.get()
                size = len(mime_content)
                response += untagged(f'{uid} FETCH (UID {uid} BODY[] {{{size}}}')
                response += mime_content.decode('utf-8', errors='ignore')
                response += ")"

        response += ok(tag, "FETCH completed")
        return response

    async def handle_store(self, tag, args):
        # STORE uid +FLAGS (\Seen)
        uid = int(args[0])
        operation = args[1]  # +FLAGS or -FLAGS
        flags = " ".join(args[2:]).strip("()").upper()

        if uid not in self.msg_map:
            return no(tag, "Message not found")

        msg_id = self.msg_map[uid]

        # Update message properties
        update = {}
        if "\\SEEN" in flags:
            update["isRead"] = "+" in operation
        if "\\FLAGGED" in flags:
            update["flag"] = {"flagStatus": "flagged" if "+" in operation else "notFlagged"}

        await self.client.me.messages.by_message_id(msg_id).patch(update)

        return ok(tag, "STORE completed")

    async def handle_logout(self, tag):
        response = untagged("BYE IMAP4rev1 Server logging out")
        response += ok(tag, "LOGOUT completed")
        return response

async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    # Initialize Graph client
    credential = DeviceCodeCredential(
        client_id="YOUR_CLIENT_ID",
        tenant_id="common"
    )
    scopes = ["https://graph.microsoft.com/.default"]
    client = GraphServiceClient(credential, scopes)

    proxy = IMAPGraphProxy(client)

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

            parts = line.split(None, 2)
            if len(parts) < 2:
                continue

            tag = parts[0]
            command = parts[1].upper()
            args = parts[2].split() if len(parts) > 2 else []

            response = ""

            if command == "CAPABILITY":
                response = await proxy.handle_capability(tag)
            elif command == "LOGIN":
                response = await proxy.handle_login(tag, args)
            elif command == "LIST":
                response = await proxy.handle_list(tag, args)
            elif command == "SELECT":
                response = await proxy.handle_select(tag, args)
            elif command == "FETCH":
                response = await proxy.handle_fetch(tag, args)
            elif command == "STORE":
                response = await proxy.handle_store(tag, args)
            elif command == "LOGOUT":
                response = await proxy.handle_logout(tag)
                writer.write(response.encode())
                await writer.drain()
                break
            else:
                response = no(tag, f"{command} not implemented")

            print(f">> {response}")
            writer.write(response.encode())
            await writer.drain()

    except Exception as e:
        print(f"Error: {e}")
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    server = await asyncio.start_server(handle_client, '127.0.0.1', 1143)

    print("IMAP Graph Proxy listening on 127.0.0.1:1143")
    print("Configure your email client to connect to localhost:1143")

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
