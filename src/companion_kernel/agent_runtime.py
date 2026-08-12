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
        source_duplicate = self._kernel.contains_event(event.id)
        decision_event = event
        if event.kind is EventKind.USER_MESSAGE:
            if source_duplicate:
                self._kernel.refresh()
            else:
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
                        at=derived.recorded_at,
                    )
                    self._kernel.consider_persona_learning(fact.kind)
            post_event_state = self._kernel.state
            decision_event = KernelEvent(
                f"decision:{event.id}",
                event.at,
                EventKind.DECISION_TICK,
                {"source_event_id": event.id, "proactive_cycle": False},
                recorded_at=event.recorded_at,
            )
        else:
            post_event_state = self._kernel.preview(event)
        existing_action = (
            self._kernel.runtime_store.get_action_for_decision(decision_event.id)
            if self._kernel.runtime_store is not None
            else None
        )
        if existing_action is not None:
            restored = self._kernel.refresh(duplicate=True)
            active = existing_action.status not in {"failed", "cancelled"}
            return AgentRunResult(
                restored,
                (),
                existing_action.content
                if active and existing_action.action == ActionKind.SEND_MESSAGE.value
                else None,
                None,
                (),
                (),
                existing_action.action_id if active else None,
            )
        if self._kernel.contains_event(decision_event.id):
            restored = self._kernel.refresh(duplicate=True)
            return AgentRunResult(restored, (), None, None, (), ())
        urgencies = self._kernel.urgencies_for(post_event_state, event.at)
        proactive_cycle = event.kind is EventKind.DECISION_TICK and bool(
            event.payload.get("proactive_cycle", True)
        )
        native = self._motivation.generate(
            post_event_state,
            urgencies,
            event_kind=event.kind,
            proactive=proactive_cycle,
            persona_values=self._kernel.persona_values(),
        )
        native_intents = tuple(item.intent for item in native)
        native_by_id = {item.intent.id: item for item in native}
        provisional = self._kernel.preview_decision(decision_event, native_intents)
        selected_native = native_by_id.get(provisional.selected.id)
        if selected_native is None or selected_native.intent.action is ActionKind.WAIT:
            kernel_result = self._kernel.process(decision_event, native_intents)
            return AgentRunResult(kernel_result, (), None, None, (), ())

        context = ModelContext(
            event=event,
            state=post_event_state,
            urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
            user_message=_user_message(event),
            proactive_cycle=proactive_cycle,
            allowed_actions=(selected_native.intent.action,),
            persona=self._kernel.persona_context(),
            allowed_tools=tuple(
                tool
                for tool in self._permissions.allowed_tools
                if tool != "memory.write" or self._permissions.can_write_memory
            ),
            memory_write_allowed=self._permissions.can_write_memory,
            memory=_merge_memory(
                memory + self._kernel.recent_memories(4),
                self._kernel.recent_dialogue(),
            ),
            intent_guidance=(
                f"{selected_native.intent.action.value}: {selected_native.purpose}",
            ),
        )

        model_error: str | None = None
        checked: tuple[CandidateProposal, ...] = ()
        try:
            turn = self._backend.propose(context)
            matching = tuple(
                proposal
                for proposal in turn.proposals
                if proposal.intent.action is selected_native.intent.action
            )
            checked, blocked = self._check_proposals(matching[:1], context)
        except ModelBackendError as exc:
            model_error = f"{type(exc).__name__}: {str(exc)[:240]}"
            checked, blocked = (), ()

        bound_candidate = None
        rendered = None
        if checked:
            candidate = checked[0]
            bound_intent = replace(
                selected_native.intent,
                id=selected_native.intent.id,
                intrusion_cost=max(
                    selected_native.intent.intrusion_cost,
                    candidate.intent.intrusion_cost,
                ),
                repetition=max(
                    selected_native.intent.repetition,
                    candidate.intent.repetition,
                ),
                safety=candidate.intent.safety,
            )
            bound_candidate = replace(candidate, intent=bound_intent)
            if not bound_candidate.intent.safety.violations():
                rendered = bound_candidate

        # A failed or blocked render must become an explicit kernel-owned wait,
        # not an accidental NOOP. Keep a blocked bound candidate in the audit so
        # the reason remains visible while the wait candidate fails closed.
        wait_intent = next(
            (item.intent for item in native if item.intent.action is ActionKind.WAIT),
            None,
        )
        if rendered is not None:
            candidates = (rendered.intent,) + ((wait_intent,) if wait_intent is not None else ())
        elif bound_candidate is not None and wait_intent is not None:
            candidates = (bound_candidate.intent, wait_intent)
        elif wait_intent is not None:
            candidates = (wait_intent,)
        else:
            candidates = ()

        def build_action(decision, next_state):
            if rendered is None or decision.selected.id != rendered.intent.id:
                return None
            if decision.selected.action not in {
                ActionKind.SEND_MESSAGE,
                ActionKind.INTERNAL_NOTE,
            }:
                return None
            action_id = f"action:{decision_event.id}:{decision.selected.id}"
            action_at = max(decision_event.at, next_state.last_event_at)
            return OutboxAction(
                action_id=action_id,
                source_event_id=event.id,
                decision_event_id=decision_event.id,
                candidate_id=decision.selected.id,
                action=decision.selected.action.value,
                proactive=decision.selected.proactive,
                content=rendered.draft_text,
                status="rendered",
                created_at=action_at,
                updated_at=action_at,
            )

        kernel_result = self._kernel.process(
            decision_event,
            candidates,
            action_builder=build_action,
        )
        response_text: str | None = None
        approved: tuple[ToolRequest, ...] = ()
        action_id: str | None = None
        if kernel_result.decision is not None:
            selected_id = kernel_result.decision.selected.id
            selected = rendered if rendered is not None and rendered.intent.id == selected_id else None
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
                if self._kernel.runtime_store is not None:
                    action = self._kernel.runtime_store.get_action_for_decision(decision_event.id)
                    action_id = action.action_id if action is not None else None
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
        requested_at = at or self._kernel.now()
        event_at = max(requested_at, action.updated_at, self._kernel.state.last_event_at)
        if outcome != "delivered":
            if action.status == outcome:
                return None
            changed = store.transition_action(
                action_id,
                expected=("planned", "rendered", "queued", "sending"),
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
            KernelEvent(
                event_id,
                event_at,
                kind,
                payload,
                recorded_at=max(self._kernel.now(), event_at),
            ),
            event_at,
        )
        return self._kernel.refresh(duplicate=not created)

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
                or (request.name == "memory.write" and not self._permissions.can_write_memory)
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
    dialogue = tuple(item for item in dialogue if not item.startswith("internal_note:"))
    remaining = 8 - len(semantic)
    return semantic + dialogue[-remaining:] if remaining else semantic
