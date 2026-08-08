from datetime import UTC, datetime

import pytest

from companion_kernel.drives import HomeostasisEngine
from companion_kernel.emotions import Appraisal, EmotionEvaluator, EmotionState
from companion_kernel.types import DriveKind, EmotionLabel


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def test_connection_deficit_maps_to_longing_when_controllable() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(NOW)
    after = dict(before)
    after[DriveKind.CONNECTION] = after[DriveKind.CONNECTION].__class__(
        value=0.10,
        unmet_since=NOW,
        last_updated_at=NOW,
        evidence=("silence",),
    )
    state = EmotionEvaluator().evaluate(
        before=before,
        after=after,
        urgencies={**engine.urgencies(after, NOW), DriveKind.CONNECTION: 0.5},
        appraisal=Appraisal(
            controllability=0.7,
            uncertainty=0.2,
            relational_significance=0.9,
            blocked_goal=0.2,
            risk=0.1,
        ),
        previous=EmotionState.neutral(NOW),
        at=NOW,
    )
    assert state.label is EmotionLabel.LONGING


@pytest.mark.parametrize(
    ("risk", "uncertainty", "expected"),
    [(0.8, 0.7, EmotionLabel.WORRY), (0.1, 0.1, EmotionLabel.NEUTRAL)],
)
def test_risk_and_uncertainty_control_worry(
    risk: float,
    uncertainty: float,
    expected: EmotionLabel,
) -> None:
    engine = HomeostasisEngine.defaults()
    drives = engine.initial_state(NOW)
    state = EmotionEvaluator().evaluate(
        before=drives,
        after=drives,
        urgencies=engine.urgencies(drives, NOW),
        appraisal=Appraisal(0.5, uncertainty, 0.2, 0.0, risk),
        previous=EmotionState.neutral(NOW),
        at=NOW,
    )
    assert state.label is expected


def test_mood_moves_more_slowly_than_event_valence() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(NOW)
    after = {
        kind: item.__class__(1.0, None, NOW, ("positive",))
        for kind, item in before.items()
    }
    state = EmotionEvaluator().evaluate(
        before,
        after,
        engine.urgencies(after, NOW),
        Appraisal(0.8, 0.0, 0.8, 0.0, 0.0),
        EmotionState.neutral(NOW),
        NOW,
    )
    assert state.valence > 0
    assert 0 < state.mood_valence < state.valence
