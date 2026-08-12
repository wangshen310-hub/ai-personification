"""Transactional runtime persistence for events, configuration, memory, and actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from companion_kernel.events import KernelEvent


SCHEMA_VERSION = 1


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path, timeout=30.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=30000")
    return connection


class SQLiteRuntimeStore:
    """A concurrency-safe event store plus the runtime's durable control tables."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(path) as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS configuration (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_event_id) REFERENCES events(id)
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    action_id TEXT PRIMARY KEY,
                    source_event_id TEXT NOT NULL,
                    decision_event_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    proactive INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    outcome_event_id TEXT UNIQUE,
                    FOREIGN KEY(source_event_id) REFERENCES events(id),
                    FOREIGN KEY(decision_event_id) REFERENCES events(id)
                );
                CREATE INDEX IF NOT EXISTS memories_source_idx ON memories(source_event_id);
                CREATE INDEX IF NOT EXISTS outbox_status_idx ON outbox(status, created_at);
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def append(self, event: KernelEvent) -> bool:
        encoded = json.dumps(event.to_dict()["payload"], ensure_ascii=False, sort_keys=True)
        with _connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO events(id, at, kind, payload) VALUES(?, ?, ?, ?)",
                (event.id, event.at.isoformat(), event.kind.value, encoded),
            )
            return cursor.rowcount == 1

    def contains(self, event_id: str) -> bool:
        with _connect(self.path) as db:
            row = db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone()
        return row is not None

    def read_all(self) -> tuple[KernelEvent, ...]:
        with _connect(self.path) as db:
            rows = db.execute(
                "SELECT id, at, kind, payload FROM events ORDER BY sequence"
            ).fetchall()
        return tuple(
            KernelEvent.from_dict(
                {
                    "id": row["id"],
                    "at": row["at"],
                    "kind": row["kind"],
                    "payload": json.loads(row["payload"]),
                }
            )
            for row in rows
        )

    def import_events(self, events: Iterable[KernelEvent]) -> int:
        imported = 0
        for event in events:
            imported += int(self.append(event))
        return imported

    def load_config(self, key: str) -> dict[str, object] | None:
        with _connect(self.path) as db:
            row = db.execute("SELECT value FROM configuration WHERE key = ?", (key,)).fetchone()
        return None if row is None else dict(json.loads(row["value"]))

    def save_config(self, key: str, value: dict[str, object], at: datetime) -> None:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        with _connect(self.path) as db:
            db.execute(
                """INSERT INTO configuration(key, value, updated_at) VALUES(?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, encoded, at.isoformat()),
            )

    def save_memory(
        self,
        *,
        memory_id: str,
        source_event_id: str,
        kind: str,
        content: str,
        confidence: float,
        status: str,
        at: datetime,
    ) -> None:
        with _connect(self.path) as db:
            db.execute(
                """INSERT OR REPLACE INTO memories
                   (id, source_event_id, kind, content, confidence, status, created_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?)""",
                (memory_id, source_event_id, kind, content, confidence, status, at.isoformat()),
            )

    def recent_memories(self, limit: int = 8) -> tuple[str, ...]:
        with _connect(self.path) as db:
            rows = db.execute(
                """SELECT kind, content, confidence, status FROM memories
                   ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return tuple(
            f"{row['kind']}[{row['status']},{row['confidence']:.2f}]: {row['content']}"
            for row in reversed(rows)
        )

    def memory_count(self, kind: str, *, status: str = "observed") -> int:
        with _connect(self.path) as db:
            row = db.execute(
                "SELECT COUNT(*) AS count FROM memories WHERE kind = ? AND status = ?",
                (kind, status),
            ).fetchone()
        return int(row["count"])

    def create_action(self, action: "OutboxAction") -> bool:
        with _connect(self.path) as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO outbox
                   (action_id, source_event_id, decision_event_id, candidate_id, action,
                    proactive, content, status, created_at, updated_at)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    action.action_id,
                    action.source_event_id,
                    action.decision_event_id,
                    action.candidate_id,
                    action.action,
                    int(action.proactive),
                    action.content,
                    action.status,
                    action.created_at.isoformat(),
                    action.updated_at.isoformat(),
                ),
            )
            return cursor.rowcount == 1

    def get_action(self, action_id: str) -> "OutboxAction | None":
        with _connect(self.path) as db:
            row = db.execute("SELECT * FROM outbox WHERE action_id = ?", (action_id,)).fetchone()
        return None if row is None else OutboxAction.from_row(row)

    def transition_action(
        self,
        action_id: str,
        *,
        expected: tuple[str, ...],
        status: str,
        at: datetime,
        outcome_event_id: str | None = None,
    ) -> bool:
        placeholders = ",".join("?" for _ in expected)
        parameters = (status, at.isoformat(), outcome_event_id, action_id, *expected)
        with _connect(self.path) as db:
            cursor = db.execute(
                f"""UPDATE outbox SET status = ?, updated_at = ?, outcome_event_id = COALESCE(?, outcome_event_id)
                    WHERE action_id = ? AND status IN ({placeholders})""",
                parameters,
            )
            return cursor.rowcount == 1

    def confirm_delivery(self, action_id: str, event: KernelEvent, at: datetime) -> bool:
        """Atomically bind one delivered outcome event to one pending action."""

        encoded = json.dumps(event.to_dict()["payload"], ensure_ascii=False, sort_keys=True)
        db = _connect(self.path)
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT status, outcome_event_id FROM outbox WHERE action_id = ?",
                (action_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown action id: {action_id}")
            if row["status"] == "delivered" and row["outcome_event_id"] == event.id:
                db.rollback()
                return False
            if row["status"] not in {"planned", "rendered", "queued"}:
                raise ValueError(f"cannot deliver action in {row['status']} state")
            db.execute(
                "INSERT INTO events(id, at, kind, payload) VALUES(?, ?, ?, ?)",
                (event.id, event.at.isoformat(), event.kind.value, encoded),
            )
            db.execute(
                """UPDATE outbox SET status='delivered', updated_at=?, outcome_event_id=?
                   WHERE action_id=?""",
                (at.isoformat(), event.id, action_id),
            )
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def pending_actions(self) -> tuple["OutboxAction", ...]:
        with _connect(self.path) as db:
            rows = db.execute(
                "SELECT * FROM outbox WHERE status IN ('rendered', 'queued') ORDER BY created_at"
            ).fetchall()
        return tuple(OutboxAction.from_row(row) for row in rows)

    def cancel_pending(self, at: datetime) -> int:
        with _connect(self.path) as db:
            cursor = db.execute(
                """UPDATE outbox SET status='cancelled', updated_at=?
                   WHERE status IN ('planned', 'rendered', 'queued')""",
                (at.isoformat(),),
            )
            return cursor.rowcount


@dataclass(frozen=True, slots=True)
class OutboxAction:
    action_id: str
    source_event_id: str
    decision_event_id: str
    candidate_id: str
    action: str
    proactive: bool
    content: str
    status: str
    created_at: datetime
    updated_at: datetime
    outcome_event_id: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "OutboxAction":
        return cls(
            action_id=row["action_id"],
            source_event_id=row["source_event_id"],
            decision_event_id=row["decision_event_id"],
            candidate_id=row["candidate_id"],
            action=row["action"],
            proactive=bool(row["proactive"]),
            content=row["content"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            outcome_event_id=row["outcome_event_id"],
        )
