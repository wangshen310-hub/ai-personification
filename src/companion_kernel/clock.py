from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("clock requires timezone-aware datetime")
        self._current = current.astimezone(UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("clock cannot move backwards")
        self._current += delta
