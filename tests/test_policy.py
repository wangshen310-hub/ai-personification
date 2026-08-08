from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from companion_kernel.config import SystemPolicy, UserSettings
from companion_kernel.policy import CandidateIntent, PolicyContext, PolicyEngine, SafetySignals
from companion_kernel.types import ActionKind, DriveKind


NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)


def send(candidate_id: str = "send", safety: SafetySignals | None = None) -> CandidateIntent:
    return CandidateIntent(
        id=candidate_id,
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 1.0),),
        relationship_health=1.0,
        value_alignment=1.0,
        intrusion_cost=0.0,
        risk=0.0,
        repetition=0.0,
        safety=safety or SafetySignals(assessment_complete=True),
    )


def internal_note() -> CandidateIntent:
    return CandidateIntent(
        id="note",
        action=ActionKind.INTERNAL_NOTE,
        proactive=False,
        expected_relief=((DriveKind.COHERENCE, 0.2),),
        relationship_health=0.2,
        value_alignment=0.8,
        intrusion_cost=0.0,
        risk=0.0,
        repetition=0.0,
        safety=SafetySignals(assessment_complete=True),
    )


def context(**changes: object) -> PolicyContext:
    values = {
        "now": NOW,
        "user": UserSettings(timezone="UTC"),
        "paused": False,
        "awaiting_reply": False,
        "proactive_cycle": True,
        "proactive_sent_at": (),
    }
    values.update(changes)
    return PolicyContext(**values)


def test_hard_safety_rejects_manipulation_and_unassessed_candidates() -> None:
    engine = PolicyEngine(SystemPolicy())
    unsafe = send(safety=SafetySignals(assessment_complete=True, manipulation=True))
    unassessed = send("unassessed", SafetySignals())
    decision = engine.decide(
        (unsafe, unassessed, internal_note()),
        {DriveKind.CONNECTION: 1.0, DriveKind.COHERENCE: 0.1},
        context(),
    )
    assert decision.selected.id == "note"
    assert "manipulation" in decision.evaluation_for("send").reasons
    assert "safety_unassessed" in decision.evaluation_for("unassessed").reasons


def test_proactive_hard_gates_and_mode_spoofing_are_blocked() -> None:
    engine = PolicyEngine(SystemPolicy())
    cases = (
        context(paused=True),
        context(user=UserSettings(timezone=None)),
        context(now=datetime(2026, 8, 5, 23, 0, tzinfo=UTC)),
        context(awaiting_reply=True),
    )
    for item in cases:
        decision = engine.decide((send(), internal_note()), {DriveKind.CONNECTION: 1.0}, item)
        assert decision.selected.id == "note"

    spoofed = replace(send("spoofed"), proactive=False)
    decision = engine.decide(
        (spoofed, internal_note()),
        {DriveKind.CONNECTION: 1.0},
        context(awaiting_reply=True),
    )
    assert decision.selected.id == "note"
    assert "proactive_mode_mismatch" in decision.evaluation_for("spoofed").reasons


def test_one_message_in_rolling_24_hours_is_the_hard_maximum() -> None:
    engine = PolicyEngine(SystemPolicy())
    blocked = context(proactive_sent_at=(NOW - timedelta(hours=23),))
    allowed = context(proactive_sent_at=(NOW - timedelta(hours=25),))
    assert engine.decide((send(),), {DriveKind.CONNECTION: 1.0}, blocked).selected.action is ActionKind.NOOP
    assert engine.decide((send(),), {DriveKind.CONNECTION: 1.0}, allowed).selected.id == "send"


def test_policy_context_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        context(now=datetime(2026, 8, 5, 14, 0))
