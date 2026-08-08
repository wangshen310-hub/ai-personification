"""Deterministic personality kernel for a long-term AI companion."""

from typing import TYPE_CHECKING

from companion_kernel.agent_runtime import AgentRunResult, AgentRuntime
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import (
    CandidateProposal,
    ModelAdapter,
    ModelBackend,
    ModelContext,
    ModelTurn,
    ToolRequest,
    create_model_backend,
)
from companion_kernel.permissions import DIALOGUE_PERMISSIONS, PermissionProfile
from companion_kernel.policy import CandidateIntent

if TYPE_CHECKING:
    from companion_kernel.simulation import SimulationRunner

__all__ = [
    "AgentRunResult",
    "AgentRuntime",
    "CandidateIntent",
    "CandidateProposal",
    "DIALOGUE_PERMISSIONS",
    "KernelEvent",
    "ModelBackend",
    "ModelAdapter",
    "ModelContext",
    "ModelSettings",
    "ModelTurn",
    "PermissionProfile",
    "PersonalityKernel",
    "SimulationRunner",
    "ToolRequest",
    "create_model_backend",
]


def __getattr__(name: str) -> object:
    if name == "SimulationRunner":
        from companion_kernel.simulation import SimulationRunner

        globals()[name] = SimulationRunner
        return SimulationRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
