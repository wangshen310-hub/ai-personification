from datetime import UTC, datetime

import pytest

from companion_kernel.events import CorruptEventLog, InMemoryEventStore, JsonlEventStore, KernelEvent
from companion_kernel.storage import SQLiteRuntimeStore
from companion_kernel.types import EventKind


def event(event_id: str = "evt-1") -> KernelEvent:
    return KernelEvent(
        id=event_id,
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        kind=EventKind.USER_MESSAGE,
        payload={"text_length": 12},
    )


def test_jsonl_store_is_idempotent_across_reopen(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    first = JsonlEventStore(path)
    assert first.append(event()) is True
    assert first.append(event()) is False

    reopened = JsonlEventStore(path)
    assert reopened.contains("evt-1") is True
    assert reopened.read_all() == (event(),)


def test_jsonl_store_rejects_corrupt_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(CorruptEventLog):
        JsonlEventStore(path)


def test_event_payload_is_deeply_immutable() -> None:
    item = KernelEvent(
        id="immutable",
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        kind=EventKind.USER_MESSAGE,
        payload={"items": [1, 2]},
    )
    with pytest.raises(TypeError):
        item.payload["other"] = 3
    assert item.payload["items"] == (1, 2)


@pytest.mark.parametrize("store_kind", ["memory", "sqlite"])
def test_tail_and_reverse_reads_match_full_log(tmp_path, store_kind: str) -> None:
    store = InMemoryEventStore() if store_kind == "memory" else SQLiteRuntimeStore(tmp_path / "runtime.db")
    # More than one SQLite page so reverse iteration has to cross a page boundary.
    for index in range(150):
        store.append(
            KernelEvent(
                id=f"evt-{index}",
                at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
                kind=EventKind.TIME_TICK if index % 3 == 0 else EventKind.USER_MESSAGE,
                payload={"index": index},
            )
        )

    full = store.read_all_with_sequences()
    assert store.latest_sequence() == 150
    assert store.read_after(0) == full
    assert store.read_after(140) == full[140:]
    assert store.read_after(150) == ()
    expected = tuple(
        item for _, item in reversed(full) if item.kind is EventKind.TIME_TICK
    )
    assert tuple(store.iter_reversed({EventKind.TIME_TICK})) == expected
    both = {EventKind.TIME_TICK, EventKind.USER_MESSAGE}
    assert tuple(store.iter_reversed(both)) == tuple(item for _, item in reversed(full))
    assert tuple(store.iter_reversed(())) == ()
