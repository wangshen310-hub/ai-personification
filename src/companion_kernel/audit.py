from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
import os
from pathlib import Path
from typing import Mapping

from companion_kernel.policy import CandidateIntent, PolicyDecision
from companion_kernel.types import ActionKind, DriveKind


def _finite_or_none(value: float) -> float | None:
    return float(value) if isfinite(value) else None


@dataclass(frozen=True, slots=True)
class AuditedCandidate:
    id: str
    action: ActionKind
    proactive: bool
    expected_relief: tuple[tuple[DriveKind, float | None], ...]
    relationship_health: float | None
    value_alignment: float | None
    intrusion_cost: float | None
    risk: float | None
    repetition: float | None
    safety_findings: tuple[str, ...]

    @classmethod
    def from_intent(cls, candidate: CandidateIntent) -> "AuditedCandidate":
        return cls(
            id=candidate.id,
            action=candidate.action,
            proactive=candidate.proactive,
            expected_relief=tuple(
                (kind, _finite_or_none(value))
                for kind, value in candidate.expected_relief
            ),
            relationship_health=_finite_or_none(candidate.relationship_health),
            value_alignment=_finite_or_none(candidate.value_alignment),
            intrusion_cost=_finite_or_none(candidate.intrusion_cost),
            risk=_finite_or_none(candidate.risk),
            repetition=_finite_or_none(candidate.repetition),
            safety_findings=candidate.safety.violations(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action.value,
            "proactive": self.proactive,
            "expected_relief": [
                {"drive": kind.value, "value": value}
                for kind, value in self.expected_relief
            ],
            "relationship_health": self.relationship_health,
            "value_alignment": self.value_alignment,
            "intrusion_cost": self.intrusion_cost,
            "risk": self.risk,
            "repetition": self.repetition,
            "safety_findings": list(self.safety_findings),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AuditedCandidate":
        def optional_number(value: object) -> float | None:
            return None if value is None else float(value)

        return cls(
            id=str(raw["id"]),
            action=ActionKind(str(raw["action"])),
            proactive=bool(raw["proactive"]),
            expected_relief=tuple(
                (
                    DriveKind(str(item["drive"])),
                    optional_number(item["value"]),
                )
                for item in raw["expected_relief"]
            ),
            relationship_health=optional_number(raw["relationship_health"]),
            value_alignment=optional_number(raw["value_alignment"]),
            intrusion_cost=optional_number(raw["intrusion_cost"]),
            risk=optional_number(raw["risk"]),
            repetition=optional_number(raw["repetition"]),
            safety_findings=tuple(str(item) for item in raw["safety_findings"]),
        )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    event_id: str
    state_version: int
    at: datetime
    urgencies: tuple[tuple[DriveKind, float], ...]
    candidates: tuple[AuditedCandidate, ...]
    selected_candidate_id: str
    evaluations: tuple[tuple[str, bool, float, tuple[str, ...]], ...]

    @classmethod
    def from_decision(
        cls,
        event_id: str,
        state_version: int,
        at: datetime,
        decision: PolicyDecision,
        urgencies: Mapping[DriveKind, float],
    ) -> "AuditEntry":
        return cls(
            event_id=event_id,
            state_version=state_version,
            at=at,
            urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
            candidates=tuple(
                AuditedCandidate.from_intent(candidate)
                for candidate in decision.candidates
            ),
            selected_candidate_id=decision.selected.id,
            evaluations=tuple(
                (item.candidate_id, item.allowed, item.score, item.reasons)
                for item in decision.evaluations
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "state_version": self.state_version,
            "at": self.at.isoformat(),
            "urgencies": [
                {"drive": kind.value, "value": value}
                for kind, value in self.urgencies
            ],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "evaluations": [list(item[:3]) + [list(item[3])] for item in self.evaluations],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AuditEntry":
        return cls(
            event_id=str(raw["event_id"]),
            state_version=int(raw["state_version"]),
            at=datetime.fromisoformat(str(raw["at"])),
            urgencies=tuple(
                (DriveKind(str(item["drive"])), float(item["value"]))
                for item in raw["urgencies"]
            ),
            candidates=tuple(
                AuditedCandidate.from_dict(item)
                for item in raw["candidates"]
            ),
            selected_candidate_id=str(raw["selected_candidate_id"]),
            evaluations=tuple(
                (str(item[0]), bool(item[1]), float(item[2]), tuple(item[3]))
                for item in raw["evaluations"]
            ),
        )


class JsonlAuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> None:
        encoded = json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> tuple[AuditEntry, ...]:
        if not self._path.exists():
            return ()
        return tuple(
            AuditEntry.from_dict(json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines()
        )
