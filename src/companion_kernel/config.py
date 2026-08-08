from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from companion_kernel.types import ConfigActor, DriveKind


@dataclass(frozen=True, slots=True)
class SystemPolicy:
    proactive_limit_per_24h: int = 1
    default_quiet_start: time = time(22, 0)
    default_quiet_end: time = time(8, 0)


@dataclass(frozen=True, slots=True)
class UserSettings:
    timezone: str | None = None
    quiet_start: time = time(22, 0)
    quiet_end: time = time(8, 0)
    proactive_enabled: bool = True
    proactive_limit_per_24h: int = 1


@dataclass(frozen=True, slots=True)
class LearnedPersona:
    drive_weight_offsets: tuple[tuple[DriveKind, float], ...] = ()


class ConfigStore:
    def __init__(
        self,
        system: SystemPolicy,
        user: UserSettings,
        learned: LearnedPersona,
    ) -> None:
        self._system = system
        self._user = user
        self._learned = learned

    @classmethod
    def defaults(cls) -> "ConfigStore":
        system = SystemPolicy()
        user = UserSettings(
            quiet_start=system.default_quiet_start,
            quiet_end=system.default_quiet_end,
        )
        return cls(system, user, LearnedPersona())

    @property
    def system(self) -> SystemPolicy:
        return self._system

    @property
    def user(self) -> UserSettings:
        return self._user

    @property
    def learned(self) -> LearnedPersona:
        return self._learned

    def replace_system(self, value: SystemPolicy, actor: ConfigActor) -> None:
        if actor is not ConfigActor.SYSTEM_ADMIN:
            raise PermissionError("only system_admin may replace system policy")
        if value.proactive_limit_per_24h != 1:
            raise ValueError("version 1 hard limits are fixed")
        self._system = value

    def replace_user(self, value: UserSettings, actor: ConfigActor) -> None:
        if actor is not ConfigActor.USER:
            raise PermissionError("only user may replace user settings")
        if value.proactive_limit_per_24h < 0:
            raise ValueError("proactive limit cannot be negative")
        if value.proactive_limit_per_24h > self._system.proactive_limit_per_24h:
            raise ValueError("user limit exceeds system maximum")
        if value.timezone is not None:
            try:
                ZoneInfo(value.timezone)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("unknown IANA timezone") from exc
        self._user = value

    def replace_learned(self, value: LearnedPersona, actor: ConfigActor) -> None:
        if actor is not ConfigActor.KERNEL:
            raise PermissionError("only kernel may replace learned persona")
        kinds = [kind for kind, _ in value.drive_weight_offsets]
        if len(kinds) != len(set(kinds)):
            raise ValueError("duplicate drive weight offset")
        if any(not -0.25 <= offset <= 0.25 for _, offset in value.drive_weight_offsets):
            raise ValueError("learned weight offset outside [-0.25, 0.25]")
        self._learned = value
