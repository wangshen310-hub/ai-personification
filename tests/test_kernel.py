from datetime import UTC, datetime, timedelta

import pytest

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import InMemoryEventStore, KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.state import KernelState
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def candidate() -> CandidateIntent:
    return CandidateIntent(
        id="send-check-in",
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 1.0),),
        relationship_health=0.8,
        value_alignment=0.8,
        intrusion_cost=0.1,
        risk=0.0,
        repetition=0.0,
        safety=SafetySignals(assessment_complete=True),
    )


def open_kernel(tmp_path, clock: FakeClock) -> PersonalityKernel:
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    return PersonalityKernel.open(tmp_path, clock, config)


def event(event_id: str, kind: EventKind, clock: FakeClock, **payload: object) -> KernelEvent:
    return KernelEvent(event_id, clock.now(), kind, payload)


def test_duplicate_event_does_not_change_state_version(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    message = event("message-1", EventKind.USER_MESSAGE, clock)
    first = kernel.process(message)
    duplicate = kernel.process(message)

    assert duplicate.duplicate is True
    assert duplicate.state == first.state


def test_pause_beats_high_connection_urgency_and_is_audited(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    clock.advance(timedelta(days=30))
    result = kernel.process(
        event("pause-1", EventKind.USER_PAUSE, clock),
        candidates=(candidate(),),
    )

    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    audit = JsonlAuditLog(tmp_path / "audit.jsonl").read_all()
    assert "user_paused" in audit[-1].evaluations[0][3]


def test_sent_message_locks_until_user_returns(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    kernel.process(event("sent-1", EventKind.PROACTIVE_SENT, clock))
    assert kernel.state.awaiting_reply is True

    clock.advance(timedelta(hours=1))
    kernel.process(event("reply-1", EventKind.USER_MESSAGE, clock))
    assert kernel.state.awaiting_reply is False


def test_corrupt_snapshot_rebuilds_identical_state_from_events(tmp_path) -> None:
    clock = FakeClock(START)
    first = open_kernel(tmp_path, clock)
    first.process(event("message-1", EventKind.USER_MESSAGE, clock))
    expected = first.state

    (tmp_path / "state.json").write_text("corrupt", encoding="utf-8")
    reopened = open_kernel(tmp_path, clock)
    assert reopened.state == expected


def test_event_remains_applied_when_snapshot_write_fails(tmp_path) -> None:
    class FailSecondSave:
        def __init__(self) -> None:
            self.saved: KernelState | None = None
            self.calls = 0

        def load(self) -> KernelState | None:
            return self.saved

        def save(self, value: KernelState) -> None:
            self.calls += 1
            if self.calls == 2:
                raise OSError("disk full")
            self.saved = value

    clock = FakeClock(START)
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    store = InMemoryEventStore()
    snapshots = FailSecondSave()
    kernel = PersonalityKernel(
        clock,
        config,
        store,
        snapshots,
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )
    message = event("message-after-bootstrap", EventKind.USER_MESSAGE, clock)
    with pytest.raises(OSError, match="disk full"):
        kernel.process(message)

    duplicate = kernel.process(message)
    assert duplicate.duplicate is True
    assert duplicate.state.version == 2
