from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

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
from companion_kernel.storage import SQLiteRuntimeStore


@dataclass(frozen=True, slots=True)
class KernelResult:
    state: KernelState
    decision: PolicyDecision | None
    duplicate: bool


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
        events = self._events.read_all()
        try:
            snapshot = self._snapshots.load()
        except SnapshotCorrupt:
            snapshot = None
        if snapshot is not None and snapshot.version <= len(events):
            state = snapshot
            tail = events[snapshot.version :]
        else:
            state = self._initial(events[0].at)
            tail = events
        for event in tail:
            state = self._reduce(state, event)
        self._snapshots.save(state)
        return state

    def _reduce(self, state: KernelState, event: KernelEvent) -> KernelState:
        if event.at < state.last_event_at:
            raise ValueError("event time cannot move backwards")
        before = state.drive_map()
        after = self._homeostasis.apply_event(before, event, resolve_event_impacts(event))
        urgencies = self._homeostasis.urgencies(after, event.at)
        emotion = self._emotions.evaluate(
            before,
            after,
            urgencies,
            Appraisal.from_event(event),
            state.emotion,
            event.at,
        )
        paused = state.paused
        awaiting_reply = state.awaiting_reply
        cutoff = event.at - timedelta(
            hours=max(24, self._config.system.unanswered_cooldown_hours + 24)
        )
        proactive_sent_at = tuple(item for item in state.proactive_sent_at if item > cutoff)
        if event.kind is EventKind.USER_PAUSE:
            paused = True
        elif event.kind is EventKind.USER_RESUME:
            paused = False
        elif event.kind is EventKind.USER_MESSAGE:
            awaiting_reply = False
        elif event.kind is EventKind.PROACTIVE_SENT:
            awaiting_reply = True
            proactive_sent_at = proactive_sent_at + (event.at,)
        return KernelState(
            version=state.version + 1,
            last_event_at=event.at,
            drives=tuple(sorted(after.items(), key=lambda pair: pair[0].value)),
            emotion=emotion,
            relationship=evolve_relationship(state.relationship, event),
            paused=paused,
            awaiting_reply=awaiting_reply,
            proactive_sent_at=proactive_sent_at,
        )

    def urgencies(self) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(self._state.drive_map(), self._clock.now())

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

    def preview(self, event: KernelEvent) -> KernelState:
        """Reduce an event without committing it.

        Agent adapters use this to build context from the post-event state while
        keeping the normal ``process`` method as the only commit path.
        """

        if self._events.contains(event.id):
            return self._state
        return self._reduce(self._state, event)

    def urgencies_for(self, state: KernelState, at: datetime) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(state.drive_map(), at)

    def process(
        self,
        event: KernelEvent,
        candidates: tuple[CandidateIntent, ...] = (),
    ) -> KernelResult:
        if self._events.contains(event.id):
            return KernelResult(self._state, None, True)
        next_state = self._reduce(self._state, event)
        urgencies = self._homeostasis.urgencies(next_state.drive_map(), event.at)
        decision: PolicyDecision | None = None
        if candidates or event.kind is EventKind.DECISION_TICK:
            decision = self._policy.decide(
                candidates,
                urgencies,
                PolicyContext(
                    now=event.at,
                    user=self._config.user,
                    paused=next_state.paused,
                    awaiting_reply=next_state.awaiting_reply,
                    proactive_cycle=bool(
                        event.payload.get(
                            "proactive_cycle",
                            event.kind is EventKind.DECISION_TICK,
                        )
                    ),
                    proactive_sent_at=next_state.proactive_sent_at,
                ),
            )
        if not self._events.append(event):
            self._state = self._restore()
            return KernelResult(self._state, None, True)
        self._state = next_state
        self._snapshots.save(next_state)
        if event.kind is EventKind.USER_PAUSE and self._runtime_store is not None:
            self._runtime_store.cancel_pending(event.at)
        if decision is not None:
            self._audit.append(
                AuditEntry.from_decision(
                    event.id,
                    next_state.version,
                    event.at,
                    decision,
                    urgencies,
                )
            )
        return KernelResult(next_state, decision, False)
