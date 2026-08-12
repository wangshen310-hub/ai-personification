"""Deterministic personality kernel for a long-term AI companion."""

from typing import TYPE_CHECKING

from companion_kernel.agent_runtime import AgentRunResult, AgentRuntime
from companion_kernel.events import KernelEvent
from companion_kernel.evaluation import ConservativeProposalEvaluator, ProposalEvaluator
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.config import ConfigStore, ModelSettings, PersonaProfile
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
from companion_kernel.relationship import RelationshipState
from companion_kernel.motivation import MotivationEngine, NativeIntent
from companion_kernel.semantics import SemanticFact, SemanticInterpreter
from companion_kernel.storage import OutboxAction, SQLiteRuntimeStore

if TYPE_CHECKING:
    from companion_kernel.simulation import SimulationRunner

__all__ = [
    "AgentRunResult",
    "AgentRuntime",
    "CandidateIntent",
    "CandidateProposal",
    "DIALOGUE_PERMISSIONS",
    "ConservativeProposalEvaluator",
    "ConfigStore",
    "KernelEvent",
    "ModelBackend",
    "ModelAdapter",
    "ModelContext",
    "ModelSettings",
    "ModelTurn",
    "MotivationEngine",
    "NativeIntent",
    "OutboxAction",
    "PersonaProfile",
    "PermissionProfile",
    "ProposalEvaluator",
    "RelationshipState",
    "SQLiteRuntimeStore",
    "SemanticFact",
    "SemanticInterpreter",
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
