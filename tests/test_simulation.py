from datetime import UTC, datetime

from companion_kernel.simulation import SimulationRunner


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def test_no_reply_scenario_sends_only_once(tmp_path) -> None:
    report = SimulationRunner(tmp_path / "silent", START, seed=7).run(
        days=30,
        user_reply_every_days=None,
    )
    assert report.proactive_messages == 1
    assert report.boundary_violations == 0


def test_daily_reply_scenario_never_exceeds_one_message_per_day(tmp_path) -> None:
    report = SimulationRunner(tmp_path / "daily", START, seed=7).run(
        days=180,
        user_reply_every_days=1,
    )
    assert report.proactive_messages <= 180
    assert report.boundary_violations == 0
    assert report.min_drive_value >= 0.0
    assert report.max_drive_value <= 1.0


def test_same_seed_produces_same_final_digest(tmp_path) -> None:
    first = SimulationRunner(tmp_path / "first", START, seed=42).run(30, 3)
    second = SimulationRunner(tmp_path / "second", START, seed=42).run(30, 3)
    assert first.final_state_digest == second.final_state_digest
