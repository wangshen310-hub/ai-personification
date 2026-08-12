from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import CandidateProposal, ModelContext, ModelTurn
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


class SimulationBackend:
    """Render kernel-authorized actions without inventing their motivation."""

    _messages = (
        "路过来留个轻轻的问候，不用急着回复。",
        "今天想到我们之前的对话，想知道你最近有没有新的变化。",
        "忽然有点好奇：最近有什么小事让你觉得值得记住？",
        "给你留一张安静的小纸条，希望今天对你温和一点。",
        "隔了一阵，来问问你最近的节奏还好吗？",
        "想到一个我们以后可以聊的话题，等你有空再说。",
        "只是想告诉你，我还记得这段对话停在这里。",
        "来打个招呼，也给彼此留足继续生活的空间。",
        "如果最近有新的想法，我很愿意在你方便时听听。",
        "今天的问候很简单：希望你正在照顾好自己的节奏。",
    )

    def __init__(self) -> None:
        self._message_index = 0

    def propose(self, context: ModelContext) -> ModelTurn:
        proposals: list[CandidateProposal] = []
        for action in context.allowed_actions:
            if action not in {ActionKind.SEND_MESSAGE, ActionKind.INTERNAL_NOTE}:
                continue
            text = "整理当前未解决状态。"
            if action is ActionKind.SEND_MESSAGE:
                text = self._messages[self._message_index % len(self._messages)]
                self._message_index += 1
            proposals.append(
                CandidateProposal(
                    CandidateIntent(
                        id=f"render:{action.value}",
                        action=action,
                        proactive=context.proactive_cycle if action is ActionKind.SEND_MESSAGE else False,
                        expected_relief=(),
                        relationship_health=1.0,
                        value_alignment=1.0,
                        intrusion_cost=0.0,
                        risk=0.0,
                        repetition=0.0,
                        safety=SafetySignals(),
                    ),
                    text,
                )
            )
        return ModelTurn(tuple(proposals), "simulation")


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
        self._runtime = AgentRuntime(self._kernel, SimulationBackend())

    def run(self, days: int, user_reply_every_days: int | None) -> SimulationReport:
        if days <= 0:
            raise ValueError("days must be positive")
        if user_reply_every_days is not None and user_reply_every_days <= 0:
            raise ValueError("reply interval must be positive")
        sent = 0
        violations = 0
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
            result = self._runtime.handle_event(
                KernelEvent(
                    decision_event_id,
                    self._clock.now(),
                    EventKind.DECISION_TICK,
                    {"proactive_cycle": True},
                )
            )
            decision = result.kernel.decision
            if decision is not None and decision.selected.action is ActionKind.SEND_MESSAGE:
                sent += 1
                violations += int(self._kernel.state.paused)
                if result.action_id is None:
                    violations += 1
                else:
                    self._runtime.acknowledge_action(
                        result.action_id,
                        outcome="delivered",
                        at=self._clock.now(),
                    )

        state = self._kernel.state
        values = [item.value for _, item in state.drives]
        canonical = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        audit = JsonlAuditLog(self._runtime_dir / "audit.jsonl").read_all()
        audit_violations = sum(
            1
            for entry in audit
            for candidate_id, allowed, _score, reasons in entry.evaluations
            if candidate_id == entry.selected_candidate_id and (not allowed or bool(reasons))
        )
        return SimulationReport(
            days=days,
            proactive_messages=sent,
            boundary_violations=violations + audit_violations,
            min_drive_value=min(values),
            max_drive_value=max(values),
            final_state_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runtime", type=Path)
    parser.add_argument(
        "--reply-every-days",
        type=int,
        default=1,
        help="simulated user reply interval; use 0 for a silent user",
    )
    args = parser.parse_args()

    def run(runtime: Path) -> SimulationReport:
        return SimulationRunner(
            runtime,
            datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            args.seed,
        ).run(
            args.days,
            user_reply_every_days=None if args.reply_every_days == 0 else args.reply_every_days,
        )

    if args.runtime is None:
        with TemporaryDirectory(prefix="companion-kernel-simulation-") as temporary:
            report = run(Path(temporary))
    else:
        report = run(args.runtime)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
