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
from companion_kernel.permissions import DIALOGUE_PERMISSIONS, PermissionProfile
from companion_kernel.policy import SafetySignals
from companion_kernel.safety import ConservativeSafetyEvaluator, SafetyEvaluator
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
    ) -> None:
        self._kernel = kernel
        self._backend = backend
        self._permissions = permissions
        self._safety = safety or ConservativeSafetyEvaluator()
        self._evaluator = evaluator or ConservativeProposalEvaluator()

    def handle_event(
        self,
        event: KernelEvent,
        *,
        memory: tuple[str, ...] = (),
    ) -> AgentRunResult:
        if self._kernel.contains_event(event.id):
            result = self._kernel.process(event)
            return AgentRunResult(result, (), None, None, (), ())

        post_event_state = self._kernel.preview(event)
        urgencies = self._kernel.urgencies_for(post_event_state, event.at)
        context = ModelContext(
            event=event,
            state=post_event_state,
            urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
            user_message=_user_message(event),
            proactive_cycle=event.kind is EventKind.DECISION_TICK,
            allowed_actions=tuple(ActionKind),
            persona=self._kernel.persona_context(),
            allowed_tools=self._permissions.allowed_tools,
            memory=_merge_memory(memory, self._kernel.recent_dialogue()),
        )

        model_error: str | None = None
        try:
            turn = self._backend.propose(context)
            checked, blocked = self._check_proposals(turn.proposals, context)
        except ModelBackendError as exc:
            model_error = f"{type(exc).__name__}: {str(exc)[:240]}"
            checked, blocked = (), ()

        kernel_result = self._kernel.process(event, tuple(item.intent for item in checked))
        response_text: str | None = None
        approved: tuple[ToolRequest, ...] = ()
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
        return AgentRunResult(
            kernel=kernel_result,
            proposals=tuple(checked),
            response_text=response_text,
            model_error=model_error,
            blocked_tool_requests=tuple(blocked),
            approved_tool_requests=tuple(approved),
        )

    def acknowledge_action(
        self,
        source_event: KernelEvent,
        result: AgentRunResult,
        *,
        at: datetime | None = None,
    ) -> KernelResult | None:
        """Persist a selected action only after its executor confirms success.

        The deterministic event id makes retries safe. Message text is retained
        as recent dialogue so a new model process can recover conversational
        continuity after a restart.
        """

        decision = result.kernel.decision
        if decision is None or decision.selected.action in {ActionKind.NOOP, ActionKind.WAIT}:
            return None
        selected = next(
            (item for item in result.proposals if item.intent.id == decision.selected.id),
            None,
        )
        if selected is None:
            return None
        event_at = at or source_event.at
        event_id = f"action:{source_event.id}:{selected.intent.id}"
        if decision.selected.action is ActionKind.SEND_MESSAGE:
            if result.response_text is None:
                return None
            kind = (
                EventKind.PROACTIVE_SENT
                if decision.selected.proactive
                else EventKind.ASSISTANT_MESSAGE_SENT
            )
            payload = {
                "source_event_id": source_event.id,
                "candidate_id": selected.intent.id,
                "message": result.response_text,
            }
        elif decision.selected.action is ActionKind.INTERNAL_NOTE:
            kind = EventKind.INTERNAL_NOTE_CREATED
            payload = {
                "source_event_id": source_event.id,
                "candidate_id": selected.intent.id,
                "note": selected.draft_text,
            }
        else:
            return None
        return self._kernel.process(KernelEvent(event_id, event_at, kind, payload))

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
