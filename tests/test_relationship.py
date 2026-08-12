from datetime import UTC, datetime

from companion_kernel.events import KernelEvent
from companion_kernel.relationship import RelationshipState, evolve_relationship
from companion_kernel.types import EventKind


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def event(index: int, kind: EventKind) -> KernelEvent:
    return KernelEvent(f"relationship-{index}", NOW, kind, {})


def test_relationship_grows_slowly_from_reciprocal_dialogue() -> None:
    state = RelationshipState.initial()
    for index in range(5):
        state = evolve_relationship(state, event(index, EventKind.USER_MESSAGE))
        state = evolve_relationship(state, event(index + 10, EventKind.ASSISTANT_MESSAGE_SENT))

    assert 0.05 < state.familiarity < 0.20
    assert state.trust > 0.50
    assert state.reciprocity > 0.50


def test_relationship_tracks_repair_and_contradiction_separately() -> None:
    initial = RelationshipState.initial()
    damaged = evolve_relationship(initial, event(1, EventKind.CONTRADICTION))
    repaired = evolve_relationship(damaged, event(2, EventKind.BOUNDARY_RESPECTED))

    assert damaged.trust < initial.trust
    assert repaired.trust > damaged.trust
    assert repaired.boundary_clarity > initial.boundary_clarity


def test_relationship_values_remain_bounded() -> None:
    state = RelationshipState.initial()
    for index in range(1_000):
        state = evolve_relationship(state, event(index, EventKind.USER_MESSAGE))

    assert all(0.0 <= value <= 1.0 for value in state.to_dict().values())
