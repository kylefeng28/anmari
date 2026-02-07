"""
Action Queue System - Git-like staging for IMAP operations
"""

import json
import sqlite3
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class QueuedAction:
    id: int
    query: str
    folder: str
    action_type: str
    action_data: dict
    message_count: int
    created_at: str
    status: str

    @classmethod
    def from_row(cls, row):
        return cls(
            id=row['id'],
            query=row['query'],
            folder=row['folder'],
            action_type=row['action_type'],
            action_data=json.loads(row['action_data']),
            message_count=row['message_count'],
            created_at=row['created_at'],
            status=row['status']
        )

    def describe(self) -> str:
        """Human-readable description of the action"""
        if self.action_type == 'move':
            return f"MOVE: {self.message_count} messages ({self.query}) → {self.action_data['dest']}"
        elif self.action_type == 'add_flag':
            flags = ', '.join(self.action_data['flags'])
            return f"ADD_FLAG: {self.message_count} messages ({self.query}) → {flags}"
        elif self.action_type == 'remove_flag':
            flags = ', '.join(self.action_data['flags'])
            return f"REMOVE_FLAG: {self.message_count} messages ({self.query}) → {flags}"
        elif self.action_type == 'add_label':
            labels = ', '.join(self.action_data['labels'])
            return f"ADD_LABEL: {self.message_count} messages ({self.query}) → {labels}"
        elif self.action_type == 'remove_label':
            labels = ', '.join(self.action_data['labels'])
            return f"REMOVE_LABEL: {self.message_count} messages ({self.query}) → {labels}"
        return f"{self.action_type.upper()}: {self.message_count} messages ({self.query})"


class ActionQueue:
    def __init__(self, cache):
        self.conn = cache.conn

    def queue_action(self, query: str, folder: str, action_type: str, 
                    action_data: dict, message_count: int) -> int:
        """Add an action to the queue"""
        cur = self.conn.execute(
            """INSERT INTO action_queue (query, folder, action_type, action_data, message_count)
               VALUES (?, ?, ?, ?, ?)""",
            (query, folder, action_type, json.dumps(action_data), message_count)
        )
        self.conn.commit()
        return cur.lastrowid

    def get_pending_actions(self) -> List[QueuedAction]:
        """Get all pending actions"""
        cur = self.conn.execute(
            """SELECT * FROM action_queue 
               WHERE status = 'pending' 
               ORDER BY id ASC"""
        )
        return [QueuedAction.from_row(row) for row in cur.fetchall()]

    def get_action(self, action_id: int) -> Optional[QueuedAction]:
        """Get specific action by ID"""
        cur = self.conn.execute(
            "SELECT * FROM action_queue WHERE id = ?",
            (action_id,)
        )
        row = cur.fetchone()
        return QueuedAction.from_row(row) if row else None

    def mark_applied(self, action_id: int):
        """Mark action as applied"""
        self.conn.execute(
            "UPDATE action_queue SET status = 'applied' WHERE id = ?",
            (action_id,)
        )
        self.conn.commit()

    def mark_failed(self, action_id: int):
        """Mark action as failed"""
        self.conn.execute(
            "UPDATE action_queue SET status = 'failed' WHERE id = ?",
            (action_id,)
        )
        self.conn.commit()

    def remove_action(self, action_id: int):
        """Remove action from queue"""
        self.conn.execute(
            "DELETE FROM action_queue WHERE id = ?",
            (action_id,)
        )
        self.conn.commit()

    def clear_pending(self):
        """Clear all pending actions"""
        self.conn.execute("DELETE FROM action_queue WHERE status = 'pending'")
        self.conn.commit()

    def undo_last(self, count: int = 1) -> int:
        """Remove last N pending actions"""
        cur = self.conn.execute(
            """DELETE FROM action_queue 
               WHERE id IN (
                   SELECT id FROM action_queue 
                   WHERE status = 'pending' 
                   ORDER BY id DESC 
                   LIMIT ?
               )""",
            (count,)
        )
        self.conn.commit()
        return cur.rowcount
