from datetime import UTC, datetime

from companion_kernel.audit import AuditedCandidate, AuditEntry, JsonlAuditLog
from companion_kernel.types import ActionKind, DriveKind


def test_audit_log_round_trip(tmp_path) -> None:
    candidate = AuditedCandidate(
        id="send",
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 0.8),),
        relationship_health=0.6,
        value_alignment=0.8,
        intrusion_cost=0.2,
        risk=0.0,
        repetition=0.1,
        safety_findings=(),
    )
    entry = AuditEntry(
        event_id="decision-1",
        state_version=3,
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        urgencies=((DriveKind.CONNECTION, 0.9),),
        candidates=(candidate,),
        selected_candidate_id="send",
        evaluations=(("send", True, 1.2, ()),),
    )
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    log.append(entry)
    assert log.read_all() == (entry,)
