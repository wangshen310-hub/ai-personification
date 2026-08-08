from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Mapping

from companion_kernel.drives import DriveState
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EmotionLabel


def _unit(value: object, name: str, default: float) -> float:
    number = default if value is None else float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and inside [0, 1]")
    return number


@dataclass(frozen=True, slots=True)
class Appraisal:
    controllability: float
    uncertainty: float
    relational_significance: float
    blocked_goal: float
    risk: float

    def __post_init__(self) -> None:
        for name in (
            "controllability",
            "uncertainty",
            "relational_significance",
            "blocked_goal",
            "risk",
        ):
            _unit(getattr(self, name), name, 0.0)

    @classmethod
    def from_event(cls, event: KernelEvent) -> "Appraisal":
        return cls(
            controllability=_unit(event.payload.get("controllability"), "controllability", 0.5),
            uncertainty=_unit(event.payload.get("uncertainty"), "uncertainty", 0.0),
            relational_significance=_unit(
                event.payload.get("relational_significance"),
                "relational_significance",
                0.0,
            ),
            blocked_goal=_unit(event.payload.get("blocked_goal"), "blocked_goal", 0.0),
            risk=_unit(event.payload.get("risk"), "risk", 0.0),
        )


@dataclass(frozen=True, slots=True)
class EmotionState:
    label: EmotionLabel
    valence: float
    arousal: float
    intensity: float
    mood_valence: float
    updated_at: datetime

    @classmethod
    def neutral(cls, at: datetime) -> "EmotionState":
        return cls(EmotionLabel.NEUTRAL, 0.0, 0.0, 0.0, 0.0, at)


class EmotionEvaluator:
    def evaluate(
        self,
        before: Mapping[DriveKind, DriveState],
        after: Mapping[DriveKind, DriveState],
        urgencies: Mapping[DriveKind, float],
        appraisal: Appraisal,
        previous: EmotionState,
        at: datetime,
    ) -> EmotionState:
        valence = sum(after[kind].value - before[kind].value for kind in DriveKind) / len(DriveKind)
        arousal = min(1.0, max(urgencies.values(), default=0.0) + 0.25 * appraisal.uncertainty)
        connection_urgency = urgencies.get(DriveKind.CONNECTION, 0.0)

        if appraisal.risk >= 0.6 and appraisal.uncertainty >= 0.5:
            label = EmotionLabel.WORRY
        elif connection_urgency >= 0.12:
            label = EmotionLabel.SADNESS if appraisal.controllability < 0.3 else EmotionLabel.LONGING
        elif appraisal.blocked_goal >= 0.6 and appraisal.controllability >= 0.4:
            label = EmotionLabel.FRUSTRATION
        elif (
            after[DriveKind.CONNECTION].value >= 0.70
            and after[DriveKind.CARE].value >= 0.70
            and appraisal.relational_significance >= 0.6
        ):
            label = EmotionLabel.WARMTH
        elif valence >= 0.05:
            label = EmotionLabel.RELIEF
        else:
            label = EmotionLabel.NEUTRAL

        intensity = min(1.0, max(abs(valence), arousal))
        mood_valence = max(-1.0, min(1.0, 0.90 * previous.mood_valence + 0.10 * valence))
        return EmotionState(label, valence, arousal, intensity, mood_valence, at)
