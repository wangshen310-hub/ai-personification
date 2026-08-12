from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from typing import Mapping

from companion_kernel.config import LearnedPersona
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class DriveConfig:
    target_low: float
    target_high: float
    base_weight: float
    sensitivity: float
    duration_rate_per_hour: float
    urgency_cap: float
    natural_rate_per_hour: float


@dataclass(frozen=True, slots=True)
class DriveState:
    value: float
    unmet_since: datetime | None
    last_updated_at: datetime
    evidence: tuple[str, ...] = ()


def default_drive_configs() -> dict[DriveKind, DriveConfig]:
    common = dict(
        target_low=0.55,
        target_high=1.0,
        base_weight=1.0,
        sensitivity=1.35,
        duration_rate_per_hour=0.002,
        urgency_cap=1.0,
    )
    return {
        DriveKind.CONNECTION: DriveConfig(**common, natural_rate_per_hour=-0.002),
        DriveKind.CARE: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.CURIOSITY: DriveConfig(**common, natural_rate_per_hour=-0.001),
        DriveKind.AUTONOMY: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.COHERENCE: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.RHYTHM: DriveConfig(**common, natural_rate_per_hour=0.01),
    }


EVENT_IMPACTS: dict[EventKind, dict[DriveKind, float]] = {
    EventKind.TIME_TICK: {},
    EventKind.USER_MESSAGE: {
        DriveKind.CONNECTION: 0.18,
        DriveKind.CURIOSITY: 0.04,
        DriveKind.RHYTHM: -0.03,
    },
    EventKind.USER_PAUSE: {DriveKind.AUTONOMY: 0.05},
    EventKind.USER_RESUME: {},
    EventKind.IMPORTANT_DATE: {DriveKind.CARE: -0.08},
    EventKind.COMMITMENT_DUE: {
        DriveKind.CARE: -0.15,
        DriveKind.COHERENCE: -0.08,
    },
    EventKind.BOUNDARY_RESPECTED: {DriveKind.AUTONOMY: 0.10},
    EventKind.CONTRADICTION: {DriveKind.COHERENCE: -0.20},
    EventKind.DECISION_TICK: {},
    EventKind.ASSISTANT_MESSAGE_SENT: {DriveKind.RHYTHM: -0.03},
    EventKind.INTERNAL_NOTE_CREATED: {
        DriveKind.COHERENCE: 0.08,
        DriveKind.RHYTHM: -0.01,
    },
    EventKind.PROACTIVE_SENT: {DriveKind.RHYTHM: -0.05},
}


def resolve_event_impacts(event: KernelEvent) -> Mapping[DriveKind, float]:
    return EVENT_IMPACTS[event.kind]


class HomeostasisEngine:
    def __init__(self, configs: Mapping[DriveKind, DriveConfig]) -> None:
        if set(configs) != set(DriveKind):
            raise ValueError("every drive kind requires configuration")
        self._configs = dict(configs)

    @classmethod
    def defaults(cls, learned: LearnedPersona | None = None) -> "HomeostasisEngine":
        configs = default_drive_configs()
        if learned is not None:
            for kind, offset in learned.drive_weight_offsets:
                current = configs[kind]
                configs[kind] = replace(
                    current,
                    base_weight=min(1.25, max(0.75, current.base_weight + offset)),
                )
        return cls(configs)

    def initial_state(self, at: datetime) -> dict[DriveKind, DriveState]:
        if at.tzinfo is None:
            raise ValueError("state time must be timezone-aware")
        return {
            kind: DriveState(value=0.70, unmet_since=None, last_updated_at=at)
            for kind in DriveKind
        }

    def advance(
        self,
        states: Mapping[DriveKind, DriveState],
        to_time: datetime,
    ) -> dict[DriveKind, DriveState]:
        result: dict[DriveKind, DriveState] = {}
        for kind, state in states.items():
            if to_time < state.last_updated_at:
                raise ValueError("drive time cannot move backwards")
            hours = (to_time - state.last_updated_at).total_seconds() / 3600
            config = self._configs[kind]
            value = min(1.0, max(0.0, state.value + config.natural_rate_per_hour * hours))
            unmet = state.unmet_since
            if config.target_low <= value <= config.target_high:
                unmet = None
            elif unmet is None:
                rate = config.natural_rate_per_hour
                if rate < 0 and state.value >= config.target_low and value < config.target_low:
                    crossing_hours = (state.value - config.target_low) / abs(rate)
                    unmet = state.last_updated_at + timedelta(hours=crossing_hours)
                elif rate > 0 and state.value <= config.target_high and value > config.target_high:
                    crossing_hours = (config.target_high - state.value) / rate
                    unmet = state.last_updated_at + timedelta(hours=crossing_hours)
                else:
                    unmet = state.last_updated_at
            result[kind] = replace(
                state,
                value=value,
                unmet_since=unmet,
                last_updated_at=to_time,
            )
        return result

    def apply_event(
        self,
        states: Mapping[DriveKind, DriveState],
        event: KernelEvent,
        impacts: Mapping[DriveKind, float],
    ) -> dict[DriveKind, DriveState]:
        if any(event.id in state.evidence for state in states.values()):
            return dict(states)
        result = self.advance(states, event.at)
        for kind, delta in impacts.items():
            if not isfinite(delta):
                raise ValueError("drive impact must be finite")
            state = result[kind]
            value = min(1.0, max(0.0, state.value + delta))
            config = self._configs[kind]
            unmet = None if config.target_low <= value <= config.target_high else state.unmet_since or event.at
            result[kind] = replace(
                state,
                value=value,
                unmet_since=unmet,
                evidence=(state.evidence + (event.id,))[-32:],
            )
        return result

    def urgencies(
        self,
        states: Mapping[DriveKind, DriveState],
        at: datetime,
        context: Mapping[DriveKind, float] | None = None,
    ) -> dict[DriveKind, float]:
        multipliers = context or {}
        output: dict[DriveKind, float] = {}
        for kind, state in states.items():
            config = self._configs[kind]
            if state.value < config.target_low:
                distance = config.target_low - state.value
            elif state.value > config.target_high:
                distance = state.value - config.target_high
            else:
                distance = 0.0
            duration_hours = 0.0
            if state.unmet_since is not None:
                duration_hours = max(0.0, (at - state.unmet_since).total_seconds() / 3600)
            raw = (
                config.base_weight
                * distance**config.sensitivity
                * (1.0 + config.duration_rate_per_hour * duration_hours)
                * multipliers.get(kind, 1.0)
            )
            output[kind] = min(config.urgency_cap, max(0.0, raw))
        return output
