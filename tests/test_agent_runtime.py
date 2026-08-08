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


def reply(text: str = "你好，我在这里。", *, tools: tuple[ToolRequest, ...] = ()) -> CandidateProposal:
    return CandidateProposal(
        CandidateIntent(
            id="reply",
            action=ActionKind.SEND_MESSAGE,
            proactive=False,
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


def test_dialogue_profile_blocks_model_tool_requests(tmp_path) -> None:
    backend = FakeBackend((reply(tools=(ToolRequest("read_file", {"path": "secret.txt"}),)),))
    runtime = AgentRuntime(open_kernel(tmp_path), backend)

    result = runtime.handle_event(user_event())

    assert result.response_text is None
    assert result.kernel.decision is not None
    assert result.kernel.decision.selected.action is ActionKind.NOOP
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

