"""Explicit tool and capability profiles for model agents."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PermissionProfile:
    """Capabilities visible to an agent for one run.

    An empty tool list is intentional: the default dialogue profile can only
    propose conversation and cannot access files, a shell, or external systems.
    """

    name: str
    allowed_tools: tuple[str, ...] = ()
    can_write_memory: bool = False

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("permission profile name cannot be empty")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("permission profile contains duplicate tools")
        if any(not item.strip() for item in self.allowed_tools):
            raise ValueError("permission profile contains an empty tool name")

    def allows_tool(self, name: str) -> bool:
        return name in self.allowed_tools


DIALOGUE_PERMISSIONS = PermissionProfile("dialogue")


def coding_permissions(*tools: str) -> PermissionProfile:
    """Create an explicit coding profile; no coding tools are granted by default."""

    return PermissionProfile("coding", tuple(tools))

