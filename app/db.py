"""SQLite storage for threads, messages (short-term) and memories (long-term).

Everything lives in a single local file: data/assistant.db.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT 'New chat',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    -- short-term memory: rolling summary of messages with id <= summary_upto
    summary       TEXT NOT NULL DEFAULT '',
    summary_upto  INTEGER NOT NULL DEFAULT 0,
    -- long-term memory: messages with id <= memory_upto were already mined
    memory_upto   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id   TEXT NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,
    thinking    TEXT NOT NULL DEFAULT '',
    stats       TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);

CREATE TABLE IF NOT EXISTS memories (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category          TEXT NOT NULL DEFAULT 'other',
    content           TEXT NOT NULL,
    importance        INTEGER NOT NULL DEFAULT 3,
    source_thread_id  TEXT,
    created_at        REAL NOT NULL,
    updated_at        REAL NOT NULL
);
"""


class Store:
    def __init__(self, path=config.DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _q(self, sql: str, args: tuple = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, args).fetchall()]

    def _x(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.execute(sql, args)
            self._conn.commit()
            return cur.lastrowid

    # ---- threads ---------------------------------------------------------
    def create_thread(self, title: str = "New chat") -> Dict[str, Any]:
        tid, now = uuid.uuid4().hex, time.time()
        self._x(
            "INSERT INTO threads (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (tid, title, now, now),
        )
        return self.get_thread(tid)

    def get_thread(self, tid: str) -> Optional[Dict[str, Any]]:
        rows = self._q("SELECT * FROM threads WHERE id=?", (tid,))
        return rows[0] if rows else None

    def list_threads(self) -> List[Dict[str, Any]]:
        return self._q(
            "SELECT id, title, created_at, updated_at FROM threads ORDER BY updated_at DESC"
        )

    def update_thread(self, tid: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE threads SET {cols} WHERE id=?", (*fields.values(), tid))

    def delete_thread(self, tid: str) -> None:
        self._x("DELETE FROM threads WHERE id=?", (tid,))

    def threads_with_unmined_messages(self) -> List[str]:
        rows = self._q(
            """SELECT t.id FROM threads t
               WHERE EXISTS (SELECT 1 FROM messages m
                             WHERE m.thread_id=t.id AND m.id>t.memory_upto)"""
        )
        return [r["id"] for r in rows]

    # ---- messages --------------------------------------------------------
    def add_message(
        self, tid: str, role: str, content: str, thinking: str = "", stats: Optional[dict] = None
    ) -> Dict[str, Any]:
        now = time.time()
        mid = self._x(
            "INSERT INTO messages (thread_id, role, content, thinking, stats, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (tid, role, content, thinking, json.dumps(stats or {}), now),
        )
        self._x("UPDATE threads SET updated_at=? WHERE id=?", (now, tid))
        return self._q("SELECT * FROM messages WHERE id=?", (mid,))[0]

    def messages(self, tid: str, after_id: int = 0, upto_id: Optional[int] = None) -> List[Dict[str, Any]]:
        sql, args = "SELECT * FROM messages WHERE thread_id=? AND id>?", [tid, after_id]
        if upto_id is not None:
            sql += " AND id<=?"
            args.append(upto_id)
        rows = self._q(sql + " ORDER BY id", tuple(args))
        for r in rows:
            r["stats"] = json.loads(r["stats"] or "{}")
        return rows

    def message_count(self, tid: str) -> int:
        return self._q("SELECT COUNT(*) AS n FROM messages WHERE thread_id=?", (tid,))[0]["n"]

    # ---- memories --------------------------------------------------------
    def list_memories(self) -> List[Dict[str, Any]]:
        return self._q("SELECT * FROM memories ORDER BY importance DESC, updated_at DESC")

    def add_memory(self, category: str, content: str, importance: int = 3,
                   source_thread_id: Optional[str] = None) -> int:
        now = time.time()
        return self._x(
            "INSERT INTO memories (category, content, importance, source_thread_id, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (category, content, importance, source_thread_id, now, now),
        )

    def update_memory(self, mid: int, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if v is not None}
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        self._x(f"UPDATE memories SET {cols} WHERE id=?", (*fields.values(), mid))

    def delete_memory(self, mid: int) -> None:
        self._x("DELETE FROM memories WHERE id=?", (mid,))

    def replace_memories(self, items: List[Dict[str, Any]]) -> None:
        """Atomically swap the whole memory set (used by compaction)."""
        now = time.time()
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM memories")
                self._conn.executemany(
                    "INSERT INTO memories (category, content, importance, created_at, updated_at)"
                    " VALUES (?,?,?,?,?)",
                    [(i["category"], i["content"], i["importance"], now, now) for i in items],
                )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def export_memories(self) -> None:
        """Mirror long-term memory to a human-readable JSON file next to the DB."""
        config.MEMORY_EXPORT_PATH.write_text(
            json.dumps(self.list_memories(), indent=2, ensure_ascii=False)
        )


store = Store()
