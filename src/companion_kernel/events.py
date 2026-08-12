from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import isfinite
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from companion_kernel.types import EventKind


class CorruptEventLog(RuntimeError):
    pass


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("event payload floats must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("event payload must contain JSON-compatible values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class KernelEvent:
    id: str
    at: datetime
    kind: EventKind
    payload: Mapping[str, object]
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("event id cannot be empty")
        if self.at.tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        recorded_at = self.recorded_at or self.at
        if recorded_at.tzinfo is None:
            raise ValueError("event recorded time must be timezone-aware")
        object.__setattr__(self, "recorded_at", recorded_at.astimezone(UTC))
        object.__setattr__(self, "payload", _freeze_json(dict(self.payload)))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "at": self.at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "KernelEvent":
        return cls(
            id=str(value["id"]),
            at=datetime.fromisoformat(str(value["at"])),
            kind=EventKind(str(value["kind"])),
            payload=dict(value["payload"]),
            recorded_at=(
                datetime.fromisoformat(str(value["recorded_at"]))
                if value.get("recorded_at") is not None
                else None
            ),
        )


class EventStore(Protocol):
    def append(self, event: KernelEvent) -> bool:
        raise NotImplementedError

    def contains(self, event_id: str) -> bool:
        raise NotImplementedError

    def read_all(self) -> tuple[KernelEvent, ...]:
        raise NotImplementedError

    def read_all_with_sequences(self) -> tuple[tuple[int, KernelEvent], ...]:
        raise NotImplementedError


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[KernelEvent] = []
        self._ids: set[str] = set()

    def append(self, event: KernelEvent) -> bool:
        if event.id in self._ids:
            return False
        self._events.append(event)
        self._ids.add(event.id)
        return True

    def contains(self, event_id: str) -> bool:
        return event_id in self._ids

    def read_all(self) -> tuple[KernelEvent, ...]:
        return tuple(self._events)

    def read_all_with_sequences(self) -> tuple[tuple[int, KernelEvent], ...]:
        return tuple(enumerate(self._events, start=1))


class JsonlEventStore(InMemoryEventStore):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            for line_number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    raw = json.loads(line)
                    event = KernelEvent.from_dict(raw)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CorruptEventLog(f"invalid event at line {line_number}") from exc
                if not super().append(event):
                    raise CorruptEventLog(f"duplicate event id at line {line_number}")

    def append(self, event: KernelEvent) -> bool:
        if self.contains(event.id):
            return False
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return super().append(event)
