from datetime import UTC, datetime, timedelta
import random

from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.events import KernelEvent
from companion_kernel.types import EventKind


def test_100_seed_event_sweep_preserves_homeostasis_invariants() -> None:
    start = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    for seed in range(100):
        rng = random.Random(seed)
        engine = HomeostasisEngine.defaults()
        state = engine.initial_state(start)
        current = start
        for index in range(200):
            current += timedelta(minutes=rng.randint(0, 720))
            kind = rng.choice(tuple(EventKind))
            event = KernelEvent(f"{seed}-{index}", current, kind, {})
            state = engine.apply_event(state, event, resolve_event_impacts(event))
            urgency = engine.urgencies(state, current)
            assert all(0.0 <= item.value <= 1.0 for item in state.values())
            assert all(0.0 <= value <= 1.0 for value in urgency.values())
