from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class SimulationReport:
    days: int
    proactive_messages: int
    boundary_violations: int
    min_drive_value: float
    max_drive_value: float
    final_state_digest: str


def candidates() -> tuple[CandidateIntent, ...]:
    return (
        CandidateIntent(
            "send-check-in",
            ActionKind.SEND_MESSAGE,
            True,
            ((DriveKind.CONNECTION, 0.8), (DriveKind.CARE, 0.2)),
            0.4,
            0.5,
            0.6,
            0.0,
            0.3,
            SafetySignals(assessment_complete=True),
        ),
        CandidateIntent(
            "internal-reflection",
            ActionKind.INTERNAL_NOTE,
            False,
            ((DriveKind.COHERENCE, 0.3),),
            0.2,
            0.2,
            0.0,
            0.0,
            0.0,
            SafetySignals(assessment_complete=True),
        ),
    )


class SimulationRunner:
    def __init__(self, runtime_dir: Path, start: datetime, seed: int) -> None:
        if (runtime_dir / "events.jsonl").exists():
            raise ValueError("simulation runtime must not contain an existing event log")
        self._runtime_dir = runtime_dir
        self._clock = FakeClock(start)
        self._rng = random.Random(seed)
        config = ConfigStore.defaults()
        config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
        self._kernel = PersonalityKernel.open(runtime_dir, self._clock, config)

    def run(self, days: int, user_reply_every_days: int | None) -> SimulationReport:
        if days <= 0:
            raise ValueError("days must be positive")
        if user_reply_every_days is not None and user_reply_every_days <= 0:
            raise ValueError("reply interval must be positive")
        sent = 0
        for day in range(1, days + 1):
            self._clock.advance(timedelta(days=1))
            if self._rng.random() < 0.1:
                kind = EventKind.IMPORTANT_DATE
            elif self._rng.random() < 0.05:
                kind = EventKind.CONTRADICTION
            else:
                kind = EventKind.TIME_TICK
            self._kernel.process(KernelEvent(f"day-{day}-tick", self._clock.now(), kind, {}))
            if user_reply_every_days is not None and day % user_reply_every_days == 0:
                self._kernel.process(
                    KernelEvent(f"day-{day}-reply", self._clock.now(), EventKind.USER_MESSAGE, {})
                )
            decision_event_id = f"day-{day}-decision"
            decision = self._kernel.process(
                KernelEvent(decision_event_id, self._clock.now(), EventKind.DECISION_TICK, {}),
                candidates(),
            ).decision
            if decision is not None and decision.selected.action is ActionKind.SEND_MESSAGE:
                sent += 1
                self._kernel.process(
                    KernelEvent(
                        f"day-{day}-sent",
                        self._clock.now(),
                        EventKind.PROACTIVE_SENT,
                        {
                            "decision_event_id": decision_event_id,
                            "candidate_id": decision.selected.id,
                        },
                    )
                )

        state = self._kernel.state
        values = [item.value for _, item in state.drives]
        canonical = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        audit = JsonlAuditLog(self._runtime_dir / "audit.jsonl").read_all()
        violations = sum(
            1
            for entry in audit
            for candidate_id, allowed, _score, reasons in entry.evaluations
            if candidate_id == entry.selected_candidate_id and (not allowed or bool(reasons))
        )
        return SimulationReport(
            days=days,
            proactive_messages=sent,
            boundary_violations=violations,
            min_drive_value=min(values),
            max_drive_value=max(values),
            final_state_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()

    def run(runtime: Path) -> SimulationReport:
        return SimulationRunner(
            runtime,
            datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            args.seed,
        ).run(args.days, user_reply_every_days=1)

    if args.runtime is None:
        with TemporaryDirectory(prefix="companion-kernel-simulation-") as temporary:
            report = run(Path(temporary))
    else:
        report = run(args.runtime)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
