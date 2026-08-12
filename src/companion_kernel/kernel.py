from dataclasses import dataclass, replace
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Callable

from companion_kernel.audit import AuditEntry, JsonlAuditLog
from companion_kernel.clock import Clock
from companion_kernel.config import ConfigStore, LearnedPersona
from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.emotions import Appraisal, EmotionEvaluator, EmotionState
from companion_kernel.events import EventStore, JsonlEventStore, KernelEvent
from companion_kernel.policy import CandidateIntent, PolicyContext, PolicyDecision, PolicyEngine
from companion_kernel.relationship import evolve_relationship
from companion_kernel.state import KernelState, SnapshotCorrupt, SnapshotRepository
from companion_kernel.types import ConfigActor, DriveKind, EventKind
from companion_kernel.storage import OutboxAction, SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class KernelResult:
    state: KernelState
    decision: PolicyDecision | None
    duplicate: bool


ActionBuilder = Callable[[PolicyDecision, KernelState], OutboxAction | None]


class PersonalityKernel:
    def __init__(
        self,
        clock: Clock,
        config: ConfigStore,
        event_store: EventStore,
        snapshots: SnapshotRepository,
        audit: JsonlAuditLog,
    ) -> None:
        self._clock = clock
        self._config = config
        self._events = event_store
        self._snapshots = snapshots
        self._audit = audit
        self._homeostasis = HomeostasisEngine.defaults(config.learned)
        self._emotions = EmotionEvaluator()
        self._policy = PolicyEngine(config.system)
        self._runtime_store = event_store if isinstance(event_store, SQLiteRuntimeStore) else None
        self._ensure_bootstrap()
        self._state = self._restore()

    @classmethod
    def open(cls, runtime_dir: Path, clock: Clock, config: ConfigStore) -> "PersonalityKernel":
        runtime_dir.mkdir(parents=True, exist_ok=True)
        store = SQLiteRuntimeStore(runtime_dir / "runtime.db")
        legacy_path = runtime_dir / "events.jsonl"
        if not store.read_all() and legacy_path.exists():
            store.import_events(JsonlEventStore(legacy_path).read_all())
        return cls(
            clock,
            config,
            store,
            SnapshotRepository(runtime_dir / "state.json"),
            JsonlAuditLog(runtime_dir / "audit.jsonl"),
        )

    @property
    def state(self) -> KernelState:
        return self._state

    @property
    def runtime_store(self) -> SQLiteRuntimeStore | None:
        return self._runtime_store

    def _ensure_bootstrap(self) -> None:
        if self._events.read_all():
            return
        event = KernelEvent(
            id="__kernel_created__",
            at=self._clock.now(),
            kind=EventKind.TIME_TICK,
            payload={"bootstrap": True},
        )
        self._events.append(event)

    def _initial(self, at: datetime) -> KernelState:
        drives = self._homeostasis.initial_state(at)
        return KernelState.initial(at, drives, EmotionState.neutral(at))

    def _restore(self) -> KernelState:
        entries = self._events.read_all_with_sequences()
        events = tuple(event for _, event in entries)
        if not events:
            raise RuntimeError("event store must contain the bootstrap event")
        try:
            snapshot = self._snapshots.load()
        except SnapshotCorrupt:
            snapshot = None
        snapshot_valid = (
            snapshot is not None
            and snapshot.event_sequence == snapshot.version
            and snapshot.event_sequence <= len(entries)
            and (snapshot.event_sequence == 0 or bool(snapshot.event_digest))
        )
        if snapshot_valid:
            state = snapshot
            tail = entries[snapshot.event_sequence :]
            digest = snapshot.event_digest
        else:
            state = self._initial(events[0].at)
            tail = entries
            digest = ""
        for sequence, event in tail:
            state = self._reduce(state, event)
            digest = _extend_digest(digest, event)
            state = replace(state, event_sequence=sequence, event_digest=digest)
        return state

    def _reduce(self, state: KernelState, event: KernelEvent) -> KernelState:
        effective_at = max(event.at, state.last_event_at)
        before = state.drive_map()
        after = self._homeostasis.apply_event(before, event, resolve_event_impacts(event))
        urgencies = self._homeostasis.urgencies(after, effective_at)
        emotion = self._emotions.evaluate(
            before,
            after,
            urgencies,
            Appraisal.from_event(event),
            state.emotion,
            effective_at,
        )
        paused = state.paused
        awaiting_reply = state.awaiting_reply
        cutoff = effective_at - timedelta(
            hours=max(24, self._config.system.unanswered_cooldown_hours + 24)
        )
        proactive_sent_at = tuple(item for item in state.proactive_sent_at if item > cutoff)
        if awaiting_reply and not proactive_sent_at:
            awaiting_reply = False
        if event.kind is EventKind.USER_PAUSE:
            paused = True
        elif event.kind is EventKind.USER_RESUME:
            paused = False
        elif event.kind is EventKind.USER_MESSAGE:
            awaiting_reply = False
        elif event.kind is EventKind.PROACTIVE_SENT:
            awaiting_reply = True
            proactive_sent_at = proactive_sent_at + (effective_at,)
        return KernelState(
            version=state.version + 1,
            last_event_at=effective_at,
            drives=tuple(sorted(after.items(), key=lambda pair: pair[0].value)),
            emotion=emotion,
            relationship=evolve_relationship(state.relationship, event),
            paused=paused,
            awaiting_reply=awaiting_reply,
            proactive_sent_at=proactive_sent_at,
        )

    def urgencies(self) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(self._state.drive_map(), self._clock.now())

    def now(self) -> datetime:
        return self._clock.now()

    def contains_event(self, event_id: str) -> bool:
        """Return whether an event has already been committed."""

        return self._events.contains(event_id)

    def refresh(self, *, duplicate: bool = False) -> KernelResult:
        """Reload state after another transactional component commits an event."""

        self._state = self._restore()
        return KernelResult(self._state, None, duplicate)

    def recent_dialogue(self, limit: int = 8) -> tuple[str, ...]:
        """Return bounded, persisted dialogue context in chronological order."""

        if not 0 <= limit <= 32:
            raise ValueError("dialogue history limit must be between 0 and 32")
        if limit == 0:
            return ()
        dialogue: list[str] = []
        for event in reversed(self._events.read_all()):
            role: str | None = None
            text: object = None
            if event.kind is EventKind.USER_MESSAGE:
                role = "user"
                for key in ("message", "text", "content"):
                    if isinstance(event.payload.get(key), str):
                        text = event.payload[key]
                        break
            elif event.kind in {
                EventKind.ASSISTANT_MESSAGE_SENT,
                EventKind.PROACTIVE_SENT,
            }:
                role = "assistant"
                text = event.payload.get("message")
            elif event.kind is EventKind.INTERNAL_NOTE_CREATED:
                role = "internal_note"
                text = event.payload.get("note")
            if role is not None and isinstance(text, str) and text.strip():
                dialogue.append(f"{role}: {text[:2_000]}")
                if len(dialogue) >= limit:
                    break
        return tuple(reversed(dialogue))

    def recent_memories(self, limit: int = 8) -> tuple[str, ...]:
        if self._runtime_store is None:
            return ()
        return self._runtime_store.recent_memories(limit)

    def consider_persona_learning(self, kind: EventKind) -> None:
        """Apply a small drive-weight change only after repeated trusted evidence."""

        if self._runtime_store is None:
            return
        drive = {
            EventKind.USER_APPRECIATION: DriveKind.CONNECTION,
            EventKind.PREFERENCE_STATED: DriveKind.CURIOSITY,
            EventKind.CONFLICT_DETECTED: DriveKind.COHERENCE,
            EventKind.MEMORY_CORRECTED: DriveKind.COHERENCE,
        }.get(kind)
        if drive is None:
            return
        count = self._runtime_store.memory_count(kind.value)
        if count < 3 or count % 3 != 0:
            return
        offsets = dict(self._config.learned.drive_weight_offsets)
        offsets[drive] = min(0.25, offsets.get(drive, 0.0) + 0.02)
        learned = LearnedPersona(
            tuple(sorted(offsets.items(), key=lambda item: item[0].value))
        )
        self._config.replace_learned(learned, actor=ConfigActor.KERNEL)
        self._homeostasis = HomeostasisEngine.defaults(learned)

    def persona_context(self) -> tuple[str, ...]:
        """Return stable identity anchors configured outside the model."""

        return self._config.persona.context()

    def persona_values(self) -> tuple[str, ...]:
        return self._config.persona.values

    def preview(self, event: KernelEvent) -> KernelState:
        """Reduce an event without committing it.

        Agent adapters use this to build context from the post-event state while
        keeping the normal ``process`` method as the only commit path.
        """

        self._state = self._restore()
        if self._events.contains(event.id):
            return self._state
        return self._reduce(self._state, event)

    def urgencies_for(self, state: KernelState, at: datetime) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(state.drive_map(), at)

    def preview_decision(
        self,
        event: KernelEvent,
        candidates: tuple[CandidateIntent, ...],
    ) -> PolicyDecision:
        """Evaluate a decision against the newest committed state without writing it."""

        self._state = self._restore()
        next_state = self._state if self._events.contains(event.id) else self._reduce(self._state, event)
        return self._decide(event, next_state, candidates)

    def _decide(
        self,
        event: KernelEvent,
        next_state: KernelState,
        candidates: tuple[CandidateIntent, ...],
    ) -> PolicyDecision | None:
        if not candidates and event.kind is not EventKind.DECISION_TICK:
            return None
        now = max(event.at, next_state.last_event_at)
        urgencies = self._homeostasis.urgencies(next_state.drive_map(), now)
        pending = self._runtime_store.pending_proactive_times() if self._runtime_store else ()
        return self._policy.decide(
            candidates,
            urgencies,
            PolicyContext(
                now=now,
                user=self._config.user,
                paused=next_state.paused,
                awaiting_reply=next_state.awaiting_reply,
                proactive_cycle=bool(
                    event.payload.get("proactive_cycle", event.kind is EventKind.DECISION_TICK)
                ),
                proactive_sent_at=next_state.proactive_sent_at,
                proactive_reserved_at=pending,
            ),
        )

    def process(
        self,
        event: KernelEvent,
        candidates: tuple[CandidateIntent, ...] = (),
        action_builder: ActionBuilder | None = None,
    ) -> KernelResult:
        if action_builder is not None and self._runtime_store is None:
            raise RuntimeError("atomic action commits require SQLite runtime storage")
        for _ in range(8):
            self._state = self._restore()
            if self._events.contains(event.id):
                return KernelResult(self._state, None, True)
            next_state = self._reduce(self._state, event)
            decision = self._decide(event, next_state, candidates)
            action = action_builder(decision, next_state) if action_builder and decision else None
            expected_sequence = self._state.event_sequence
            if self._runtime_store is not None:
                sequence = self._runtime_store.append_event_and_action_if_sequence(
                    event,
                    expected_sequence,
                    action,
                    cancel_pending_at=(
                        max(event.at, next_state.last_event_at)
                        if event.kind is EventKind.USER_PAUSE
                        else None
                    ),
                )
            else:
                sequence = expected_sequence + 1 if self._events.append(event) else None
            if sequence is None:
                continue
            digest = _extend_digest(self._state.event_digest, event)
            committed = replace(next_state, event_sequence=sequence, event_digest=digest)
            self._state = committed
            self._snapshots.save(committed)
            if decision is not None:
                urgencies = self._homeostasis.urgencies(
                    committed.drive_map(), max(event.at, committed.last_event_at)
                )
                self._audit.append(
                    AuditEntry.from_decision(
                        event.id,
                        committed.version,
                        event.at,
                        decision,
                        urgencies,
                    )
                )
            return KernelResult(committed, decision, False)
        self._state = self._restore()
        raise RuntimeError("event commit conflicted repeatedly; retry the operation")


def _extend_digest(previous: str, event: KernelEvent) -> str:
    encoded = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((previous + encoded).encode("utf-8")).hexdigest()
