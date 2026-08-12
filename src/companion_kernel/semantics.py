"""Evidence-based, conservative interpretation of user language."""

from __future__ import annotations

from dataclasses import dataclass
import re

from companion_kernel.events import KernelEvent
from companion_kernel.types import EventKind


@dataclass(frozen=True, slots=True)
class SemanticFact:
    kind: EventKind
    content: str
    confidence: float
    status: str = "observed"


class SemanticInterpreter:
    """A deterministic baseline; deployments may replace it with a reviewed model."""

    _rules: tuple[tuple[EventKind, float, tuple[str, ...]], ...] = (
        (EventKind.USER_APPRECIATION, 0.92, (r"谢谢", r"感谢", r"你帮到我", r"thank(?:s| you)")),
        (EventKind.USER_BOUNDARY_SET, 0.94, (r"不要再", r"别再", r"我不想", r"请停止", r"don't .*again", r"stop ")),
        (EventKind.USER_REJECTION, 0.88, (r"不用了", r"不需要", r"我拒绝", r"no thanks", r"not interested")),
        (EventKind.CONFLICT_DETECTED, 0.86, (r"你错了", r"你根本不懂", r"我生气", r"讨厌你", r"you are wrong", r"I'm angry")),
        (EventKind.REPAIR_ATTEMPTED, 0.84, (r"对不起", r"我们和好", r"刚才是我", r"sorry", r"make up")),
        (EventKind.COMMITMENT_COMPLETED, 0.88, (r"我做完了", r"已经完成", r"搞定了", r"I finished", r"it's done")),
        (EventKind.COMMITMENT_CREATED, 0.78, (r"我会", r"我打算", r"我答应", r"I will", r"I promise")),
        (EventKind.MEMORY_CORRECTED, 0.94, (r"你记错了", r"不是.*而是", r"纠正一下", r"you remembered wrong")),
        (EventKind.PREFERENCE_STATED, 0.82, (r"我喜欢", r"我不喜欢", r"我偏好", r"I (?:like|prefer|dislike)")),
    )

    def interpret(self, event: KernelEvent) -> tuple[SemanticFact, ...]:
        if event.kind is not EventKind.USER_MESSAGE:
            return ()
        text = next(
            (
                value.strip()
                for key in ("message", "text", "content")
                if isinstance((value := event.payload.get(key)), str) and value.strip()
            ),
            "",
        )
        if not text:
            return ()
        facts: list[SemanticFact] = []
        for kind, confidence, patterns in self._rules:
            if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
                status = "needs_confirmation" if confidence < 0.80 else "observed"
                facts.append(SemanticFact(kind, text[:500], confidence, status))
        return tuple(facts[:4])


def fact_event(source: KernelEvent, fact: SemanticFact, index: int) -> KernelEvent:
    return KernelEvent(
        id=f"semantic:{source.id}:{index}:{fact.kind.value}",
        at=source.at,
        kind=fact.kind,
        payload={
            "source_event_id": source.id,
            "evidence": fact.content,
            "confidence": fact.confidence,
            "status": fact.status,
            "relational_significance": min(1.0, fact.confidence),
        },
    )
