from datetime import UTC, datetime

import pytest

from companion_kernel.events import CorruptEventLog, JsonlEventStore, KernelEvent
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
