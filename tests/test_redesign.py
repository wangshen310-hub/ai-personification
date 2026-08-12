from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Thread

import pytest

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, PersonaProfile, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import CandidateProposal, ModelContext, ModelTurn
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.storage import SQLiteRuntimeStore
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def proposal(*, proactive: bool, action: ActionKind = ActionKind.SEND_MESSAGE) -> CandidateProposal:
    return CandidateProposal(
        CandidateIntent(
            "model-high-score",
            action,
            proactive,
            ((DriveKind.CONNECTION, 1.0),),
            1.0,
            1.0,
            0.0,
            0.0,
            0.0,
            SafetySignals(),
        ),
        "模型自称这是满分候选",
    )


@dataclass
class Backend:
    value: CandidateProposal

    def propose(self, context: ModelContext) -> ModelTurn:
        return ModelTurn((self.value,), "adversarial")


def runtime_at(path: Path, clock: FakeClock, backend: Backend) -> tuple[PersonalityKernel, AgentRuntime]:
    config = ConfigStore.open(path)
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(path, clock, config)
    return kernel, AgentRuntime(kernel, backend)


def test_zero_need_model_self_score_cannot_create_proactive_motive(tmp_path) -> None:
    clock = FakeClock(START)
    _kernel, runtime = runtime_at(tmp_path, clock, Backend(proposal(proactive=True)))

    result = runtime.handle_event(
        KernelEvent("zero-pressure", START, EventKind.DECISION_TICK, {"proactive_cycle": True})
    )

    assert result.response_text is None
    assert result.action_id is None
    assert result.kernel.decision is not None
    assert result.kernel.decision.selected.action is ActionKind.WAIT


def test_high_connection_pressure_creates_native_proactive_opportunity(tmp_path) -> None:
    clock = FakeClock(START)
    _kernel, runtime = runtime_at(tmp_path, clock, Backend(proposal(proactive=True)))
    clock.advance(timedelta(days=7))

    result = runtime.handle_event(
        KernelEvent("high-pressure", clock.now(), EventKind.DECISION_TICK, {"proactive_cycle": True})
    )

    assert result.response_text == "模型自称这是满分候选"
    assert result.action_id is not None
    assert result.kernel.decision is not None
    evaluation = result.kernel.decision.evaluation_for("model-high-score")
    assert evaluation.score < 2.0


def test_action_confirmation_is_strictly_bound_to_persisted_id(tmp_path) -> None:
    clock = FakeClock(START)
    _kernel, runtime = runtime_at(tmp_path, clock, Backend(proposal(proactive=False)))
    result = runtime.handle_event(
        KernelEvent("source", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )
    assert result.action_id is not None

    with pytest.raises(KeyError, match="unknown action"):
        runtime.acknowledge_action("action:forged")

    delivered = runtime.acknowledge_action(result.action_id, outcome="delivered")
    duplicate = runtime.acknowledge_action(result.action_id, outcome="delivered")
    assert delivered is not None and delivered.duplicate is False
    assert duplicate is not None and duplicate.duplicate is True


def test_semantic_evidence_not_message_count_changes_relationship(tmp_path) -> None:
    clock = FakeClock(START)
    kernel, runtime = runtime_at(tmp_path, clock, Backend(proposal(proactive=False)))
    before = kernel.state.relationship
    runtime.handle_event(KernelEvent("empty", START, EventKind.USER_MESSAGE, {}))
    after_empty = kernel.state.relationship
    runtime.handle_event(
        KernelEvent("thanks", START, EventKind.USER_MESSAGE, {"message": "谢谢你，你真的帮到我了"})
    )

    assert after_empty == before
    assert kernel.state.relationship.trust > before.trust
    assert any("user_appreciation" in item for item in kernel.recent_memories())


def test_persona_and_user_configuration_survive_restart(tmp_path) -> None:
    first = ConfigStore.open(tmp_path)
    first.replace_user(UserSettings(timezone="Asia/Shanghai"), ConfigActor.USER)
    first.replace_persona(PersonaProfile(name="Mira"), ConfigActor.SYSTEM_ADMIN)

    second = ConfigStore.open(tmp_path)
    assert second.user.timezone == "Asia/Shanghai"
    assert second.persona.name == "Mira"


def test_sqlite_event_uniqueness_is_safe_across_concurrent_writers(tmp_path) -> None:
    path = tmp_path / "runtime.db"
    first = SQLiteRuntimeStore(path)
    second = SQLiteRuntimeStore(path)
    barrier = Barrier(2)
    outcomes: list[bool] = []
    event = KernelEvent("same", START, EventKind.TIME_TICK, {})

    def append(store: SQLiteRuntimeStore) -> None:
        barrier.wait()
        outcomes.append(store.append(event))

    threads = [Thread(target=append, args=(store,)) for store in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes) == [False, True]
    assert len(SQLiteRuntimeStore(path).read_all()) == 1


def test_pause_cancels_rendered_outbox_actions(tmp_path) -> None:
    clock = FakeClock(START)
    kernel, runtime = runtime_at(tmp_path, clock, Backend(proposal(proactive=False)))
    result = runtime.handle_event(
        KernelEvent("source", START, EventKind.USER_MESSAGE, {"message": "你好"})
    )
    assert result.action_id is not None

    kernel.process(KernelEvent("pause", START, EventKind.USER_PAUSE, {}))

    action = kernel.runtime_store.get_action(result.action_id)
    assert action is not None and action.status == "cancelled"
    with pytest.raises(ValueError, match="cannot deliver action"):
        runtime.acknowledge_action(result.action_id, outcome="delivered")
