"""Deterministic personality kernel for a long-term AI companion."""

from typing import TYPE_CHECKING

from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent

if TYPE_CHECKING:
    from companion_kernel.simulation import SimulationRunner

__all__ = ["CandidateIntent", "KernelEvent", "PersonalityKernel", "SimulationRunner"]


def __getattr__(name: str) -> object:
    if name == "SimulationRunner":
        from companion_kernel.simulation import SimulationRunner

        globals()[name] = SimulationRunner
        return SimulationRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
