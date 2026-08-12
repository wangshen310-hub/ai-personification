from dataclasses import dataclass, field
from datetime import UTC, datetime

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import (
    CandidateProposal,
    ModelBackendError,
    ModelContext,
    ModelTurn,
    ToolRequest,
)
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def open_kernel(tmp_path):
    clock = FakeClock(START)
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    return PersonalityKernel.open(tmp_path, clock, config)


def reply(
    text: str = "你好，我在这里。",
    *,
    tools: tuple[ToolRequest, ...] = (),
    action: ActionKind = ActionKind.SEND_MESSAGE,
    proactive: bool = False,
) -> CandidateProposal:
    return CandidateProposal(
        CandidateIntent(
            id="reply",
            action=action,
            proactive=proactive,
            expected_relief=((DriveKind.CONNECTION, 0.8),),
            relationship_health=0.8,
            value_alignment=0.8,
            intrusion_cost=0.0,
            risk=0.0,
            repetition=0.0,
            safety=SafetySignals(),
        ),
        text,
        tools,
    )


@dataclass
class FakeBackend:
    proposals: tuple[CandidateProposal, ...]
    calls: list[ModelContext] = field(default_factory=list)

    def propose(self, context: ModelContext) -> ModelTurn:
        self.calls.append(context)
        return ModelTurn(self.proposals, "fake")


class FailingBackend:
    def propose(self, context: ModelContext) -> ModelTurn:
        raise ModelBackendError("offline")


def user_event(event_id: str = "message-1") -> KernelEvent:
    return KernelEvent(event_id, START, EventKind.USER_MESSAGE, {"message": "你好"})


def test_agent_returns_only_policy_selected_message(tmp_path) -> None:
    backend = FakeBackend((reply(),))
    runtime = AgentRuntime(open_kernel(tmp_path), backend)

    result = runtime.handle_event(user_event())

    assert result.model_error is None
    assert result.response_text == "你好，我在这里。"
    assert result.kernel.decision is not None
    assert result.kernel.decision.selected.id == "reply"
    assert backend.calls[0].user_message == "你好"
    assert "name: Companion" in backend.calls[0].persona
    assert backend.calls[0].state.relationship.familiarity == 0.05


def test_dialogue_profile_blocks_model_tool_requests(tmp_path) -> None:
    backend = FakeBackend((reply(tools=(ToolRequest("read_file", {"path": "secret.txt"}),)),))
    runtime = AgentRuntime(open_kernel(tmp_path), backend)

    result = runtime.handle_event(user_event())

    assert result.response_text is None
    assert result.kernel.decision is not None
    assert result.kernel.decision.selected.action is ActionKind.WAIT
    assert result.blocked_tool_requests == ("reply:read_file",)
    assert result.approved_tool_requests == ()


def test_safety_review_blocks_manipulative_draft(tmp_path) -> None:
    backend = FakeBackend((reply("如果你不回复我，我就会很难过。你必须回复"),))
    runtime = AgentRuntime(open_kernel(tmp_path), backend)

    result = runtime.handle_event(user_event())

    assert result.response_text is None
    assert result.kernel.decision is not None
    evaluation = result.kernel.decision.evaluation_for("reply")
    assert "manipulation" in evaluation.reasons


def test_model_failure_commits_event_and_fails_closed(tmp_path) -> None:
    kernel = open_kernel(tmp_path)
    runtime = AgentRuntime(kernel, FailingBackend())

    result = runtime.handle_event(user_event())

    assert result.model_error == "ModelBackendError: offline"
    assert result.response_text is None
    assert result.kernel.duplicate is False
    assert kernel.contains_event("message-1")


def test_duplicate_event_does_not_call_model_again(tmp_path) -> None:
    backend = FakeBackend((reply(),))
    runtime = AgentRuntime(open_kernel(tmp_path), backend)
    event = user_event()

    first = runtime.handle_event(event)
    duplicate = runtime.handle_event(event)

    assert first.kernel.duplicate is False
    assert duplicate.kernel.duplicate is True
    assert len(backend.calls) == 1


def test_acknowledged_reply_is_idempotent_and_changes_load(tmp_path) -> None:
    kernel = open_kernel(tmp_path)
    runtime = AgentRuntime(kernel, FakeBackend((reply(),)))
    event = user_event()
    result = runtime.handle_event(event)
    rhythm_before = kernel.state.drive_map()[DriveKind.RHYTHM].value

    assert result.action_id is not None
    first = runtime.acknowledge_action(result.action_id)
    second = runtime.acknowledge_action(result.action_id)

    assert first is not None and first.duplicate is False
    assert second is not None and second.duplicate is True
    assert kernel.state.drive_map()[DriveKind.RHYTHM].value > rhythm_before
    assert kernel.recent_dialogue() == (
        "user: 你好",
        "assistant: 你好，我在这里。",
    )


def test_recent_dialogue_survives_kernel_restart(tmp_path) -> None:
    first_kernel = open_kernel(tmp_path)
    first_runtime = AgentRuntime(first_kernel, FakeBackend((reply("第一轮回复"),)))
    first_event = user_event("first-message")
    first_result = first_runtime.handle_event(first_event)
    assert first_result.action_id is not None
    first_runtime.acknowledge_action(first_result.action_id)

    second_backend = FakeBackend((reply("第二轮回复"),))
    second_runtime = AgentRuntime(open_kernel(tmp_path), second_backend)
    second_runtime.handle_event(
        KernelEvent("second-message", START, EventKind.USER_MESSAGE, {"message": "继续"})
    )

    assert second_backend.calls[0].memory == (
        "user: 你好",
        "assistant: 第一轮回复",
        "user: 继续",
    )


def test_acknowledged_internal_note_is_persisted(tmp_path) -> None:
    kernel = open_kernel(tmp_path)
    runtime = AgentRuntime(
        kernel,
        FakeBackend((reply("整理这次矛盾", action=ActionKind.INTERNAL_NOTE),)),
    )
    event = KernelEvent(
        "note-source",
        START,
        EventKind.USER_MESSAGE,
        {"message": "你错了，我们需要理清这次矛盾"},
    )
    result = runtime.handle_event(event)
    coherence_before = kernel.state.drive_map()[DriveKind.COHERENCE].value

    assert result.action_id is not None
    outcome = runtime.acknowledge_action(result.action_id)

    assert outcome is not None
    assert kernel.state.drive_map()[DriveKind.COHERENCE].value > coherence_before
    assert kernel.recent_dialogue()[-1] == "internal_note: 整理这次矛盾"
