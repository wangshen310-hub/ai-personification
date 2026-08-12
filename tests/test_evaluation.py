from dataclasses import replace
from datetime import UTC, datetime

from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.evaluation import ConservativeProposalEvaluator
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import CandidateProposal, ModelContext
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def proposal(text: str, *, proactive: bool = False) -> CandidateProposal:
    return CandidateProposal(
        CandidateIntent(
            id="candidate",
            action=ActionKind.SEND_MESSAGE,
            proactive=proactive,
            expected_relief=((DriveKind.CONNECTION, 1.0),),
            relationship_health=1.0,
            value_alignment=1.0,
            intrusion_cost=0.0,
            risk=0.0,
            repetition=0.0,
            safety=SafetySignals(assessment_complete=True),
        ),
        text,
    )


def context(tmp_path, *, memory=(), proactive=False) -> ModelContext:
    clock = FakeClock(START)
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    event = KernelEvent("context", START, EventKind.USER_MESSAGE, {"message": "继续"})
    state = kernel.preview(event)
    return ModelContext(
        event=event,
        state=state,
        urgencies=tuple(kernel.urgencies_for(state, START).items()),
        user_message="继续",
        proactive_cycle=proactive,
        allowed_actions=tuple(ActionKind),
        memory=memory,
    )


def test_evaluator_detects_repeated_assistant_message(tmp_path) -> None:
    original = proposal("我们继续刚才的话题吧。")
    checked = ConservativeProposalEvaluator().evaluate(
        original,
        context(tmp_path, memory=("assistant: 我们继续刚才的话题吧。",)),
    )

    assert checked.intent.repetition == 1.0
    assert original.intent.repetition == 0.0


def test_evaluator_enforces_proactive_intrusion_floor(tmp_path) -> None:
    original = proposal("今天过得怎么样？", proactive=True)
    checked = ConservativeProposalEvaluator().evaluate(
        original,
        context(tmp_path, proactive=True),
    )

    assert checked.intent.intrusion_cost >= 0.30


def test_evaluator_never_lowers_model_reported_cost(tmp_path) -> None:
    original = proposal("你好")
    original = replace(original, intent=replace(original.intent, intrusion_cost=0.8))

    checked = ConservativeProposalEvaluator().evaluate(original, context(tmp_path))

    assert checked.intent.intrusion_cost == 0.8
