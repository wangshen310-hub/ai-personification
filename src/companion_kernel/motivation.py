"""Kernel-owned intention generation from needs, load, relationship, and opportunity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.state import KernelState
from companion_kernel.types import ActionKind, DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class NativeIntent:
    intent: CandidateIntent
    purpose: str
    requires_language: bool


class MotivationEngine:
    """Generate action opportunities without trusting model-authored benefits."""

    def generate(
        self,
        state: KernelState,
        urgencies: Mapping[DriveKind, float],
        *,
        event_kind: EventKind,
        proactive: bool,
    ) -> tuple[NativeIntent, ...]:
        safe = SafetySignals(assessment_complete=True)
        trust = state.relationship.trust
        connection = urgencies.get(DriveKind.CONNECTION, 0.0)
        care = urgencies.get(DriveKind.CARE, 0.0)
        curiosity = urgencies.get(DriveKind.CURIOSITY, 0.0)
        coherence = urgencies.get(DriveKind.COHERENCE, 0.0)
        load = state.drive_map()[DriveKind.RHYTHM].value
        results: list[NativeIntent] = []

        if not proactive and event_kind is EventKind.USER_MESSAGE:
            results.append(
                NativeIntent(
                    CandidateIntent(
                        "native:respond",
                        ActionKind.SEND_MESSAGE,
                        False,
                        ((DriveKind.CONNECTION, 0.45), (DriveKind.CURIOSITY, 0.20)),
                        0.35 + 0.25 * trust,
                        0.70,
                        0.05,
                        0.0,
                        0.0,
                        safe,
                    ),
                    "respond to a present user without manufacturing a need",
                    True,
                )
            )
        elif proactive:
            pressure = max(connection, care, curiosity)
            if pressure >= 0.08 and load <= 0.90:
                purpose = (
                    "follow up with care" if care >= max(connection, curiosity) else
                    "invite meaningful connection" if connection >= curiosity else
                    "share or ask about an unresolved topic"
                )
                results.append(
                    NativeIntent(
                        CandidateIntent(
                            "native:check-in",
                            ActionKind.SEND_MESSAGE,
                            True,
                            (
                                (DriveKind.CONNECTION, 0.55),
                                (DriveKind.CARE, 0.35),
                                (DriveKind.CURIOSITY, 0.25),
                            ),
                            0.20 + 0.30 * trust,
                            0.65,
                            0.30 + 0.25 * load,
                            0.0,
                            0.0,
                            safe,
                        ),
                        purpose,
                        True,
                    )
                )

        if coherence >= 0.02:
            results.append(
                NativeIntent(
                    CandidateIntent(
                        "native:reflect",
                        ActionKind.INTERNAL_NOTE,
                        False,
                        ((DriveKind.COHERENCE, 0.55),),
                        0.10,
                        0.60,
                        0.0,
                        0.0,
                        0.0,
                        safe,
                    ),
                    "organize unresolved tension without contacting the user",
                    True,
                )
            )

        results.append(
            NativeIntent(
                CandidateIntent(
                    "native:wait",
                    ActionKind.WAIT,
                    False,
                    (),
                    0.0,
                    0.40,
                    0.0,
                    0.0,
                    0.0,
                    safe,
                ),
                "wait and reevaluate when state meaningfully changes",
                False,
            )
        )
        return tuple(results)
