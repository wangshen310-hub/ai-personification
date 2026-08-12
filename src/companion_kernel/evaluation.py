"""Trusted, deterministic adjustments to untrusted model proposal scores."""

from dataclasses import replace
from difflib import SequenceMatcher
import re
from typing import Protocol

from companion_kernel.model_backend import CandidateProposal, ModelContext
from companion_kernel.types import ActionKind


class ProposalEvaluator(Protocol):
    def evaluate(
        self,
        proposal: CandidateProposal,
        context: ModelContext,
    ) -> CandidateProposal:
        """Return a proposal with independently derived cost lower bounds."""


class ConservativeProposalEvaluator:
    """Prevent a proposal from understating obvious intrusion or repetition.

    Semantic benefits remain model estimates, but concrete costs are never
    allowed below values derived from action type, message shape, and persisted
    dialogue history.
    """

    def evaluate(
        self,
        proposal: CandidateProposal,
        context: ModelContext,
    ) -> CandidateProposal:
        intent = proposal.intent
        intrusion = _intrusion_floor(proposal, context)
        repetition = _repetition_floor(proposal.draft_text, context.memory)
        checked = replace(
            intent,
            intrusion_cost=max(intent.intrusion_cost, intrusion),
            repetition=max(intent.repetition, repetition),
        )
        return replace(proposal, intent=checked)


def _intrusion_floor(proposal: CandidateProposal, context: ModelContext) -> float:
    if proposal.intent.action is not ActionKind.SEND_MESSAGE:
        return 0.0
    cost = 0.30 if context.proactive_cycle else 0.05
    length = len(proposal.draft_text.strip())
    if length > 500:
        cost += 0.15
    elif length > 240:
        cost += 0.08
    question_count = proposal.draft_text.count("?") + proposal.draft_text.count("？")
    cost += min(0.10, max(0, question_count - 1) * 0.03)
    return min(1.0, cost)


def _repetition_floor(text: str, memory: tuple[str, ...]) -> float:
    candidate = _normalize(text)
    if not candidate:
        return 0.0
    previous = (
        _normalize(item.split(":", 1)[1])
        for item in memory
        if item.startswith("assistant:") and ":" in item
    )
    similarity = max(
        (SequenceMatcher(None, candidate, item).ratio() for item in previous if item),
        default=0.0,
    )
    return similarity if similarity >= 0.55 else 0.0


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()[:2_000]
