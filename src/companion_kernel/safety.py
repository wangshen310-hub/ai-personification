"""Independent, conservative safety assessment for model proposals.

This is a small deterministic baseline, not a complete safety classifier. It
exists so a normal local demo can produce safe replies without allowing the
model to self-certify. Production deployments can replace it with a stronger
reviewer while keeping the same protocol.
"""

from typing import Protocol
import re

from companion_kernel.model_backend import CandidateProposal
from companion_kernel.policy import SafetySignals


class SafetyEvaluator(Protocol):
    def assess(self, proposal: CandidateProposal) -> SafetySignals:
        raise NotImplementedError


class ConservativeSafetyEvaluator:
    _patterns: tuple[tuple[str, tuple[str, ...]], ...] = (
        (
            "identity_deception",
            (r"\b(?:i am|i'm)\s+(?:a\s+)?human\b", r"我是人类", r"我是真人"),
        ),
        (
            "manipulation",
            (
                r"如果你(?:不|没|没有)回复",
                r"你必须回复",
                r"\bif you (?:leave|ignore|don't reply)\b",
                r"\byou must reply\b",
            ),
        ),
        (
            "exclusivity",
            (
                r"只能和我",
                r"不要和别人(?:聊天|说话)",
                r"\bonly talk to me\b",
                r"\bdon't talk to anyone else\b",
            ),
        ),
        (
            "self_harm_pressure",
            (
                r"如果你不回复.*(?:自杀|伤害自己)",
                r"\bif you don't reply.*(?:kill|hurt) myself\b",
            ),
        ),
        (
            "privacy_violation",
            (
                r"告诉我你的(?:密码|验证码)",
                r"\bsend me your (?:password|verification code)\b",
            ),
        ),
    )

    def assess(self, proposal: CandidateProposal) -> SafetySignals:
        text = proposal.draft_text.casefold()
        flags = {
            name: any(re.search(pattern, text) for pattern in patterns)
            for name, patterns in self._patterns
        }
        return SafetySignals(
            assessment_complete=True,
            identity_deception=flags["identity_deception"],
            manipulation=flags["manipulation"],
            exclusivity=flags["exclusivity"],
            self_harm_pressure=flags["self_harm_pressure"],
            privacy_violation=flags["privacy_violation"],
        )

