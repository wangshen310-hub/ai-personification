"""Transactional runtime persistence for events, configuration, memory, and actions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from companion_kernel.events import KernelEvent


SCHEMA_VERSION = 2


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
                    recorded_at TEXT NOT NULL,
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
                    claimed_by TEXT,
                    claim_until TEXT,
                    FOREIGN KEY(source_event_id) REFERENCES events(id),
                    FOREIGN KEY(decision_event_id) REFERENCES events(id)
                );
                CREATE INDEX IF NOT EXISTS memories_source_idx ON memories(source_event_id);
                CREATE INDEX IF NOT EXISTS outbox_status_idx ON outbox(status, created_at);
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(events)")}
            if "recorded_at" not in columns:
                db.execute("ALTER TABLE events ADD COLUMN recorded_at TEXT")
                db.execute("UPDATE events SET recorded_at = at WHERE recorded_at IS NULL")
            outbox_columns = {row["name"] for row in db.execute("PRAGMA table_info(outbox)")}
            if "claimed_by" not in outbox_columns:
                db.execute("ALTER TABLE outbox ADD COLUMN claimed_by TEXT")
            if "claim_until" not in outbox_columns:
                db.execute("ALTER TABLE outbox ADD COLUMN claim_until TEXT")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS outbox_decision_idx ON outbox(decision_event_id)"
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )

    def append(self, event: KernelEvent) -> bool:
        encoded = json.dumps(event.to_dict()["payload"], ensure_ascii=False, sort_keys=True)
        with _connect(self.path) as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO events(id, at, recorded_at, kind, payload) VALUES(?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.at.isoformat(),
                    event.recorded_at.isoformat(),
                    event.kind.value,
                    encoded,
                ),
            )
            return cursor.rowcount == 1

    def latest_sequence(self) -> int:
        with _connect(self.path) as db:
            row = db.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events").fetchone()
        return int(row["sequence"])

    def append_if_sequence(self, event: KernelEvent, expected_sequence: int) -> int | None:
        """Append one event only if the caller read the current event tail."""

        encoded = json.dumps(event.to_dict()["payload"], ensure_ascii=False, sort_keys=True)
        with _connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = int(
                db.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events").fetchone()[
                    "sequence"
                ]
            )
            if current != expected_sequence:
                db.rollback()
                return None
            try:
                cursor = db.execute(
                    "INSERT INTO events(id, at, recorded_at, kind, payload) VALUES(?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.at.isoformat(),
                        event.recorded_at.isoformat(),
                        event.kind.value,
                        encoded,
                    ),
                )
            except sqlite3.IntegrityError:
                db.rollback()
                return None
            sequence = int(cursor.lastrowid)
            db.commit()
            return sequence

    def contains(self, event_id: str) -> bool:
        with _connect(self.path) as db:
            row = db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone()
        return row is not None

    def read_all(self) -> tuple[KernelEvent, ...]:
        return tuple(event for _, event in self.read_all_with_sequences())

    def read_all_with_sequences(self) -> tuple[tuple[int, KernelEvent], ...]:
        with _connect(self.path) as db:
            rows = db.execute(
                "SELECT sequence, id, at, recorded_at, kind, payload FROM events ORDER BY sequence"
            ).fetchall()
        return tuple(
            (
                int(row["sequence"]),
                KernelEvent.from_dict(
                    {
                        "id": row["id"],
                        "at": row["at"],
                        "recorded_at": row["recorded_at"],
                        "kind": row["kind"],
                        "payload": json.loads(row["payload"]),
                    },
                ),
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
                    proactive, content, status, created_at, updated_at, claimed_by, claim_until)
                   VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    action.claimed_by,
                    action.claim_until.isoformat() if action.claim_until else None,
                ),
            )
            return cursor.rowcount == 1

    def append_event_and_action_if_sequence(
        self,
        event: KernelEvent,
        expected_sequence: int,
        action: "OutboxAction | None",
        *,
        cancel_pending_at: datetime | None = None,
    ) -> int | None:
        """Commit a decision event and its Outbox row in one SQLite transaction."""

        encoded = json.dumps(event.to_dict()["payload"], ensure_ascii=False, sort_keys=True)
        with _connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            current = int(
                db.execute("SELECT COALESCE(MAX(sequence), 0) AS sequence FROM events").fetchone()[
                    "sequence"
                ]
            )
            if current != expected_sequence:
                db.rollback()
                return None
            try:
                cursor = db.execute(
                    "INSERT INTO events(id, at, recorded_at, kind, payload) VALUES(?, ?, ?, ?, ?)",
                    (
                        event.id,
                        event.at.isoformat(),
                        event.recorded_at.isoformat(),
                        event.kind.value,
                        encoded,
                    ),
                )
                if action is not None:
                    db.execute(
                        """INSERT INTO outbox
                           (action_id, source_event_id, decision_event_id, candidate_id, action,
                            proactive, content, status, created_at, updated_at, claimed_by, claim_until)
                           VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                            action.claimed_by,
                            action.claim_until.isoformat() if action.claim_until else None,
                        ),
                    )
                if cancel_pending_at is not None:
                    db.execute(
                        """UPDATE outbox SET status='cancelled', updated_at=?, claimed_by=NULL, claim_until=NULL
                           WHERE status IN ('planned', 'rendered', 'queued', 'sending')""",
                        (cancel_pending_at.isoformat(),),
                    )
            except sqlite3.IntegrityError:
                db.rollback()
                return None
            sequence = int(cursor.lastrowid)
            db.commit()
            return sequence

    def get_action(self, action_id: str) -> "OutboxAction | None":
        with _connect(self.path) as db:
            row = db.execute("SELECT * FROM outbox WHERE action_id = ?", (action_id,)).fetchone()
        return None if row is None else OutboxAction.from_row(row)

    def get_action_for_decision(self, decision_event_id: str) -> "OutboxAction | None":
        with _connect(self.path) as db:
            row = db.execute(
                "SELECT * FROM outbox WHERE decision_event_id = ?",
                (decision_event_id,),
            ).fetchone()
        return None if row is None else OutboxAction.from_row(row)

    def pending_proactive_times(self) -> tuple[datetime, ...]:
        with _connect(self.path) as db:
            rows = db.execute(
                """SELECT created_at FROM outbox
                   WHERE proactive = 1 AND status IN ('planned', 'rendered', 'queued', 'sending')
                   ORDER BY created_at"""
            ).fetchall()
        return tuple(datetime.fromisoformat(row["created_at"]) for row in rows)

    def claim_actions(
        self,
        worker_id: str,
        *,
        now: datetime,
        lease_seconds: int = 300,
        limit: int = 16,
    ) -> tuple["OutboxAction", ...]:
        """Atomically claim pending actions so multiple channel workers cannot send twice."""

        if not worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if lease_seconds <= 0 or limit <= 0:
            raise ValueError("lease_seconds and limit must be positive")
        if now.tzinfo is None:
            raise ValueError("claim time must be timezone-aware")
        now_utc = now.astimezone(UTC)
        claim_time = now_utc + timedelta(seconds=lease_seconds)
        with _connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """SELECT action_id FROM outbox
                   WHERE status IN ('planned', 'rendered', 'queued')
                      OR (status = 'sending' AND (claim_until IS NULL OR julianday(claim_until) <= julianday(?)))
                   ORDER BY created_at LIMIT ?""",
                (now_utc.isoformat(), limit),
            ).fetchall()
            ids = [row["action_id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(
                    f"""UPDATE outbox SET status='sending', claimed_by=?, claim_until=?, updated_at=?
                        WHERE action_id IN ({placeholders})""",
                    (worker_id, claim_time.isoformat(), now_utc.isoformat(), *ids),
                )
            db.commit()
        claimed = tuple(self.get_action(action_id) for action_id in ids)
        return tuple(item for item in claimed if item is not None)

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
                f"""UPDATE outbox SET status = ?, updated_at = ?, outcome_event_id = COALESCE(?, outcome_event_id),
                           claimed_by = NULL, claim_until = NULL
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
            if row["status"] not in {"planned", "rendered", "queued", "sending"}:
                raise ValueError(f"cannot deliver action in {row['status']} state")
            db.execute(
                "INSERT INTO events(id, at, recorded_at, kind, payload) VALUES(?, ?, ?, ?, ?)",
                (
                    event.id,
                    event.at.isoformat(),
                    event.recorded_at.isoformat(),
                    event.kind.value,
                    encoded,
                ),
            )
            db.execute(
                """UPDATE outbox SET status='delivered', updated_at=?, outcome_event_id=?,
                           claimed_by=NULL, claim_until=NULL
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
                "SELECT * FROM outbox WHERE status IN ('planned', 'rendered', 'queued', 'sending') ORDER BY created_at"
            ).fetchall()
        return tuple(OutboxAction.from_row(row) for row in rows)

    def cancel_pending(self, at: datetime) -> int:
        with _connect(self.path) as db:
            cursor = db.execute(
                """UPDATE outbox SET status='cancelled', updated_at=?, claimed_by=NULL, claim_until=NULL
                   WHERE status IN ('planned', 'rendered', 'queued', 'sending')""",
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
    claimed_by: str | None = None
    claim_until: datetime | None = None

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
            claimed_by=row["claimed_by"] if "claimed_by" in row.keys() else None,
            claim_until=(
                datetime.fromisoformat(row["claim_until"])
                if "claim_until" in row.keys() and row["claim_until"]
                else None
            ),
        )
