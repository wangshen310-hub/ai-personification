"""Slow, deterministic relationship state derived from trusted events."""

from dataclasses import dataclass, replace

from companion_kernel.events import KernelEvent
from companion_kernel.types import EventKind


@dataclass(frozen=True, slots=True)
class RelationshipState:
    familiarity: float
    trust: float
    reciprocity: float
    boundary_clarity: float

    @classmethod
    def initial(cls) -> "RelationshipState":
        return cls(familiarity=0.05, trust=0.50, reciprocity=0.50, boundary_clarity=0.50)

    def to_dict(self) -> dict[str, float]:
        return {
            "familiarity": self.familiarity,
            "trust": self.trust,
            "reciprocity": self.reciprocity,
            "boundary_clarity": self.boundary_clarity,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "RelationshipState":
        state = cls(
            familiarity=float(raw["familiarity"]),
            trust=float(raw["trust"]),
            reciprocity=float(raw["reciprocity"]),
            boundary_clarity=float(raw["boundary_clarity"]),
        )
        if any(not 0.0 <= value <= 1.0 for value in state.to_dict().values()):
            raise ValueError("relationship values must be inside [0, 1]")
        return state


_DELTAS: dict[EventKind, tuple[float, float, float, float]] = {
    EventKind.USER_MESSAGE: (0.012, 0.004, 0.015, 0.0),
    EventKind.ASSISTANT_MESSAGE_SENT: (0.006, 0.0, 0.004, 0.0),
    EventKind.PROACTIVE_SENT: (0.004, 0.0, -0.006, 0.0),
    EventKind.BOUNDARY_RESPECTED: (0.0, 0.025, 0.0, 0.040),
    EventKind.USER_PAUSE: (0.0, 0.0, 0.0, 0.020),
    EventKind.CONTRADICTION: (0.0, -0.035, -0.010, -0.015),
}


def evolve_relationship(state: RelationshipState, event: KernelEvent) -> RelationshipState:
    delta = _DELTAS.get(event.kind)
    if delta is None:
        return state
    current = (
        state.familiarity,
        state.trust,
        state.reciprocity,
        state.boundary_clarity,
    )
    updated = tuple(min(1.0, max(0.0, value + change)) for value, change in zip(current, delta))
    return replace(
        state,
        familiarity=updated[0],
        trust=updated[1],
        reciprocity=updated[2],
        boundary_clarity=updated[3],
    )
