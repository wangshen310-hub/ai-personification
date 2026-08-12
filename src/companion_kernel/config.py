from dataclasses import dataclass
from datetime import time
from math import isfinite
from urllib.parse import urlparse
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


@dataclass(frozen=True, slots=True)
class PersonaProfile:
    """Stable identity and expression anchors supplied to every model turn."""

    name: str = "Companion"
    traits: tuple[str, ...] = ("warm", "curious", "thoughtful", "independent")
    values: tuple[str, ...] = ("honesty", "mutual respect", "growth")
    communication_style: str = "natural, concise, attentive, and willing to have a viewpoint"

    def __post_init__(self) -> None:
        if not self.name.strip() or len(self.name) > 80:
            raise ValueError("persona name must be a non-empty bounded string")
        if not 1 <= len(self.traits) <= 8 or not 1 <= len(self.values) <= 8:
            raise ValueError("persona traits and values must contain between 1 and 8 items")
        if any(not item.strip() or len(item) > 120 for item in self.traits + self.values):
            raise ValueError("persona traits and values must be non-empty bounded strings")
        if not self.communication_style.strip() or len(self.communication_style) > 500:
            raise ValueError("persona communication style must be a non-empty bounded string")

    def context(self) -> tuple[str, ...]:
        return (
            f"name: {self.name}",
            "traits: " + ", ".join(self.traits),
            "values: " + ", ".join(self.values),
            f"communication_style: {self.communication_style}",
        )


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Configuration for a model proposal backend.

    The model is deliberately configured separately from the personality state.
    A model may propose text and actions, but it cannot change these settings.
    """

    provider: str = "ollama"
    model: str = ""
    base_url: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 30.0
    max_output_chars: int = 6_000
    max_output_tokens: int = 1_200
    max_candidates: int = 3
    api_key_env: str = "OPENAI_API_KEY"

    def __post_init__(self) -> None:
        if self.provider not in {"ollama", "openai_responses", "codex_cli"}:
            raise ValueError("unsupported model provider")
        parsed_url = urlparse(self.base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("model base_url must be an HTTP(S) URL")
        if not isfinite(self.timeout_seconds) or not 0.1 <= self.timeout_seconds <= 300.0:
            raise ValueError("model timeout must be between 0.1 and 300 seconds")
        if not 256 <= self.max_output_chars <= 100_000:
            raise ValueError("model max_output_chars is outside the supported range")
        if not 32 <= self.max_output_tokens <= 32_000:
            raise ValueError("model max_output_tokens is outside the supported range")
        if not 1 <= self.max_candidates <= 8:
            raise ValueError("model max_candidates must be between 1 and 8")
        if not self.api_key_env.strip():
            raise ValueError("model api_key_env cannot be empty")


class ConfigStore:
    def __init__(
        self,
        system: SystemPolicy,
        user: UserSettings,
        learned: LearnedPersona,
        model: ModelSettings | None = None,
        persona: PersonaProfile | None = None,
    ) -> None:
        self._system = system
        self._user = user
        self._learned = learned
        self._model = model or ModelSettings()
        self._persona = persona or PersonaProfile()

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

    @property
    def model(self) -> ModelSettings:
        return self._model

    @property
    def persona(self) -> PersonaProfile:
        return self._persona

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

    def replace_model(self, value: ModelSettings, actor: ConfigActor) -> None:
        if actor is not ConfigActor.SYSTEM_ADMIN:
            raise PermissionError("only system_admin may replace model settings")
        self._model = value

    def replace_persona(self, value: PersonaProfile, actor: ConfigActor) -> None:
        if actor is not ConfigActor.SYSTEM_ADMIN:
            raise PermissionError("only system_admin may replace core persona")
        self._persona = value
