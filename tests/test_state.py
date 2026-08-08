from datetime import UTC, datetime
import json

import pytest

from companion_kernel.drives import HomeostasisEngine
from companion_kernel.emotions import EmotionState
from companion_kernel.state import KernelState, SnapshotCorrupt, SnapshotRepository


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def state() -> KernelState:
    drives = HomeostasisEngine.defaults().initial_state(NOW)
    return KernelState.initial(NOW, drives, EmotionState.neutral(NOW))


def test_snapshot_round_trip(tmp_path) -> None:
    repository = SnapshotRepository(tmp_path / "state.json")
    repository.save(state())
    assert repository.load() == state()


def test_snapshot_checksum_detects_tampering(tmp_path) -> None:
    path = tmp_path / "state.json"
    repository = SnapshotRepository(path)
    repository.save(state())
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["state"]["version"] = 99
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(SnapshotCorrupt):
        repository.load()
