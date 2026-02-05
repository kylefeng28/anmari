### Basic concepts
- **RFC 9051 - IMAP Version 4rev2**: https://www.rfc-editor.org/rfc/rfc9051
- Informational RFC 4549 - Synchronization Operations for Disconnected IMAP4 Clients: 
- Standards RFC 7162 - Quick Flag Changes Resynchronization (`CONDSTORE`) and Quick Mailbox Resynchronization (`QRESYNC`): https://www.rfc-editor.org/rfc/rfc7162.html (Amends [RFC 4551](https://www.rfc-editor.org/rfc/rfc4549))

> **RFC 9051, IMAP Version 4rev2**
> The combination of mailbox [folder] name, UIDVALIDITY, and UID must refer to a single, immutable (or expunged) message on that server forever. In particular, the internal date, RFC822.SIZE, envelope, body structure, and message texts MUST never change. This does not include message numbers, nor does it include attributes that can be set by a STORE command (such as FLAGS). 

(IMAP mailbox refers to a folder, not the entire account's mailbox)

- Each Gmail folder has a `UIDVALIDITY` value that stays the same even when the folder is renamed (can distinguish between folder that is renamed vs a folder that happened to be renamed to the old name)
  - Therefore, in Gmail, we can just use `UIDVALIDITY` + `UID` to refer to a message uniquely
- Gmail supports `CONDSTORE`, which returns a `HIGHESTMODSEQ` for each folder that the client can cache.
- When the client starts, the client can run `SEARCH MODSEQ <cached-value>` or `FETCH 1:* (FLAGS) (CHANGEDSINCE <cached-value>)` to get changes to old messages and flags since the last sync

- https://stackoverflow.com/questions/19571456/how-imap-client-can-detact-gmail-label-rename-programmatically
- https://stackoverflow.com/questions/10076690/ruby-imap-changes-since-last-check
- https://medium.com/@kehers/imap-new-messages-since-last-check-5cc338fd5f09

## Synchronization

> **RFC 4549, Section 3 and Section 6.1 (combined to incorporate CONDSYNC / HIGHESTMODSEQ)**
>   c) "Client-to-server synchronization": for each IMAP "action" that
>      was pending on the client, do the following:
>      1) If the action implies opening a new mailbox (any operation that
>         operates on messages), open the mailbox.  Check its UID
>         validity value (see Section 4.1 for more details) returned in
>         the UIDVALIDITY response code.  If the UIDVALIDITY value
>         returned by the server differs, the client MUST empty the local
>         cache of the mailbox and remove any pending "actions" that
>         refer to UIDs in that mailbox (and consider them failed).
>      2) Perform the action.  If the action is to delete a mailbox
>         (DELETE), make sure that the mailbox is closed first (see also
>         Section 3.4.12 of [RFC2683]).
>
>   d) "Server-to-client synchronization": for each mailbox that requires
>      synchronization, do the following:
>
>      1a) Check the mailbox UIDVALIDITY (see section 4.1 for more
>          details) with SELECT/EXAMINE/STATUS.
>
>          If the UIDVALIDITY value returned by the server differs, the
>          client MUST
>
>          * empty the local cache of that mailbox;
>          * (if CONDSTORE) "forget" the cached HIGHESTMODSEQ value for the mailbox;
>          * remove any pending "actions" that refer to UIDs in that
>            mailbox (note that this doesn't affect actions performed on
>            client-generated fake UIDs; see Section 5); and
>          * skip steps 1b and 2-II;
>
>      1b) Check the mailbox HIGHESTMODSEQ.  If the cached value is the
>          same as the one returned by the server, skip fetching message
>          flags on step 2-II, i.e., the client only has to find out
>          which messages got expunged.
>
>      2) Fetch the current "descriptors";
>         I)  Discover new messages.
>         II) Discover changes to old messages and flags for new messages
>
>             IIa) (if CONDSTORE)
>
>                "FETCH 1:* (FLAGS) (CHANGEDSINCE <cached-value>)" or
>                "SEARCH MODSEQ <cached-value>".
>
>                Discover expunged messages; for example, using
>                "UID SEARCH 1:<lastseenuid>".  (All messages not returned
>                in this command are expunged.)
>
>             IIb) (if no CONDSTORE)
>
>                tag1 UID FETCH <lastseenuid+1>:* <descriptors>
>                tag2 UID FETCH 1:<lastseenuid> FLAGS
>
>                The first command will request some information about "new" messages
>                (i.e., messages received by the server since the last
>                synchronization).  It will also allow the client to build a message
>                number to UID map (only for new messages).  The second command allows
>                the client to
>
>                   1) update cached flags for old messages;
>                   2) find out which old messages got expunged; and
>                   3) build a mapping between message numbers and UIDs (for old
>                      messages).
>
>      3) Fetch the bodies of any "interesting" messages that the client
>         doesn't already have.
>
>   e) Close all open mailboxes not required for further operations (if
>      staying online) or disconnect all open connections (if going
>      offline).
>
> Example 10:
> The UIDVALIDITY value is the same, but the HIGHESTMODSEQ value
> has changed on the server while the client was offline.
>
>      C: A142 SELECT INBOX
>      S: * 172 EXISTS
>      S: * 1 RECENT
>      S: * OK [UNSEEN 12] Message 12 is first unseen
>      S: * OK [UIDVALIDITY 3857529045] UIDs valid
>      S: * OK [UIDNEXT 201] Predicted next UID
>      S: * FLAGS (\Answered \Flagged \Deleted \Seen \Draft)
>      S: * OK [PERMANENTFLAGS (\Deleted \Seen \*)] Limited
>      S: * OK [HIGHESTMODSEQ 20010715194045007]
>      S: A142 OK [READ-WRITE] SELECT completed
>
>   After that, either:
>
>      C: A143 UID FETCH 1:* (FLAGS) (CHANGEDSINCE 20010715194032001)
>      S: * 2 FETCH (UID 6 MODSEQ (20010715205008000) FLAGS (\Deleted))
>      S: * 5 FETCH (UID 9 MODSEQ (20010715195517000) FLAGS ($NoJunk
>          $AutoJunk $MDNSent))
>         ...
>      S: A143 OK FETCH completed
>
>
>      C: A143 UID SEARCH MODSEQ 20010715194032001 UID 1:20
>      S: * SEARCH 6 9 11 12 18 19 20 23 (MODSEQ 20010917162500)
>      S: A143 OK Search complete
>      C: A144 UID SEARCH 1:20
>      S: * SEARCH 6 9 ...
>      S: A144 OK FETCH completed

