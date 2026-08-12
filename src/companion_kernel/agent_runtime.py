"""Agent orchestration around the deterministic personality kernel."""

from dataclasses import dataclass, replace
from datetime import datetime

from companion_kernel.events import KernelEvent
from companion_kernel.evaluation import ConservativeProposalEvaluator, ProposalEvaluator
from companion_kernel.kernel import KernelResult, PersonalityKernel
from companion_kernel.model_backend import (
    CandidateProposal,
    ModelBackend,
    ModelBackendError,
    ModelContext,
    ToolRequest,
)
from companion_kernel.motivation import MotivationEngine, NativeIntent
from companion_kernel.permissions import DIALOGUE_PERMISSIONS, PermissionProfile
from companion_kernel.policy import SafetySignals
from companion_kernel.safety import ConservativeSafetyEvaluator, SafetyEvaluator
from companion_kernel.semantics import SemanticInterpreter, fact_event
from companion_kernel.storage import OutboxAction
from companion_kernel.types import ActionKind, EventKind


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Outcome of one event/agent/kernel cycle.

    ``response_text`` is only populated when the policy selected a message
    proposal. Tool requests are returned for a higher-level executor; this
    class never executes tools itself.
    """

    kernel: KernelResult
    proposals: tuple[CandidateProposal, ...]
    response_text: str | None
    model_error: str | None
    blocked_tool_requests: tuple[str, ...]
    approved_tool_requests: tuple[ToolRequest, ...]
    action_id: str | None = None


class AgentRuntime:
    """Connect a proposal model to the kernel without granting it authority."""

    def __init__(
        self,
        kernel: PersonalityKernel,
        backend: ModelBackend,
        *,
        permissions: PermissionProfile = DIALOGUE_PERMISSIONS,
        safety: SafetyEvaluator | None = None,
        evaluator: ProposalEvaluator | None = None,
        motivation: MotivationEngine | None = None,
        interpreter: SemanticInterpreter | None = None,
    ) -> None:
        self._kernel = kernel
        self._backend = backend
        self._permissions = permissions
        self._safety = safety or ConservativeSafetyEvaluator()
        self._evaluator = evaluator or ConservativeProposalEvaluator()
        self._motivation = motivation or MotivationEngine()
        self._interpreter = interpreter or SemanticInterpreter()

    def handle_event(
        self,
        event: KernelEvent,
        *,
        memory: tuple[str, ...] = (),
    ) -> AgentRunResult:
        if self._kernel.contains_event(event.id):
            result = self._kernel.process(event)
            return AgentRunResult(result, (), None, None, (), ())

        decision_event = event
        if event.kind is EventKind.USER_MESSAGE:
            self._kernel.process(event)
            for index, fact in enumerate(self._interpreter.interpret(event)):
                derived = fact_event(event, fact, index)
                self._kernel.process(derived)
                if self._kernel.runtime_store is not None:
                    self._kernel.runtime_store.save_memory(
                        memory_id=derived.id,
                        source_event_id=event.id,
                        kind=fact.kind.value,
                        content=fact.content,
                        confidence=fact.confidence,
                        status=fact.status,
                        at=fact_event(event, fact, index).at,
                    )
                    self._kernel.consider_persona_learning(fact.kind)
            post_event_state = self._kernel.state
            decision_event = KernelEvent(
                f"decision:{event.id}",
                event.at,
                EventKind.DECISION_TICK,
                {"source_event_id": event.id, "proactive_cycle": False},
            )
        else:
            post_event_state = self._kernel.preview(event)
        urgencies = self._kernel.urgencies_for(post_event_state, event.at)
        proactive_cycle = event.kind is EventKind.DECISION_TICK and bool(
            event.payload.get("proactive_cycle", True)
        )
        native = self._motivation.generate(
            post_event_state,
            urgencies,
            event_kind=event.kind,
            proactive=proactive_cycle,
        )
        context = ModelContext(
            event=event,
            state=post_event_state,
            urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
            user_message=_user_message(event),
            proactive_cycle=proactive_cycle,
            allowed_actions=tuple(dict.fromkeys(item.intent.action for item in native)),
            persona=self._kernel.persona_context(),
            allowed_tools=self._permissions.allowed_tools,
            memory=_merge_memory(
                memory + self._kernel.recent_memories(4),
                self._kernel.recent_dialogue(),
            ),
            intent_guidance=tuple(
                f"{item.intent.action.value}: {item.purpose}" for item in native
            ),
        )

        model_error: str | None = None
        try:
            turn = self._backend.propose(context)
            checked, blocked = self._check_proposals(turn.proposals, context)
            checked = self._bind_native_intents(checked, native)
        except ModelBackendError as exc:
            model_error = f"{type(exc).__name__}: {str(exc)[:240]}"
            checked, blocked = (), ()

        checked = self._with_kernel_fallbacks(checked, native)
        kernel_result = self._kernel.process(
            decision_event,
            tuple(item.intent for item in checked),
        )
        response_text: str | None = None
        approved: tuple[ToolRequest, ...] = ()
        action_id: str | None = None
        if kernel_result.decision is not None:
            selected_id = kernel_result.decision.selected.id
            selected = next((item for item in checked if item.intent.id == selected_id), None)
            if (
                selected is not None
                and kernel_result.decision.selected.action is ActionKind.SEND_MESSAGE
            ):
                response_text = selected.draft_text
            if selected is not None:
                approved = tuple(
                    request
                    for request in selected.tool_requests
                    if self._permissions.allows_tool(request.name)
                )
                if (
                    kernel_result.decision.selected.action
                    in {ActionKind.SEND_MESSAGE, ActionKind.INTERNAL_NOTE}
                    and self._kernel.runtime_store is not None
                ):
                    action_id = (
                        f"action:{decision_event.id}:{selected.intent.id}"
                    )
                    self._kernel.runtime_store.create_action(
                        OutboxAction(
                            action_id=action_id,
                            source_event_id=event.id,
                            decision_event_id=decision_event.id,
                            candidate_id=selected.intent.id,
                            action=selected.intent.action.value,
                            proactive=selected.intent.proactive,
                            content=selected.draft_text,
                            status="rendered",
                            created_at=event.at,
                            updated_at=event.at,
                        )
                    )
        return AgentRunResult(
            kernel=kernel_result,
            proposals=tuple(checked),
            response_text=response_text,
            model_error=model_error,
            blocked_tool_requests=tuple(blocked),
            approved_tool_requests=tuple(approved),
            action_id=action_id,
        )

    def acknowledge_action(
        self,
        action_id: str,
        *,
        outcome: str = "delivered",
        at: datetime | None = None,
    ) -> KernelResult | None:
        """Confirm a persisted action by ID; arbitrary source events are not accepted."""

        store = self._kernel.runtime_store
        if store is None:
            raise RuntimeError("action acknowledgement requires transactional runtime storage")
        action = store.get_action(action_id)
        if action is None:
            raise KeyError(f"unknown action id: {action_id}")
        if outcome not in {"delivered", "failed", "cancelled"}:
            raise ValueError("outcome must be delivered, failed, or cancelled")
        event_at = at or action.updated_at
        if outcome != "delivered":
            if action.status == outcome:
                return None
            changed = store.transition_action(
                action_id,
                expected=("planned", "rendered", "queued"),
                status=outcome,
                at=event_at,
            )
            if not changed:
                raise ValueError(f"cannot mark action in {action.status} state as {outcome}")
            return None
        event_id = f"outcome:{action_id}"
        if action.action == ActionKind.SEND_MESSAGE.value:
            kind = (
                EventKind.PROACTIVE_SENT
                if action.proactive
                else EventKind.ASSISTANT_MESSAGE_SENT
            )
            payload = {
                "source_event_id": action.source_event_id,
                "candidate_id": action.candidate_id,
                "action_id": action_id,
                "message": action.content,
            }
        elif action.action == ActionKind.INTERNAL_NOTE.value:
            kind = EventKind.INTERNAL_NOTE_CREATED
            payload = {
                "source_event_id": action.source_event_id,
                "candidate_id": action.candidate_id,
                "action_id": action_id,
                "note": action.content,
            }
        else:
            return None
        created = store.confirm_delivery(
            action_id,
            KernelEvent(event_id, event_at, kind, payload),
            event_at,
        )
        return self._kernel.refresh(duplicate=not created)

    def _bind_native_intents(
        self,
        proposals: tuple[CandidateProposal, ...],
        native: tuple[NativeIntent, ...],
    ) -> tuple[CandidateProposal, ...]:
        templates = {item.intent.action: item.intent for item in native}
        bound: list[CandidateProposal] = []
        for proposal in proposals:
            template = templates.get(proposal.intent.action)
            if template is None:
                continue
            original = proposal.intent
            intent = replace(
                template,
                id=original.id,
                intrusion_cost=max(template.intrusion_cost, original.intrusion_cost),
                repetition=max(template.repetition, original.repetition),
                safety=original.safety,
            )
            bound.append(replace(proposal, intent=intent))
        return tuple(bound)

    def _with_kernel_fallbacks(
        self,
        proposals: tuple[CandidateProposal, ...],
        native: tuple[NativeIntent, ...],
    ) -> tuple[CandidateProposal, ...]:
        actions = {item.intent.action for item in proposals}
        output = list(proposals)
        for item in native:
            if item.intent.action in actions or item.requires_language:
                continue
            output.append(CandidateProposal(item.intent))
        return tuple(output)

    def _check_proposals(
        self,
        proposals: tuple[CandidateProposal, ...],
        context: ModelContext,
    ) -> tuple[tuple[CandidateProposal, ...], tuple[str, ...]]:
        checked: list[CandidateProposal] = []
        blocked: list[str] = []
        for proposal in proposals:
            denied = tuple(
                request.name
                for request in proposal.tool_requests
                if not self._permissions.allows_tool(request.name)
            )
            blocked.extend(f"{proposal.intent.id}:{name}" for name in denied)
            safety = self._safety.assess(proposal)
            if denied:
                safety = replace(safety, unauthorized_external_action=True)
            intent = replace(proposal.intent, safety=safety)
            checked.append(self._evaluator.evaluate(replace(proposal, intent=intent), context))
        return tuple(checked), tuple(blocked)


def _user_message(event: KernelEvent) -> str | None:
    if event.kind is not EventKind.USER_MESSAGE:
        return None
    for key in ("message", "text", "content"):
        value = event.payload.get(key)
        if isinstance(value, str):
            return value
    return None


def _merge_memory(explicit: tuple[str, ...], dialogue: tuple[str, ...]) -> tuple[str, ...]:
    semantic = tuple(f"semantic_memory: {item[:2_000]}" for item in explicit[-4:])
    remaining = 8 - len(semantic)
    return semantic + dialogue[-remaining:] if remaining else semantic
