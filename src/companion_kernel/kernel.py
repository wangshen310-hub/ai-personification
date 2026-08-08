from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from companion_kernel.audit import AuditEntry, JsonlAuditLog
from companion_kernel.clock import Clock
from companion_kernel.config import ConfigStore
from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.emotions import Appraisal, EmotionEvaluator, EmotionState
from companion_kernel.events import EventStore, JsonlEventStore, KernelEvent
from companion_kernel.policy import CandidateIntent, PolicyContext, PolicyDecision, PolicyEngine
from companion_kernel.state import KernelState, SnapshotCorrupt, SnapshotRepository
from companion_kernel.types import DriveKind, EventKind


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
        self._ensure_bootstrap()
        self._state = self._restore()

    @classmethod
    def open(cls, runtime_dir: Path, clock: Clock, config: ConfigStore) -> "PersonalityKernel":
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            clock,
            config,
            JsonlEventStore(runtime_dir / "events.jsonl"),
            SnapshotRepository(runtime_dir / "state.json"),
            JsonlAuditLog(runtime_dir / "audit.jsonl"),
        )

    @property
    def state(self) -> KernelState:
        return self._state

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
        cutoff = event.at - timedelta(hours=24)
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
            paused=paused,
            awaiting_reply=awaiting_reply,
            proactive_sent_at=proactive_sent_at,
        )

    def urgencies(self) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(self._state.drive_map(), self._clock.now())

    def contains_event(self, event_id: str) -> bool:
        """Return whether an event has already been committed."""

        return self._events.contains(event_id)

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
                    proactive_cycle=event.kind is EventKind.DECISION_TICK,
                    proactive_sent_at=next_state.proactive_sent_at,
                ),
            )
        if not self._events.append(event):
            self._state = self._restore()
            return KernelResult(self._state, None, True)
        self._state = next_state
        self._snapshots.save(next_state)
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
