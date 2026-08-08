from datetime import time

import pytest

from companion_kernel.config import ConfigStore, LearnedPersona, ModelSettings, SystemPolicy, UserSettings
from companion_kernel.types import ConfigActor, DriveKind


def test_model_cannot_modify_any_config_layer() -> None:
    store = ConfigStore.defaults()

    with pytest.raises(PermissionError):
        store.replace_user(UserSettings(timezone="Asia/Shanghai"), ConfigActor.MODEL)

    with pytest.raises(PermissionError):
        store.replace_system(SystemPolicy(), ConfigActor.MODEL)

    with pytest.raises(PermissionError):
        store.replace_learned(LearnedPersona(), ConfigActor.MODEL)

    with pytest.raises(PermissionError):
        store.replace_model(ModelSettings(model="local"), ConfigActor.MODEL)


def test_user_limit_cannot_exceed_system_limit() -> None:
    store = ConfigStore.defaults()

    with pytest.raises(ValueError, match="system maximum"):
        store.replace_user(
            UserSettings(timezone="Asia/Shanghai", proactive_limit_per_24h=2),
            ConfigActor.USER,
        )


def test_authorized_layers_accept_valid_replacements() -> None:
    store = ConfigStore.defaults()
    store.replace_user(
        UserSettings(
            timezone="Asia/Shanghai",
            quiet_start=time(23, 0),
            quiet_end=time(7, 30),
            proactive_limit_per_24h=1,
        ),
        ConfigActor.USER,
    )
    store.replace_learned(
        LearnedPersona(drive_weight_offsets=((DriveKind.CURIOSITY, 0.1),)),
        ConfigActor.KERNEL,
    )

    assert store.user.timezone == "Asia/Shanghai"
    assert store.learned.drive_weight_offsets == ((DriveKind.CURIOSITY, 0.1),)


def test_system_admin_can_select_model_backend() -> None:
    store = ConfigStore.defaults()
    store.replace_model(ModelSettings(provider="ollama", model="local-instruct"), ConfigActor.SYSTEM_ADMIN)
    assert store.model.model == "local-instruct"
