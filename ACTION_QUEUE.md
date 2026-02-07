# Action Queue System Design

## Overview
Git-like staging system for IMAP operations. Queue actions locally, review them, then apply to server in batch.

## Architecture

### Database Schema
```sql
CREATE TABLE action_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query TEXT NOT NULL,              -- Search query that matched messages
    folder TEXT NOT NULL,              -- Folder to operate on
    action_type TEXT NOT NULL,         -- 'move', 'add_flag', 'remove_flag', 'add_label', 'remove_label'
    action_data TEXT NOT NULL,         -- JSON: {"dest": "Newsletter"} or {"flags": ["\\Seen"]}
    message_count INTEGER,             -- Number of messages matched
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'pending'      -- 'pending', 'applied', 'failed', 'conflict'
);
```

### Action Types
- `move`: Move messages to another folder
- `add_flag`: Add IMAP flags (\\Seen, \\Flagged, \\Deleted, etc.)
- `remove_flag`: Remove IMAP flags
- `add_label`: Add Gmail labels (requires X-GM-EXT-1)
- `remove_label`: Remove Gmail labels

### Workflow
1. **Queue**: User queues actions with search queries
   - Actions are stored with the query, not individual UIDs
   - Message count is cached for display
   
2. **Status**: Review pending actions
   - Shows query, action type, message count
   - Detects conflicts (same messages affected by multiple actions)
   
3. **Apply**: Execute all pending actions on IMAP server
   - Re-runs queries to get current UIDs (handles messages added/deleted since queue)
   - Executes IMAP commands in batch per action
   - Marks actions as 'applied' or 'failed'
   - Triggers sync after apply to update local cache

4. **Undo**: Remove actions from queue
   - Only works on 'pending' actions
   - Can undo last N actions or specific action by ID

## Conflict Resolution
When applying actions, conflicts are detected:
- Multiple moves for same message
- Contradictory flag operations (add \\Seen + remove \\Seen)

On conflict, prompt user:
```
Conflict detected:
  Action #1: MOVE 5 messages (tag:newsletter) → Newsletter
  Action #2: MOVE 3 messages (from:spam) → Trash
  
  Overlapping messages: [123, 456, 789]
  
  Options:
    1. Apply action #1 only
    2. Apply action #2 only
    3. Apply both (last wins)
    4. Skip conflicting messages
    5. Cancel
```

## IMAP Operations

### Move (COPY + EXPUNGE)
```python
# Current: Basic COPY + EXPUNGE
client.copy(uids, dest_folder)
client.delete_messages(uids)
client.expunge()

# Future optimization: Use COPYUID response
# RFC 4315: UIDPLUS extension provides COPYUID response
# COPYUID <uidvalidity> <source_uids> <dest_uids>
# Allows updating cache without re-sync
```

### Flags
```python
client.add_flags(uids, [b'\\Seen'])
client.remove_flags(uids, [b'\\Flagged'])
```

### Gmail Labels
```python
client.add_gmail_labels(uids, ['Tax', 'Important'])
client.remove_gmail_labels(uids, ['Spam'])
```

## Local Cache Updates
After `apply`, run full sync to update cache:
```python
# Apply all actions
apply_actions()

# Sync affected folders
for folder in affected_folders:
    sync_from_server(folder)
```

**Future optimization**: Update cache optimistically during queue, then reconcile on apply.

## Commands

### Queue Actions
```bash
# Move messages
anmari queue move "tag:newsletter" --to Newsletter

# Add flags
anmari queue flag "from:turbotax.com" --add Seen --add Flagged

# Remove flags  
anmari queue flag "date:yesterday" --remove Seen

# Add Gmail labels
anmari queue label "from:turbotax.com" --add Tax --add Important

# Remove Gmail labels
anmari queue label "tag:spam" --remove Inbox
```

### Review Queue
```bash
anmari status

# Output:
# Pending Actions (3):
#   [1] MOVE: 15 messages (tag:newsletter) → Newsletter
#   [2] ADD_FLAG: 8 messages (from:turbotax.com) → \\Seen, \\Flagged
#   [3] ADD_LABEL: 8 messages (from:turbotax.com) → Tax, Important
```

### Apply Actions
```bash
# Dry-run (preview without executing)
anmari apply --dry-run

# Apply all pending actions
anmari apply

# Apply specific action
anmari apply --id 1
```

### Manage Queue
```bash
# Clear all pending actions
anmari queue clear

# Undo last action
anmari queue undo

# Undo last 5 actions
anmari queue undo --count 5

# Remove specific action
anmari queue remove --id 2
```

## Error Handling
- **Network failure**: Mark action as 'failed', keep in queue for retry
- **Permission denied**: Mark as 'failed', log error
- **Message not found**: Skip missing UIDs, apply to remaining
- **Folder not found**: Prompt to create or cancel

## Future Enhancements
1. **UIDPLUS optimization**: Use COPYUID to update cache without re-sync
2. **Optimistic updates**: Update local cache when queueing actions
3. **Action dependencies**: "Move then label" vs "label then move"
4. **Scheduled apply**: Auto-apply at intervals
5. **Action history**: Keep log of applied actions for audit
6. **Rollback**: Undo applied actions (move back, restore flags)
7. **Batch optimization**: Combine multiple flag operations into one IMAP command
