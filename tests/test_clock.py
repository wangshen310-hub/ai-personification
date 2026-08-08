from datetime import UTC, datetime, timedelta

import pytest

from companion_kernel.clock import FakeClock


def test_fake_clock_advances_in_utc() -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    clock.advance(timedelta(hours=3))
    assert clock.now() == datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def test_fake_clock_rejects_negative_time() -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(seconds=-1))
