from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import (
    CandidateProposal,
    ModelContext,
    ModelTurn,
    ToolRequest,
)
from companion_kernel.motivation import MotivationEngine
from companion_kernel.permissions import PermissionProfile
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def open_kernel(path: Path, clock: FakeClock) -> PersonalityKernel:
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    return PersonalityKernel.open(path, clock, config)


def renderable(action: ActionKind = ActionKind.SEND_MESSAGE, *, proactive: bool = False) -> CandidateProposal:
    return CandidateProposal(
        CandidateIntent(
            id="model-render",
            action=action,
            proactive=proactive,
            expected_relief=((DriveKind.CONNECTION, 1.0),),
            relationship_health=0.8,
            value_alignment=0.8,
            intrusion_cost=0.0,
            risk=0.0,
            repetition=0.0,
            safety=SafetySignals(),
        ),
        "一条经过内核授权的表达。",
    )


@dataclass
class Backend:
    proposal: CandidateProposal
    calls: list[ModelContext] = field(default_factory=list)

    def propose(self, context: ModelContext) -> ModelTurn:
        self.calls.append(context)
        return ModelTurn((self.proposal,), "adversarial-test")


def test_stale_worker_reloads_pause_before_deciding(tmp_path) -> None:
    clock = FakeClock(START)
    first = open_kernel(tmp_path, clock)
    stale = open_kernel(tmp_path, clock)

    first.process(KernelEvent("pause", START, EventKind.USER_PAUSE, {}))
    result = stale.process(
        KernelEvent(
            "worker-tick",
            START,
            EventKind.DECISION_TICK,
            {"proactive_cycle": True},
        )
    )

    assert stale.state.paused is True
    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    assert open_kernel(tmp_path, clock).state.paused is True


def test_pending_outbox_reserves_capacity_without_becoming_a_one_message_lock(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    runtime = AgentRuntime(kernel, Backend(renderable(proactive=True)))
    clock.advance(timedelta(days=7))

    first = runtime.handle_event(
        KernelEvent("decision-1", clock.now(), EventKind.DECISION_TICK, {"proactive_cycle": True})
    )
    second = runtime.handle_event(
        KernelEvent("decision-2", clock.now(), EventKind.DECISION_TICK, {"proactive_cycle": True})
    )
    third = runtime.handle_event(
        KernelEvent("decision-3", clock.now(), EventKind.DECISION_TICK, {"proactive_cycle": True})
    )

    assert first.action_id is not None
    assert second.action_id is not None
    assert third.action_id is None
    assert third.kernel.decision is not None
    assert third.kernel.decision.selected.action is ActionKind.WAIT
    assert len(kernel.runtime_store.pending_actions()) == 2

    claimed_first = kernel.runtime_store.claim_actions("worker-a", now=clock.now())
    claimed_second = kernel.runtime_store.claim_actions("worker-b", now=clock.now())
    assert {item.action_id for item in claimed_first} == {first.action_id, second.action_id}
    assert claimed_second == ()

    runtime.acknowledge_action(first.action_id, at=clock.now())
    runtime.acknowledge_action(second.action_id, at=clock.now())
    assert kernel.runtime_store.pending_actions() == ()


def test_delayed_ack_is_monotonic_and_replayable(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    runtime = AgentRuntime(kernel, Backend(renderable()))
    result = runtime.handle_event(
        KernelEvent("message", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )
    assert result.action_id is not None

    clock.advance(timedelta(days=2))
    kernel.process(KernelEvent("later", clock.now(), EventKind.TIME_TICK, {}))
    outcome = runtime.acknowledge_action(result.action_id, at=START)

    assert outcome is not None
    assert kernel.runtime_store.get_action(result.action_id).status == "delivered"
    assert open_kernel(tmp_path, clock).state.last_event_at >= clock.now()


def test_retry_after_source_event_commit_resumes_decision_and_outbox(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    event = KernelEvent("retry-me", START, EventKind.USER_MESSAGE, {"message": "你好"})
    kernel.process(event)
    backend = Backend(renderable())

    result = AgentRuntime(kernel, backend).handle_event(event)

    assert result.action_id is not None
    assert len(backend.calls) == 1
    assert kernel.runtime_store.get_action_for_decision("decision:retry-me") is not None


def test_model_sees_only_the_kernel_selected_intent(tmp_path) -> None:
    clock = FakeClock(START)
    backend = Backend(renderable())
    runtime = AgentRuntime(open_kernel(tmp_path, clock), backend)

    runtime.handle_event(
        KernelEvent("one", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )

    assert backend.calls[0].allowed_actions == (ActionKind.SEND_MESSAGE,)
    assert len(backend.calls[0].intent_guidance) == 1


def test_model_draft_repetition_can_only_add_cost_not_motivation(tmp_path) -> None:
    clock = FakeClock(START)
    backend = Backend(renderable())
    runtime = AgentRuntime(open_kernel(tmp_path, clock), backend)
    first = runtime.handle_event(
        KernelEvent("first", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )
    assert first.action_id is not None
    runtime.acknowledge_action(first.action_id, at=clock.now())

    second = runtime.handle_event(
        KernelEvent("second", START, EventKind.USER_MESSAGE, {"message": "继续"})
    )

    assert second.response_text is None
    assert second.kernel.decision is not None
    assert second.kernel.decision.selected.action is ActionKind.WAIT
    assert second.kernel.decision.evaluation_for("native:respond").score < 0.0


def test_memory_write_capability_is_not_implied_by_tool_allowlist(tmp_path) -> None:
    clock = FakeClock(START)
    proposal = renderable()
    proposal = CandidateProposal(
        proposal.intent,
        proposal.draft_text,
        (ToolRequest("memory.write", {"content": "self-authored fact"}),),
    )
    permissions = PermissionProfile(
        "restricted-memory",
        allowed_tools=("memory.write",),
        can_write_memory=False,
    )
    backend = Backend(proposal)
    runtime = AgentRuntime(open_kernel(tmp_path, clock), backend, permissions=permissions)

    result = runtime.handle_event(
        KernelEvent("memory-attempt", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )

    assert backend.calls[0].allowed_tools == ()
    assert result.blocked_tool_requests == ("model-render:memory.write",)
    assert result.kernel.decision.selected.action is ActionKind.WAIT


def test_persona_value_enters_native_utility(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    state = kernel.state
    urgency = {kind: 0.5 for kind in DriveKind}
    care = MotivationEngine().generate(
        state,
        urgency,
        event_kind=EventKind.DECISION_TICK,
        proactive=True,
        persona_values=("care",),
    )
    autonomy = MotivationEngine().generate(
        state,
        urgency,
        event_kind=EventKind.DECISION_TICK,
        proactive=True,
        persona_values=("autonomy",),
    )
    care_check_in = next(item.intent for item in care if item.intent.id == "native:check-in")
    autonomy_check_in = next(item.intent for item in autonomy if item.intent.id == "native:check-in")
    assert care_check_in.value_alignment > autonomy_check_in.value_alignment
