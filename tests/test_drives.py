from datetime import UTC, datetime, timedelta
import random

import pytest

from companion_kernel.config import LearnedPersona
from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def make_event(event_id: str, kind: EventKind, at: datetime) -> KernelEvent:
    return KernelEvent(id=event_id, at=at, kind=kind, payload={})


def test_silence_depletes_connection_and_reduces_interaction_load() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(START)
    after = engine.advance(before, START + timedelta(hours=24))

    assert after[DriveKind.CONNECTION].value < before[DriveKind.CONNECTION].value
    assert after[DriveKind.RHYTHM].value < before[DriveKind.RHYTHM].value


def test_user_message_updates_drives_once() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(START)
    event = make_event("message-1", EventKind.USER_MESSAGE, START)
    once = engine.apply_event(before, event, resolve_event_impacts(event))
    twice = engine.apply_event(once, event, resolve_event_impacts(event))

    assert once[DriveKind.CONNECTION].value == pytest.approx(0.88)
    assert twice == once


def test_urgency_is_zero_inside_target_and_capped_outside() -> None:
    engine = HomeostasisEngine.defaults()
    state = engine.initial_state(START)
    assert engine.urgencies(state, START)[DriveKind.CONNECTION] == 0.0

    late = engine.advance(state, START + timedelta(days=200))
    urgency = engine.urgencies(late, START + timedelta(days=200))[DriveKind.CONNECTION]
    assert 0.0 < urgency <= 1.0


def test_random_event_sequences_keep_all_values_bounded() -> None:
    rng = random.Random(42)
    engine = HomeostasisEngine.defaults()
    state = engine.initial_state(START)
    current = START
    kinds = tuple(EventKind)

    for index in range(500):
        current += timedelta(hours=rng.randint(0, 12))
        event = make_event(f"evt-{index}", rng.choice(kinds), current)
        state = engine.apply_event(state, event, resolve_event_impacts(event))

    assert all(0.0 <= item.value <= 1.0 for item in state.values())


def test_learned_trait_changes_soft_urgency_but_not_cap() -> None:
    baseline = HomeostasisEngine.defaults()
    sensitive = HomeostasisEngine.defaults(
        LearnedPersona(drive_weight_offsets=((DriveKind.CONNECTION, 0.25),))
    )
    low = baseline.advance(baseline.initial_state(START), START + timedelta(days=7))
    normal = baseline.urgencies(low, START + timedelta(days=7))[DriveKind.CONNECTION]
    weighted = sensitive.urgencies(low, START + timedelta(days=7))[DriveKind.CONNECTION]
    assert normal < weighted <= 1.0
