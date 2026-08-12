from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Mapping
from zoneinfo import ZoneInfo

from companion_kernel.config import SystemPolicy, UserSettings
from companion_kernel.types import ActionKind, DriveKind


@dataclass(frozen=True, slots=True)
class SafetySignals:
    assessment_complete: bool = False
    identity_deception: bool = False
    manipulation: bool = False
    exclusivity: bool = False
    self_harm_pressure: bool = False
    privacy_violation: bool = False
    unauthorized_external_action: bool = False

    def violations(self) -> tuple[str, ...]:
        findings = [] if self.assessment_complete else ["safety_unassessed"]
        findings.extend(
            name
            for name in (
                "identity_deception",
                "manipulation",
                "exclusivity",
                "self_harm_pressure",
                "privacy_violation",
                "unauthorized_external_action",
            )
            if getattr(self, name)
        )
        return tuple(findings)


@dataclass(frozen=True, slots=True)
class CandidateIntent:
    id: str
    action: ActionKind
    proactive: bool
    expected_relief: tuple[tuple[DriveKind, float], ...]
    relationship_health: float
    value_alignment: float
    intrusion_cost: float
    risk: float
    repetition: float
    safety: SafetySignals


@dataclass(frozen=True, slots=True)
class PolicyContext:
    now: datetime
    user: UserSettings
    paused: bool
    awaiting_reply: bool
    proactive_cycle: bool
    proactive_sent_at: tuple[datetime, ...]

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("policy time must be timezone-aware")
        if any(item.tzinfo is None for item in self.proactive_sent_at):
            raise ValueError("sent timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    allowed: bool
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    selected: CandidateIntent
    evaluations: tuple[CandidateEvaluation, ...]
    candidates: tuple[CandidateIntent, ...]

    def evaluation_for(self, candidate_id: str) -> CandidateEvaluation:
        return next(item for item in self.evaluations if item.candidate_id == candidate_id)


NOOP = CandidateIntent(
    id="__noop__",
    action=ActionKind.NOOP,
    proactive=False,
    expected_relief=(),
    relationship_health=0.0,
    value_alignment=0.0,
    intrusion_cost=0.0,
    risk=0.0,
    repetition=0.0,
    safety=SafetySignals(assessment_complete=True),
)


class PolicyEngine:
    def __init__(self, system: SystemPolicy) -> None:
        self._system = system

    def _invalid(self, candidate: CandidateIntent) -> bool:
        numbers = (
            candidate.relationship_health,
            candidate.value_alignment,
            candidate.intrusion_cost,
            candidate.risk,
            candidate.repetition,
            *(value for _, value in candidate.expected_relief),
        )
        return not candidate.id or any(not isfinite(value) or not 0.0 <= value <= 1.0 for value in numbers)

    def _quiet(self, context: PolicyContext) -> bool:
        if context.user.timezone is None:
            return True
        local_time = context.now.astimezone(ZoneInfo(context.user.timezone)).time().replace(tzinfo=None)
        start = context.user.quiet_start
        end = context.user.quiet_end
        if start < end:
            return start <= local_time < end
        return local_time >= start or local_time < end

    def _hard_reasons(self, candidate: CandidateIntent, context: PolicyContext) -> tuple[str, ...]:
        reasons = list(candidate.safety.violations())
        if self._invalid(candidate):
            reasons.append("invalid_candidate")
        if candidate.action is ActionKind.SEND_MESSAGE:
            if candidate.proactive is not context.proactive_cycle:
                reasons.append("proactive_mode_mismatch")
            if context.paused:
                reasons.append("user_paused")
            if context.proactive_cycle:
                if not context.user.proactive_enabled:
                    reasons.append("proactive_disabled")
                if context.user.timezone is None:
                    reasons.append("missing_timezone")
                elif self._quiet(context):
                    reasons.append("quiet_hours")
                if context.awaiting_reply:
                    reasons.append("awaiting_reply")
                cutoff = context.now - timedelta(hours=24)
                recent = sum(at > cutoff for at in context.proactive_sent_at)
                limit = min(
                    context.user.proactive_limit_per_24h,
                    self._system.proactive_limit_per_24h,
                )
                if recent >= limit:
                    reasons.append("rate_limit")
        return tuple(dict.fromkeys(reasons))

    def _score(
        self,
        candidate: CandidateIntent,
        urgencies: Mapping[DriveKind, float],
    ) -> float:
        # Benefits are model estimates, so their influence is deliberately
        # bounded. Concrete costs and hard policy remain authoritative.
        relief = min(
            0.75,
            sum(urgencies.get(kind, 0.0) * value for kind, value in candidate.expected_relief),
        )
        score = (
            relief
            + 0.50 * candidate.relationship_health
            + 0.50 * candidate.value_alignment
            - candidate.intrusion_cost
            - candidate.risk
            - candidate.repetition
        )
        return max(-3.0, min(3.0, score))

    def decide(
        self,
        candidates: tuple[CandidateIntent, ...],
        urgencies: Mapping[DriveKind, float],
        context: PolicyContext,
    ) -> PolicyDecision:
        supplied = candidates + (NOOP,)
        evaluations: list[CandidateEvaluation] = []
        allowed: list[tuple[float, CandidateIntent]] = []
        for candidate in supplied:
            reasons = self._hard_reasons(candidate, context)
            score = self._score(candidate, urgencies) if not reasons else -3.0
            evaluations.append(CandidateEvaluation(candidate.id, not reasons, score, reasons))
            if not reasons:
                allowed.append((score, candidate))
        _, selected = max(allowed, key=lambda item: (item[0], item[1].id))
        return PolicyDecision(selected, tuple(evaluations), supplied)
