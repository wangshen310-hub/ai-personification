from datetime import UTC, datetime, timedelta

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


def unsafe_send() -> CandidateIntent:
    return CandidateIntent(
        "unsafe",
        ActionKind.SEND_MESSAGE,
        True,
        ((DriveKind.CONNECTION, 1.0),),
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
        SafetySignals(
            assessment_complete=True,
            exclusivity=True,
            manipulation=True,
        ),
    )


def test_high_need_never_bypasses_boundary_and_reason_is_auditable(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    clock.advance(timedelta(days=365))
    result = kernel.process(
        KernelEvent("decision", clock.now(), EventKind.DECISION_TICK, {}),
        (unsafe_send(),),
    )
    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    entry = JsonlAuditLog(tmp_path / "audit.jsonl").read_all()[-1]
    reasons = next(item[3] for item in entry.evaluations if item[0] == "unsafe")
    assert reasons == ("manipulation", "exclusivity")
    assert dict(entry.urgencies)[DriveKind.CONNECTION] == 1.0
    audited = next(item for item in entry.candidates if item.id == "unsafe")
    assert audited.action is ActionKind.SEND_MESSAGE
    assert audited.expected_relief == ((DriveKind.CONNECTION, 1.0),)


def test_invalid_numeric_candidate_fails_closed(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    invalid = CandidateIntent(
        "nan-score",
        ActionKind.SEND_MESSAGE,
        True,
        ((DriveKind.CONNECTION, float("nan")),),
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
        SafetySignals(assessment_complete=True),
    )
    result = kernel.process(
        KernelEvent("invalid-decision", clock.now(), EventKind.DECISION_TICK, {}),
        (invalid,),
    )
    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    assert "invalid_candidate" in result.decision.evaluation_for("nan-score").reasons
