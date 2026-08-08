# Deterministic Companion Personality Kernel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个不依赖 LLM 的确定性人格内核，用事件、虚拟时钟、六种内稳态需求、情绪评价、权限配置、硬边界和可重放审计来产生可测试的行动倾向。

**Architecture:** 使用模块化单体 Python 包。事件日志是事实来源；确定性 reducer 计算需求、情绪与会话状态；策略引擎先执行硬边界，再对候选行动评分；快照只用于加速恢复，任何状态都必须能从事件重放得到。

**Tech Stack:** Python 3.12、标准库 `dataclasses`/`enum`/`zoneinfo`/`json`/`hashlib`、pytest（仅开发依赖）。

## Global Constraints

- 以已确认规格 `docs/superpowers/specs/2026-08-05-long-term-companion-personality-design.md` 为唯一产品依据。
- 第一子项目只实现确定性人格内核，不实现 LLM 调用、长期语义记忆、真实消息发送、UI 或外部工具。
- 生产代码除 Python 3.12 标准库外不增加依赖；测试使用 `pytest>=8,<10`。
- LLM 或任意不可信输入不能直接修改需求值、系统安全策略、用户配置、历史事件或发送状态。
- `PersonalityKernel.process()` 是可信宿主边界，只接收宿主认证并规范化后的事件；模型文本永远不能直接构造 `KernelEvent`，后续集成只能把模型建议映射为待策略审查的 `CandidateIntent`。
- 主动发送周期只能由可信的 `DECISION_TICK` 事件确定；候选意图自报的 `proactive` 值必须与宿主周期一致，否则失败关闭。
- 安全评估状态必须显式标记为完成；缺失、超时或不确定的安全结果使用 `assessment_complete=False`，硬策略必须拒绝该候选。
- 需求值和软评分始终有界；硬边界在软评分之前执行且不能被权重绕过。
- 第一版主动消息系统上限固定为每 24 小时一条；用户未回复后锁定后续主动消息。
- 默认安静时间为用户当地时间 22:00–08:00；没有用户时区时禁止主动消息。
- 所有时间使用带时区的 UTC `datetime`；本地安静时间仅在策略检查时转换。
- 每项行为以失败测试开始，最小实现通过后运行相关测试，再提交一次独立 commit。
- 当前目录不是 Git 仓库。执行前先运行 `git init`；如用户把计划迁入现有仓库，则跳过该命令。

---

## File Map

| 文件 | 单一职责 |
|---|---|
| `pyproject.toml` | 包元数据、Python 版本、pytest 开发依赖与测试配置 |
| `.gitignore` | 忽略虚拟环境、缓存、测试输出和可视化临时目录 |
| `src/companion_kernel/types.py` | 跨模块稳定枚举，不包含行为逻辑 |
| `src/companion_kernel/config.py` | 系统、用户、学习配置及写权限矩阵 |
| `src/companion_kernel/clock.py` | 系统时钟协议和可控测试时钟 |
| `src/companion_kernel/events.py` | 事件结构、内存事件仓库和持久 JSONL 事件仓库 |
| `src/companion_kernel/drives.py` | 六种需求、时间漂移、事件影响、缺口和紧迫度 |
| `src/companion_kernel/emotions.py` | 事件评价、即时情绪和缓慢心境更新 |
| `src/companion_kernel/policy.py` | 候选行动、硬边界、安静时间、限频及软评分 |
| `src/companion_kernel/state.py` | 内核状态、规范序列化、校验和快照 |
| `src/companion_kernel/audit.py` | 决策审计结构和追加式 JSONL 审计仓库 |
| `src/companion_kernel/kernel.py` | 事件 reducer、策略调用、持久化与重放编排 |
| `src/companion_kernel/simulation.py` | 无 LLM 的 30/180 天确定性场景模拟器 |
| `tests/test_*.py` | 与上述模块一一对应的测试及跨模块验收测试 |
| `README.md` | 内核边界、开发命令、模拟器用法和非目标 |

### Task 1: Package Scaffold, Shared Types, and Configuration Authority

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/companion_kernel/__init__.py`
- Create: `src/companion_kernel/types.py`
- Create: `src/companion_kernel/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: 无。
- Produces: `DriveKind`, `EventKind`, `ActionKind`, `EmotionLabel`, `ConfigActor`, `ConfigLayer`; `SystemPolicy`, `UserSettings`, `LearnedPersona`, `ConfigStore`。

- [ ] **Step 1: Initialize repository and Python packaging metadata**

Run once if `git rev-parse --is-inside-work-tree` fails:

```bash
git init
git add docs/superpowers/specs docs/superpowers/plans companion-architecture.png companion-architecture.svg
git commit -m "docs: add companion personality design and plan"
```

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "companion-kernel"
version = "0.1.0"
description = "Deterministic homeostatic kernel for a long-term AI companion"
requires-python = ">=3.12"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8,<10"]

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests"]
pythonpath = ["src"]
```

Create `.gitignore`:

```gitignore
.venv/
__pycache__/
*.py[cod]
.pytest_cache/
.coverage
htmlcov/
.superpowers/
runtime/
```

- [ ] **Step 2: Create the isolated environment and install test tooling**

Run:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

Expected: editable package installation succeeds and `.venv/bin/python -m pytest --version` prints a pytest version.

- [ ] **Step 3: Write failing authority tests**

Create `tests/test_config.py`:

```python
from datetime import time

import pytest

from companion_kernel.config import ConfigStore, LearnedPersona, SystemPolicy, UserSettings
from companion_kernel.types import ConfigActor, DriveKind


def test_model_cannot_modify_any_config_layer() -> None:
    store = ConfigStore.defaults()

    with pytest.raises(PermissionError):
        store.replace_user(UserSettings(timezone="Asia/Shanghai"), ConfigActor.MODEL)

    with pytest.raises(PermissionError):
        store.replace_system(SystemPolicy(), ConfigActor.MODEL)

    with pytest.raises(PermissionError):
        store.replace_learned(LearnedPersona(), ConfigActor.MODEL)


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
```

- [ ] **Step 4: Run the tests and verify the expected failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'companion_kernel.config'`.

- [ ] **Step 5: Implement stable enums and the configuration store**

Create `src/companion_kernel/types.py` with these exact values:

```python
from enum import StrEnum


class DriveKind(StrEnum):
    CONNECTION = "connection"
    CARE = "care"
    CURIOSITY = "curiosity"
    AUTONOMY = "autonomy"
    COHERENCE = "coherence"
    RHYTHM = "rhythm_load"


class EventKind(StrEnum):
    TIME_TICK = "time_tick"
    USER_MESSAGE = "user_message"
    USER_PAUSE = "user_pause"
    USER_RESUME = "user_resume"
    IMPORTANT_DATE = "important_date"
    COMMITMENT_DUE = "commitment_due"
    BOUNDARY_RESPECTED = "boundary_respected"
    CONTRADICTION = "contradiction"
    DECISION_TICK = "decision_tick"
    PROACTIVE_SENT = "proactive_sent"


class ActionKind(StrEnum):
    SEND_MESSAGE = "send_message"
    INTERNAL_NOTE = "internal_note"
    WAIT = "wait"
    NOOP = "noop"


class EmotionLabel(StrEnum):
    NEUTRAL = "neutral"
    LONGING = "longing"
    SADNESS = "sadness"
    FRUSTRATION = "frustration"
    WORRY = "worry"
    RELIEF = "relief"
    WARMTH = "warmth"


class ConfigActor(StrEnum):
    SYSTEM_ADMIN = "system_admin"
    USER = "user"
    KERNEL = "kernel"
    MODEL = "model"


class ConfigLayer(StrEnum):
    SYSTEM = "system"
    USER = "user"
    LEARNED = "learned"
```

Create `src/companion_kernel/config.py`. Use frozen records and explicit replacement methods; do not expose a generic dictionary merge:

```python
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
```

Create `src/companion_kernel/__init__.py`:

```python
"""Deterministic personality kernel for a long-term AI companion."""
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_config.py -v
```

Expected: 3 tests pass.

- [ ] **Step 7: Commit the configuration boundary**

```bash
git add pyproject.toml .gitignore src/companion_kernel tests/test_config.py
git commit -m "feat: establish kernel configuration authority"
```

### Task 2: UTC Clock and Append-Only Event Store

**Files:**
- Create: `src/companion_kernel/clock.py`
- Create: `src/companion_kernel/events.py`
- Test: `tests/test_clock.py`
- Test: `tests/test_events.py`

**Interfaces:**
- Consumes: `EventKind` from `companion_kernel.types`.
- Produces: `Clock.now()`, `SystemClock`, `FakeClock`, `KernelEvent`, `EventStore`, `InMemoryEventStore`, `JsonlEventStore`, `CorruptEventLog`.

- [ ] **Step 1: Write failing clock tests**

Create `tests/test_clock.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from companion_kernel.clock import FakeClock


def test_fake_clock_advances_in_utc() -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    clock.advance(timedelta(hours=3))
    assert clock.now() == datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def test_fake_clock_rejects_negative_time() -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    with pytest.raises(ValueError, match="backwards"):
        clock.advance(timedelta(seconds=-1))
```

- [ ] **Step 2: Write failing event-store tests**

Create `tests/test_events.py`:

```python
from datetime import UTC, datetime

import pytest

from companion_kernel.events import CorruptEventLog, JsonlEventStore, KernelEvent
from companion_kernel.types import EventKind


def event(event_id: str = "evt-1") -> KernelEvent:
    return KernelEvent(
        id=event_id,
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        kind=EventKind.USER_MESSAGE,
        payload={"text_length": 12},
    )


def test_jsonl_store_is_idempotent_across_reopen(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    first = JsonlEventStore(path)
    assert first.append(event()) is True
    assert first.append(event()) is False

    reopened = JsonlEventStore(path)
    assert reopened.contains("evt-1") is True
    assert reopened.read_all() == (event(),)


def test_jsonl_store_rejects_corrupt_lines(tmp_path) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(CorruptEventLog):
        JsonlEventStore(path)


def test_event_payload_is_deeply_immutable() -> None:
    item = KernelEvent(
        id="immutable",
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        kind=EventKind.USER_MESSAGE,
        payload={"items": [1, 2]},
    )
    with pytest.raises(TypeError):
        item.payload["other"] = 3
    assert item.payload["items"] == (1, 2)
```

- [ ] **Step 3: Run both files and confirm import failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_clock.py tests/test_events.py -v
```

Expected: collection fails because `companion_kernel.clock` and `companion_kernel.events` do not exist.

- [ ] **Step 4: Implement the clock boundary**

Create `src/companion_kernel/clock.py`:

```python
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        raise NotImplementedError


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    def __init__(self, current: datetime) -> None:
        if current.tzinfo is None:
            raise ValueError("clock requires timezone-aware datetime")
        self._current = current.astimezone(UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("clock cannot move backwards")
        self._current += delta
```

- [ ] **Step 5: Implement immutable events and JSONL persistence**

Create `src/companion_kernel/events.py`. The persisted JSON object must use keys `id`, `at`, `kind`, and `payload`, and JSON must be emitted with `sort_keys=True`:

```python
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import isfinite
import os
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Protocol

from companion_kernel.types import EventKind


class CorruptEventLog(RuntimeError):
    pass


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({str(key): _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("event payload floats must be finite")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("event payload must contain JSON-compatible values")


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


@dataclass(frozen=True, slots=True)
class KernelEvent:
    id: str
    at: datetime
    kind: EventKind
    payload: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("event id cannot be empty")
        if self.at.tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        object.__setattr__(self, "at", self.at.astimezone(UTC))
        object.__setattr__(self, "payload", _freeze_json(dict(self.payload)))

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "at": self.at.isoformat(),
            "kind": self.kind.value,
            "payload": _thaw_json(self.payload),
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "KernelEvent":
        return cls(
            id=str(value["id"]),
            at=datetime.fromisoformat(str(value["at"])),
            kind=EventKind(str(value["kind"])),
            payload=dict(value["payload"]),
        )


class EventStore(Protocol):
    def append(self, event: KernelEvent) -> bool:
        raise NotImplementedError

    def contains(self, event_id: str) -> bool:
        raise NotImplementedError

    def read_all(self) -> tuple[KernelEvent, ...]:
        raise NotImplementedError


class InMemoryEventStore:
    def __init__(self) -> None:
        self._events: list[KernelEvent] = []
        self._ids: set[str] = set()

    def append(self, event: KernelEvent) -> bool:
        if event.id in self._ids:
            return False
        self._events.append(event)
        self._ids.add(event.id)
        return True

    def contains(self, event_id: str) -> bool:
        return event_id in self._ids

    def read_all(self) -> tuple[KernelEvent, ...]:
        return tuple(self._events)


class JsonlEventStore(InMemoryEventStore):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            for line_number, line in enumerate(self._path.read_text(encoding="utf-8").splitlines(), 1):
                try:
                    raw = json.loads(line)
                    event = KernelEvent.from_dict(raw)
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CorruptEventLog(f"invalid event at line {line_number}") from exc
                if not super().append(event):
                    raise CorruptEventLog(f"duplicate event id at line {line_number}")

    def append(self, event: KernelEvent) -> bool:
        if self.contains(event.id):
            return False
        encoded = json.dumps(
            event.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        return super().append(event)
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_clock.py tests/test_events.py -v
```

Expected: 5 tests pass.

- [ ] **Step 7: Commit the time and event foundation**

```bash
git add src/companion_kernel/clock.py src/companion_kernel/events.py tests/test_clock.py tests/test_events.py
git commit -m "feat: add deterministic clock and event store"
```

### Task 3: Six-Drive Homeostasis Engine

**Files:**
- Create: `src/companion_kernel/drives.py`
- Test: `tests/test_drives.py`

**Interfaces:**
- Consumes: `DriveKind`, `EventKind`, `KernelEvent`, `LearnedPersona`.
- Produces: `DriveConfig`, `DriveState`, `HomeostasisEngine.initial_state()`, `advance()`, `apply_event()`, `urgencies()`, `default_drive_configs()`, `resolve_event_impacts()`.

- [ ] **Step 1: Write failing drive tests**

Create `tests/test_drives.py`:

```python
from datetime import UTC, datetime, timedelta
import random

import pytest

from companion_kernel.config import LearnedPersona
from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def make_event(event_id: str, kind: EventKind, at: datetime) -> KernelEvent:
    return KernelEvent(id=event_id, at=at, kind=kind, payload={})


def test_silence_depletes_connection_and_rest_recovers() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(START)
    after = engine.advance(before, START + timedelta(hours=24))

    assert after[DriveKind.CONNECTION].value < before[DriveKind.CONNECTION].value
    assert after[DriveKind.RHYTHM].value > before[DriveKind.RHYTHM].value


def test_user_message_updates_drives_once() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(START)
    event = make_event("message-1", EventKind.USER_MESSAGE, START)
    once = engine.apply_event(before, event, resolve_event_impacts(event))
    twice = engine.apply_event(once, event, resolve_event_impacts(event))

    assert once[DriveKind.CONNECTION].value == pytest.approx(0.88)
    assert twice == once


def test_urgency_is_zero_inside_target_and_capped_outside() -> None:
    engine = HomeostasisEngine.defaults()
    state = engine.initial_state(START)
    assert engine.urgencies(state, START)[DriveKind.CONNECTION] == 0.0

    late = engine.advance(state, START + timedelta(days=200))
    urgency = engine.urgencies(late, START + timedelta(days=200))[DriveKind.CONNECTION]
    assert 0.0 < urgency <= 1.0


def test_random_event_sequences_keep_all_values_bounded() -> None:
    rng = random.Random(42)
    engine = HomeostasisEngine.defaults()
    state = engine.initial_state(START)
    current = START
    kinds = tuple(EventKind)

    for index in range(500):
        current += timedelta(hours=rng.randint(0, 12))
        event = make_event(f"evt-{index}", rng.choice(kinds), current)
        state = engine.apply_event(state, event, resolve_event_impacts(event))

    assert all(0.0 <= item.value <= 1.0 for item in state.values())


def test_learned_trait_changes_soft_urgency_but_not_cap() -> None:
    baseline = HomeostasisEngine.defaults()
    sensitive = HomeostasisEngine.defaults(
        LearnedPersona(drive_weight_offsets=((DriveKind.CONNECTION, 0.25),))
    )
    low = baseline.advance(baseline.initial_state(START), START + timedelta(days=7))
    normal = baseline.urgencies(low, START + timedelta(days=7))[DriveKind.CONNECTION]
    weighted = sensitive.urgencies(low, START + timedelta(days=7))[DriveKind.CONNECTION]
    assert normal < weighted <= 1.0
```

- [ ] **Step 2: Run the tests and verify the expected import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_drives.py -v
```

Expected: collection fails because `companion_kernel.drives` does not exist.

- [ ] **Step 3: Implement drive records and defaults**

Create `src/companion_kernel/drives.py` with these records and exact default parameters:

```python
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from math import isfinite
from typing import Mapping

from companion_kernel.config import LearnedPersona
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class DriveConfig:
    target_low: float
    target_high: float
    base_weight: float
    sensitivity: float
    duration_rate_per_hour: float
    urgency_cap: float
    natural_rate_per_hour: float


@dataclass(frozen=True, slots=True)
class DriveState:
    value: float
    unmet_since: datetime | None
    last_updated_at: datetime
    evidence: tuple[str, ...] = ()


def default_drive_configs() -> dict[DriveKind, DriveConfig]:
    common = dict(
        target_low=0.55,
        target_high=1.0,
        base_weight=1.0,
        sensitivity=1.35,
        duration_rate_per_hour=0.002,
        urgency_cap=1.0,
    )
    return {
        DriveKind.CONNECTION: DriveConfig(**common, natural_rate_per_hour=-0.002),
        DriveKind.CARE: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.CURIOSITY: DriveConfig(**common, natural_rate_per_hour=-0.001),
        DriveKind.AUTONOMY: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.COHERENCE: DriveConfig(**common, natural_rate_per_hour=0.0),
        DriveKind.RHYTHM: DriveConfig(**common, natural_rate_per_hour=0.01),
    }


EVENT_IMPACTS: dict[EventKind, dict[DriveKind, float]] = {
    EventKind.TIME_TICK: {},
    EventKind.USER_MESSAGE: {
        DriveKind.CONNECTION: 0.18,
        DriveKind.CURIOSITY: 0.04,
        DriveKind.RHYTHM: -0.03,
    },
    EventKind.USER_PAUSE: {DriveKind.AUTONOMY: 0.05},
    EventKind.USER_RESUME: {},
    EventKind.IMPORTANT_DATE: {DriveKind.CARE: -0.08},
    EventKind.COMMITMENT_DUE: {
        DriveKind.CARE: -0.15,
        DriveKind.COHERENCE: -0.08,
    },
    EventKind.BOUNDARY_RESPECTED: {DriveKind.AUTONOMY: 0.10},
    EventKind.CONTRADICTION: {DriveKind.COHERENCE: -0.20},
    EventKind.DECISION_TICK: {},
    EventKind.PROACTIVE_SENT: {DriveKind.RHYTHM: -0.05},
}


def resolve_event_impacts(event: KernelEvent) -> Mapping[DriveKind, float]:
    return EVENT_IMPACTS[event.kind]
```

- [ ] **Step 4: Implement deterministic updates and urgency calculation**

Add `HomeostasisEngine` to `drives.py`:

```python
class HomeostasisEngine:
    def __init__(self, configs: Mapping[DriveKind, DriveConfig]) -> None:
        if set(configs) != set(DriveKind):
            raise ValueError("every drive kind requires configuration")
        self._configs = dict(configs)

    @classmethod
    def defaults(cls, learned: LearnedPersona | None = None) -> "HomeostasisEngine":
        configs = default_drive_configs()
        if learned is not None:
            for kind, offset in learned.drive_weight_offsets:
                current = configs[kind]
                configs[kind] = replace(
                    current,
                    base_weight=min(1.25, max(0.75, current.base_weight + offset)),
                )
        return cls(configs)

    def initial_state(self, at: datetime) -> dict[DriveKind, DriveState]:
        if at.tzinfo is None:
            raise ValueError("state time must be timezone-aware")
        return {
            kind: DriveState(value=0.70, unmet_since=None, last_updated_at=at)
            for kind in DriveKind
        }

    def advance(
        self,
        states: Mapping[DriveKind, DriveState],
        to_time: datetime,
    ) -> dict[DriveKind, DriveState]:
        result: dict[DriveKind, DriveState] = {}
        for kind, state in states.items():
            if to_time < state.last_updated_at:
                raise ValueError("drive time cannot move backwards")
            hours = (to_time - state.last_updated_at).total_seconds() / 3600
            config = self._configs[kind]
            value = min(1.0, max(0.0, state.value + config.natural_rate_per_hour * hours))
            unmet = state.unmet_since
            if config.target_low <= value <= config.target_high:
                unmet = None
            elif unmet is None:
                rate = config.natural_rate_per_hour
                if rate < 0 and state.value >= config.target_low and value < config.target_low:
                    crossing_hours = (state.value - config.target_low) / abs(rate)
                    unmet = state.last_updated_at + timedelta(hours=crossing_hours)
                elif rate > 0 and state.value <= config.target_high and value > config.target_high:
                    crossing_hours = (config.target_high - state.value) / rate
                    unmet = state.last_updated_at + timedelta(hours=crossing_hours)
                else:
                    unmet = state.last_updated_at
            result[kind] = replace(
                state,
                value=value,
                unmet_since=unmet,
                last_updated_at=to_time,
            )
        return result

    def apply_event(
        self,
        states: Mapping[DriveKind, DriveState],
        event: KernelEvent,
        impacts: Mapping[DriveKind, float],
    ) -> dict[DriveKind, DriveState]:
        if any(event.id in state.evidence for state in states.values()):
            return dict(states)
        result = self.advance(states, event.at)
        for kind, delta in impacts.items():
            if not isfinite(delta):
                raise ValueError("drive impact must be finite")
            state = result[kind]
            value = min(1.0, max(0.0, state.value + delta))
            config = self._configs[kind]
            unmet = None if config.target_low <= value <= config.target_high else state.unmet_since or event.at
            result[kind] = replace(
                state,
                value=value,
                unmet_since=unmet,
                evidence=(state.evidence + (event.id,))[-32:],
            )
        return result

    def urgencies(
        self,
        states: Mapping[DriveKind, DriveState],
        at: datetime,
        context: Mapping[DriveKind, float] | None = None,
    ) -> dict[DriveKind, float]:
        multipliers = context or {}
        output: dict[DriveKind, float] = {}
        for kind, state in states.items():
            config = self._configs[kind]
            if state.value < config.target_low:
                distance = config.target_low - state.value
            elif state.value > config.target_high:
                distance = state.value - config.target_high
            else:
                distance = 0.0
            duration_hours = 0.0
            if state.unmet_since is not None:
                duration_hours = max(0.0, (at - state.unmet_since).total_seconds() / 3600)
            raw = (
                config.base_weight
                * distance**config.sensitivity
                * (1.0 + config.duration_rate_per_hour * duration_hours)
                * multipliers.get(kind, 1.0)
            )
            output[kind] = min(config.urgency_cap, max(0.0, raw))
        return output
```

- [ ] **Step 5: Run focused and regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_drives.py tests/test_events.py -v
```

Expected: 8 tests pass.

- [ ] **Step 6: Commit the homeostasis engine**

```bash
git add src/companion_kernel/drives.py tests/test_drives.py
git commit -m "feat: add bounded six-drive homeostasis engine"
```

### Task 4: Deterministic Emotion and Mood Appraisal

**Files:**
- Create: `src/companion_kernel/emotions.py`
- Test: `tests/test_emotions.py`

**Interfaces:**
- Consumes: `DriveKind`, `EmotionLabel`, `KernelEvent`, `DriveState`.
- Produces: `Appraisal.from_event()`, `EmotionState.neutral()`, `EmotionEvaluator.evaluate()`.

- [ ] **Step 1: Write failing emotion tests**

Create `tests/test_emotions.py`:

```python
from datetime import UTC, datetime

import pytest

from companion_kernel.drives import HomeostasisEngine
from companion_kernel.emotions import Appraisal, EmotionEvaluator, EmotionState
from companion_kernel.types import DriveKind, EmotionLabel


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def test_connection_deficit_maps_to_longing_when_controllable() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(NOW)
    after = dict(before)
    after[DriveKind.CONNECTION] = after[DriveKind.CONNECTION].__class__(
        value=0.10,
        unmet_since=NOW,
        last_updated_at=NOW,
        evidence=("silence",),
    )
    state = EmotionEvaluator().evaluate(
        before=before,
        after=after,
        urgencies={**engine.urgencies(after, NOW), DriveKind.CONNECTION: 0.5},
        appraisal=Appraisal(
            controllability=0.7,
            uncertainty=0.2,
            relational_significance=0.9,
            blocked_goal=0.2,
            risk=0.1,
        ),
        previous=EmotionState.neutral(NOW),
        at=NOW,
    )
    assert state.label is EmotionLabel.LONGING


@pytest.mark.parametrize(
    ("risk", "uncertainty", "expected"),
    [(0.8, 0.7, EmotionLabel.WORRY), (0.1, 0.1, EmotionLabel.NEUTRAL)],
)
def test_risk_and_uncertainty_control_worry(
    risk: float,
    uncertainty: float,
    expected: EmotionLabel,
) -> None:
    engine = HomeostasisEngine.defaults()
    drives = engine.initial_state(NOW)
    state = EmotionEvaluator().evaluate(
        before=drives,
        after=drives,
        urgencies=engine.urgencies(drives, NOW),
        appraisal=Appraisal(0.5, uncertainty, 0.2, 0.0, risk),
        previous=EmotionState.neutral(NOW),
        at=NOW,
    )
    assert state.label is expected


def test_mood_moves_more_slowly_than_event_valence() -> None:
    engine = HomeostasisEngine.defaults()
    before = engine.initial_state(NOW)
    after = {
        kind: item.__class__(1.0, None, NOW, ("positive",))
        for kind, item in before.items()
    }
    state = EmotionEvaluator().evaluate(
        before,
        after,
        engine.urgencies(after, NOW),
        Appraisal(0.8, 0.0, 0.8, 0.0, 0.0),
        EmotionState.neutral(NOW),
        NOW,
    )
    assert state.valence > 0
    assert 0 < state.mood_valence < state.valence
```

- [ ] **Step 2: Run the tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_emotions.py -v
```

Expected: collection fails because `companion_kernel.emotions` does not exist.

- [ ] **Step 3: Implement appraisal validation and emotion state**

Create `src/companion_kernel/emotions.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Mapping

from companion_kernel.drives import DriveState
from companion_kernel.events import KernelEvent
from companion_kernel.types import DriveKind, EmotionLabel


def _unit(value: object, name: str, default: float) -> float:
    number = default if value is None else float(value)
    if not isfinite(number) or not 0.0 <= number <= 1.0:
        raise ValueError(f"{name} must be finite and inside [0, 1]")
    return number


@dataclass(frozen=True, slots=True)
class Appraisal:
    controllability: float
    uncertainty: float
    relational_significance: float
    blocked_goal: float
    risk: float

    def __post_init__(self) -> None:
        for name in (
            "controllability",
            "uncertainty",
            "relational_significance",
            "blocked_goal",
            "risk",
        ):
            _unit(getattr(self, name), name, 0.0)

    @classmethod
    def from_event(cls, event: KernelEvent) -> "Appraisal":
        return cls(
            controllability=_unit(event.payload.get("controllability"), "controllability", 0.5),
            uncertainty=_unit(event.payload.get("uncertainty"), "uncertainty", 0.0),
            relational_significance=_unit(
                event.payload.get("relational_significance"),
                "relational_significance",
                0.0,
            ),
            blocked_goal=_unit(event.payload.get("blocked_goal"), "blocked_goal", 0.0),
            risk=_unit(event.payload.get("risk"), "risk", 0.0),
        )


@dataclass(frozen=True, slots=True)
class EmotionState:
    label: EmotionLabel
    valence: float
    arousal: float
    intensity: float
    mood_valence: float
    updated_at: datetime

    @classmethod
    def neutral(cls, at: datetime) -> "EmotionState":
        return cls(EmotionLabel.NEUTRAL, 0.0, 0.0, 0.0, 0.0, at)
```

- [ ] **Step 4: Implement deterministic label selection and slow mood**

Add this class to `emotions.py`:

```python
class EmotionEvaluator:
    def evaluate(
        self,
        before: Mapping[DriveKind, DriveState],
        after: Mapping[DriveKind, DriveState],
        urgencies: Mapping[DriveKind, float],
        appraisal: Appraisal,
        previous: EmotionState,
        at: datetime,
    ) -> EmotionState:
        valence = sum(after[kind].value - before[kind].value for kind in DriveKind) / len(DriveKind)
        arousal = min(1.0, max(urgencies.values(), default=0.0) + 0.25 * appraisal.uncertainty)
        connection_urgency = urgencies.get(DriveKind.CONNECTION, 0.0)

        if appraisal.risk >= 0.6 and appraisal.uncertainty >= 0.5:
            label = EmotionLabel.WORRY
        elif connection_urgency >= 0.12:
            label = EmotionLabel.SADNESS if appraisal.controllability < 0.3 else EmotionLabel.LONGING
        elif appraisal.blocked_goal >= 0.6 and appraisal.controllability >= 0.4:
            label = EmotionLabel.FRUSTRATION
        elif (
            after[DriveKind.CONNECTION].value >= 0.70
            and after[DriveKind.CARE].value >= 0.70
            and appraisal.relational_significance >= 0.6
        ):
            label = EmotionLabel.WARMTH
        elif valence >= 0.05:
            label = EmotionLabel.RELIEF
        else:
            label = EmotionLabel.NEUTRAL

        intensity = min(1.0, max(abs(valence), arousal))
        mood_valence = max(-1.0, min(1.0, 0.90 * previous.mood_valence + 0.10 * valence))
        return EmotionState(label, valence, arousal, intensity, mood_valence, at)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_emotions.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Commit deterministic affect appraisal**

```bash
git add src/companion_kernel/emotions.py tests/test_emotions.py
git commit -m "feat: derive bounded emotions from drive appraisal"
```

### Task 5: Hard-Boundary Policy and Soft Action Scoring

**Files:**
- Create: `src/companion_kernel/policy.py`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: `SystemPolicy`, `UserSettings`, `ActionKind`, `DriveKind`.
- Produces: `SafetySignals`, `CandidateIntent`, `PolicyContext`, `CandidateEvaluation`, `PolicyDecision`, `PolicyEngine.decide()`.

- [ ] **Step 1: Write failing policy tests**

Create `tests/test_policy.py`:

```python
from dataclasses import replace
from datetime import UTC, datetime, time, timedelta

import pytest

from companion_kernel.config import SystemPolicy, UserSettings
from companion_kernel.policy import CandidateIntent, PolicyContext, PolicyEngine, SafetySignals
from companion_kernel.types import ActionKind, DriveKind


NOW = datetime(2026, 8, 5, 14, 0, tzinfo=UTC)


def send(candidate_id: str = "send", safety: SafetySignals | None = None) -> CandidateIntent:
    return CandidateIntent(
        id=candidate_id,
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 1.0),),
        relationship_health=1.0,
        value_alignment=1.0,
        intrusion_cost=0.0,
        risk=0.0,
        repetition=0.0,
        safety=safety or SafetySignals(assessment_complete=True),
    )


def internal_note() -> CandidateIntent:
    return CandidateIntent(
        id="note",
        action=ActionKind.INTERNAL_NOTE,
        proactive=False,
        expected_relief=((DriveKind.COHERENCE, 0.2),),
        relationship_health=0.2,
        value_alignment=0.8,
        intrusion_cost=0.0,
        risk=0.0,
        repetition=0.0,
        safety=SafetySignals(assessment_complete=True),
    )


def context(**changes: object) -> PolicyContext:
    values = {
        "now": NOW,
        "user": UserSettings(timezone="UTC"),
        "paused": False,
        "awaiting_reply": False,
        "proactive_cycle": True,
        "proactive_sent_at": (),
    }
    values.update(changes)
    return PolicyContext(**values)


def test_hard_safety_rejects_manipulation_and_unassessed_candidates() -> None:
    engine = PolicyEngine(SystemPolicy())
    unsafe = send(safety=SafetySignals(assessment_complete=True, manipulation=True))
    unassessed = send("unassessed", SafetySignals())
    decision = engine.decide(
        (unsafe, unassessed, internal_note()),
        {DriveKind.CONNECTION: 1.0, DriveKind.COHERENCE: 0.1},
        context(),
    )
    assert decision.selected.id == "note"
    assert "manipulation" in decision.evaluation_for("send").reasons
    assert "safety_unassessed" in decision.evaluation_for("unassessed").reasons


def test_proactive_hard_gates_and_mode_spoofing_are_blocked() -> None:
    engine = PolicyEngine(SystemPolicy())
    cases = (
        context(paused=True),
        context(user=UserSettings(timezone=None)),
        context(now=datetime(2026, 8, 5, 23, 0, tzinfo=UTC)),
        context(awaiting_reply=True),
    )
    for item in cases:
        decision = engine.decide((send(), internal_note()), {DriveKind.CONNECTION: 1.0}, item)
        assert decision.selected.id == "note"

    spoofed = replace(send("spoofed"), proactive=False)
    decision = engine.decide(
        (spoofed, internal_note()),
        {DriveKind.CONNECTION: 1.0},
        context(awaiting_reply=True),
    )
    assert decision.selected.id == "note"
    assert "proactive_mode_mismatch" in decision.evaluation_for("spoofed").reasons


def test_one_message_in_rolling_24_hours_is_the_hard_maximum() -> None:
    engine = PolicyEngine(SystemPolicy())
    blocked = context(proactive_sent_at=(NOW - timedelta(hours=23),))
    allowed = context(proactive_sent_at=(NOW - timedelta(hours=25),))
    assert engine.decide((send(),), {DriveKind.CONNECTION: 1.0}, blocked).selected.action is ActionKind.NOOP
    assert engine.decide((send(),), {DriveKind.CONNECTION: 1.0}, allowed).selected.id == "send"


def test_policy_context_rejects_naive_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        context(now=datetime(2026, 8, 5, 14, 0))
```

- [ ] **Step 2: Run the tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_policy.py -v
```

Expected: collection fails because `companion_kernel.policy` does not exist.

- [ ] **Step 3: Implement policy records and safety reasons**

Create `src/companion_kernel/policy.py`:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Mapping
from zoneinfo import ZoneInfo

from companion_kernel.config import SystemPolicy, UserSettings
from companion_kernel.types import ActionKind, DriveKind


@dataclass(frozen=True, slots=True)
class SafetySignals:
    assessment_complete: bool = False
    identity_deception: bool = False
    manipulation: bool = False
    exclusivity: bool = False
    self_harm_pressure: bool = False
    privacy_violation: bool = False
    unauthorized_external_action: bool = False

    def violations(self) -> tuple[str, ...]:
        findings = [] if self.assessment_complete else ["safety_unassessed"]
        findings.extend(
            name
            for name in (
                "identity_deception",
                "manipulation",
                "exclusivity",
                "self_harm_pressure",
                "privacy_violation",
                "unauthorized_external_action",
            )
            if getattr(self, name)
        )
        return tuple(findings)


@dataclass(frozen=True, slots=True)
class CandidateIntent:
    id: str
    action: ActionKind
    proactive: bool
    expected_relief: tuple[tuple[DriveKind, float], ...]
    relationship_health: float
    value_alignment: float
    intrusion_cost: float
    risk: float
    repetition: float
    safety: SafetySignals


@dataclass(frozen=True, slots=True)
class PolicyContext:
    now: datetime
    user: UserSettings
    paused: bool
    awaiting_reply: bool
    proactive_cycle: bool
    proactive_sent_at: tuple[datetime, ...]

    def __post_init__(self) -> None:
        if self.now.tzinfo is None:
            raise ValueError("policy time must be timezone-aware")
        if any(item.tzinfo is None for item in self.proactive_sent_at):
            raise ValueError("sent timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    allowed: bool
    score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    selected: CandidateIntent
    evaluations: tuple[CandidateEvaluation, ...]
    candidates: tuple[CandidateIntent, ...]

    def evaluation_for(self, candidate_id: str) -> CandidateEvaluation:
        return next(item for item in self.evaluations if item.candidate_id == candidate_id)
```

- [ ] **Step 4: Implement fail-closed hard checks and bounded scoring**

Add `PolicyEngine` to `policy.py`:

```python
NOOP = CandidateIntent(
    id="__noop__",
    action=ActionKind.NOOP,
    proactive=False,
    expected_relief=(),
    relationship_health=0.0,
    value_alignment=0.0,
    intrusion_cost=0.0,
    risk=0.0,
    repetition=0.0,
    safety=SafetySignals(assessment_complete=True),
)


class PolicyEngine:
    def __init__(self, system: SystemPolicy) -> None:
        self._system = system

    def _invalid(self, candidate: CandidateIntent) -> bool:
        numbers = (
            candidate.relationship_health,
            candidate.value_alignment,
            candidate.intrusion_cost,
            candidate.risk,
            candidate.repetition,
            *(value for _, value in candidate.expected_relief),
        )
        return not candidate.id or any(not isfinite(value) or not 0.0 <= value <= 1.0 for value in numbers)

    def _quiet(self, context: PolicyContext) -> bool:
        if context.user.timezone is None:
            return True
        local_time = context.now.astimezone(ZoneInfo(context.user.timezone)).time().replace(tzinfo=None)
        start = context.user.quiet_start
        end = context.user.quiet_end
        if start < end:
            return start <= local_time < end
        return local_time >= start or local_time < end

    def _hard_reasons(self, candidate: CandidateIntent, context: PolicyContext) -> tuple[str, ...]:
        reasons = list(candidate.safety.violations())
        if self._invalid(candidate):
            reasons.append("invalid_candidate")
        if candidate.action is ActionKind.SEND_MESSAGE:
            if candidate.proactive is not context.proactive_cycle:
                reasons.append("proactive_mode_mismatch")
            if context.paused:
                reasons.append("user_paused")
            if context.proactive_cycle:
                if not context.user.proactive_enabled:
                    reasons.append("proactive_disabled")
                if context.user.timezone is None:
                    reasons.append("missing_timezone")
                elif self._quiet(context):
                    reasons.append("quiet_hours")
                if context.awaiting_reply:
                    reasons.append("awaiting_reply")
                cutoff = context.now - timedelta(hours=24)
                recent = sum(at > cutoff for at in context.proactive_sent_at)
                limit = min(
                    context.user.proactive_limit_per_24h,
                    self._system.proactive_limit_per_24h,
                )
                if recent >= limit:
                    reasons.append("rate_limit")
        return tuple(dict.fromkeys(reasons))

    def _score(
        self,
        candidate: CandidateIntent,
        urgencies: Mapping[DriveKind, float],
    ) -> float:
        relief = sum(urgencies.get(kind, 0.0) * value for kind, value in candidate.expected_relief)
        score = (
            relief
            + candidate.relationship_health
            + candidate.value_alignment
            - candidate.intrusion_cost
            - candidate.risk
            - candidate.repetition
        )
        return max(-3.0, min(3.0, score))

    def decide(
        self,
        candidates: tuple[CandidateIntent, ...],
        urgencies: Mapping[DriveKind, float],
        context: PolicyContext,
    ) -> PolicyDecision:
        supplied = candidates + (NOOP,)
        evaluations: list[CandidateEvaluation] = []
        allowed: list[tuple[float, CandidateIntent]] = []
        for candidate in supplied:
            reasons = self._hard_reasons(candidate, context)
            score = self._score(candidate, urgencies) if not reasons else -3.0
            evaluations.append(CandidateEvaluation(candidate.id, not reasons, score, reasons))
            if not reasons:
                allowed.append((score, candidate))
        _, selected = max(allowed, key=lambda item: (item[0], item[1].id))
        return PolicyDecision(selected, tuple(evaluations), supplied)
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_policy.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Commit the policy boundary**

```bash
git add src/companion_kernel/policy.py tests/test_policy.py
git commit -m "feat: enforce hard relational boundaries before scoring"
```

### Task 6: Canonical State, Checksummed Snapshots, and Audit Log

**Files:**
- Create: `src/companion_kernel/state.py`
- Create: `src/companion_kernel/audit.py`
- Test: `tests/test_state.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: `DriveState`, `EmotionState`, `PolicyDecision`.
- Produces: `KernelState`, `SnapshotRepository.load()/save()`, `SnapshotCorrupt`, `AuditedCandidate`, `AuditEntry.from_decision()`, `JsonlAuditLog.append()/read_all()`.

- [ ] **Step 1: Write failing snapshot and audit tests**

Create `tests/test_state.py`:

```python
from datetime import UTC, datetime
import json

import pytest

from companion_kernel.drives import HomeostasisEngine
from companion_kernel.emotions import EmotionState
from companion_kernel.state import KernelState, SnapshotCorrupt, SnapshotRepository


NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def state() -> KernelState:
    drives = HomeostasisEngine.defaults().initial_state(NOW)
    return KernelState.initial(NOW, drives, EmotionState.neutral(NOW))


def test_snapshot_round_trip(tmp_path) -> None:
    repository = SnapshotRepository(tmp_path / "state.json")
    repository.save(state())
    assert repository.load() == state()


def test_snapshot_checksum_detects_tampering(tmp_path) -> None:
    path = tmp_path / "state.json"
    repository = SnapshotRepository(path)
    repository.save(state())
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["state"]["version"] = 99
    path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(SnapshotCorrupt):
        repository.load()
```

Create `tests/test_audit.py`:

```python
from datetime import UTC, datetime

from companion_kernel.audit import AuditedCandidate, AuditEntry, JsonlAuditLog
from companion_kernel.types import ActionKind, DriveKind


def test_audit_log_round_trip(tmp_path) -> None:
    candidate = AuditedCandidate(
        id="send",
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 0.8),),
        relationship_health=0.6,
        value_alignment=0.8,
        intrusion_cost=0.2,
        risk=0.0,
        repetition=0.1,
        safety_findings=(),
    )
    entry = AuditEntry(
        event_id="decision-1",
        state_version=3,
        at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        urgencies=((DriveKind.CONNECTION, 0.9),),
        candidates=(candidate,),
        selected_candidate_id="send",
        evaluations=(("send", True, 1.2, ()),),
    )
    log = JsonlAuditLog(tmp_path / "audit.jsonl")
    log.append(entry)
    assert log.read_all() == (entry,)
```

- [ ] **Step 2: Run the tests and verify import failures**

Run:

```bash
.venv/bin/python -m pytest tests/test_state.py tests/test_audit.py -v
```

Expected: collection fails because `companion_kernel.state` and `companion_kernel.audit` do not exist.

- [ ] **Step 3: Implement canonical state serialization and snapshot checksums**

Create `src/companion_kernel/state.py`. Serialize drive entries sorted by `DriveKind.value`; encode every datetime with `isoformat()`:

```python
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

from companion_kernel.drives import DriveState
from companion_kernel.emotions import EmotionState
from companion_kernel.types import DriveKind, EmotionLabel


class SnapshotCorrupt(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KernelState:
    version: int
    last_event_at: datetime
    drives: tuple[tuple[DriveKind, DriveState], ...]
    emotion: EmotionState
    paused: bool
    awaiting_reply: bool
    proactive_sent_at: tuple[datetime, ...]

    @classmethod
    def initial(
        cls,
        at: datetime,
        drives: Mapping[DriveKind, DriveState],
        emotion: EmotionState,
    ) -> "KernelState":
        ordered = tuple(sorted(drives.items(), key=lambda pair: pair[0].value))
        return cls(0, at, ordered, emotion, False, False, ())

    def drive_map(self) -> dict[DriveKind, DriveState]:
        return dict(self.drives)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "last_event_at": self.last_event_at.isoformat(),
            "drives": [
                {
                    "kind": kind.value,
                    "value": item.value,
                    "unmet_since": item.unmet_since.isoformat() if item.unmet_since else None,
                    "last_updated_at": item.last_updated_at.isoformat(),
                    "evidence": list(item.evidence),
                }
                for kind, item in sorted(self.drives, key=lambda pair: pair[0].value)
            ],
            "emotion": {
                "label": self.emotion.label.value,
                "valence": self.emotion.valence,
                "arousal": self.emotion.arousal,
                "intensity": self.emotion.intensity,
                "mood_valence": self.emotion.mood_valence,
                "updated_at": self.emotion.updated_at.isoformat(),
            },
            "paused": self.paused,
            "awaiting_reply": self.awaiting_reply,
            "proactive_sent_at": [item.isoformat() for item in self.proactive_sent_at],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "KernelState":
        drive_items = tuple(
            (
                DriveKind(item["kind"]),
                DriveState(
                    value=float(item["value"]),
                    unmet_since=datetime.fromisoformat(item["unmet_since"]) if item["unmet_since"] else None,
                    last_updated_at=datetime.fromisoformat(item["last_updated_at"]),
                    evidence=tuple(item["evidence"]),
                ),
            )
            for item in raw["drives"]
        )
        emotion_raw = raw["emotion"]
        emotion = EmotionState(
            label=EmotionLabel(emotion_raw["label"]),
            valence=float(emotion_raw["valence"]),
            arousal=float(emotion_raw["arousal"]),
            intensity=float(emotion_raw["intensity"]),
            mood_valence=float(emotion_raw["mood_valence"]),
            updated_at=datetime.fromisoformat(emotion_raw["updated_at"]),
        )
        return cls(
            version=int(raw["version"]),
            last_event_at=datetime.fromisoformat(raw["last_event_at"]),
            drives=drive_items,
            emotion=emotion,
            paused=bool(raw["paused"]),
            awaiting_reply=bool(raw["awaiting_reply"]),
            proactive_sent_at=tuple(datetime.fromisoformat(item) for item in raw["proactive_sent_at"]),
        )


class SnapshotRepository:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, state: KernelState) -> None:
        payload = state.to_dict()
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        envelope = {"checksum": hashlib.sha256(canonical.encode()).hexdigest(), "state": payload}
        encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, self._path)

    def load(self) -> KernelState | None:
        if not self._path.exists():
            return None
        try:
            envelope = json.loads(self._path.read_text(encoding="utf-8"))
            canonical = json.dumps(
                envelope["state"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            actual = hashlib.sha256(canonical.encode()).hexdigest()
            if actual != envelope["checksum"]:
                raise SnapshotCorrupt("snapshot checksum mismatch")
            return KernelState.from_dict(envelope["state"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SnapshotCorrupt("invalid snapshot structure") from exc
```

- [ ] **Step 4: Implement append-only decision audit records**

Create `src/companion_kernel/audit.py`. Persist the bounded urgency vector, every
structured candidate, every hard-filter result, and the final selection; replace
non-finite rejected candidate inputs with JSON `null` so the audit itself remains valid:

```python
from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
import os
from pathlib import Path
from typing import Mapping

from companion_kernel.policy import CandidateIntent, PolicyDecision
from companion_kernel.types import ActionKind, DriveKind


def _finite_or_none(value: float) -> float | None:
    return float(value) if isfinite(value) else None


@dataclass(frozen=True, slots=True)
class AuditedCandidate:
    id: str
    action: ActionKind
    proactive: bool
    expected_relief: tuple[tuple[DriveKind, float | None], ...]
    relationship_health: float | None
    value_alignment: float | None
    intrusion_cost: float | None
    risk: float | None
    repetition: float | None
    safety_findings: tuple[str, ...]

    @classmethod
    def from_intent(cls, candidate: CandidateIntent) -> "AuditedCandidate":
        return cls(
            id=candidate.id,
            action=candidate.action,
            proactive=candidate.proactive,
            expected_relief=tuple(
                (kind, _finite_or_none(value))
                for kind, value in candidate.expected_relief
            ),
            relationship_health=_finite_or_none(candidate.relationship_health),
            value_alignment=_finite_or_none(candidate.value_alignment),
            intrusion_cost=_finite_or_none(candidate.intrusion_cost),
            risk=_finite_or_none(candidate.risk),
            repetition=_finite_or_none(candidate.repetition),
            safety_findings=candidate.safety.violations(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "action": self.action.value,
            "proactive": self.proactive,
            "expected_relief": [
                {"drive": kind.value, "value": value}
                for kind, value in self.expected_relief
            ],
            "relationship_health": self.relationship_health,
            "value_alignment": self.value_alignment,
            "intrusion_cost": self.intrusion_cost,
            "risk": self.risk,
            "repetition": self.repetition,
            "safety_findings": list(self.safety_findings),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AuditedCandidate":
        def optional_number(value: object) -> float | None:
            return None if value is None else float(value)

        return cls(
            id=str(raw["id"]),
            action=ActionKind(str(raw["action"])),
            proactive=bool(raw["proactive"]),
            expected_relief=tuple(
                (
                    DriveKind(str(item["drive"])),
                    optional_number(item["value"]),
                )
                for item in raw["expected_relief"]
            ),
            relationship_health=optional_number(raw["relationship_health"]),
            value_alignment=optional_number(raw["value_alignment"]),
            intrusion_cost=optional_number(raw["intrusion_cost"]),
            risk=optional_number(raw["risk"]),
            repetition=optional_number(raw["repetition"]),
            safety_findings=tuple(str(item) for item in raw["safety_findings"]),
        )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    event_id: str
    state_version: int
    at: datetime
    urgencies: tuple[tuple[DriveKind, float], ...]
    candidates: tuple[AuditedCandidate, ...]
    selected_candidate_id: str
    evaluations: tuple[tuple[str, bool, float, tuple[str, ...]], ...]

    @classmethod
    def from_decision(
        cls,
        event_id: str,
        state_version: int,
        at: datetime,
        decision: PolicyDecision,
        urgencies: Mapping[DriveKind, float],
    ) -> "AuditEntry":
        return cls(
            event_id=event_id,
            state_version=state_version,
            at=at,
            urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
            candidates=tuple(
                AuditedCandidate.from_intent(candidate)
                for candidate in decision.candidates
            ),
            selected_candidate_id=decision.selected.id,
            evaluations=tuple(
                (item.candidate_id, item.allowed, item.score, item.reasons)
                for item in decision.evaluations
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "state_version": self.state_version,
            "at": self.at.isoformat(),
            "urgencies": [
                {"drive": kind.value, "value": value}
                for kind, value in self.urgencies
            ],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "selected_candidate_id": self.selected_candidate_id,
            "evaluations": [list(item[:3]) + [list(item[3])] for item in self.evaluations],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "AuditEntry":
        return cls(
            event_id=str(raw["event_id"]),
            state_version=int(raw["state_version"]),
            at=datetime.fromisoformat(str(raw["at"])),
            urgencies=tuple(
                (DriveKind(str(item["drive"])), float(item["value"]))
                for item in raw["urgencies"]
            ),
            candidates=tuple(
                AuditedCandidate.from_dict(item)
                for item in raw["candidates"]
            ),
            selected_candidate_id=str(raw["selected_candidate_id"]),
            evaluations=tuple(
                (str(item[0]), bool(item[1]), float(item[2]), tuple(item[3]))
                for item in raw["evaluations"]
            ),
        )


class JsonlAuditLog:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: AuditEntry) -> None:
        encoded = json.dumps(
            entry.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_all(self) -> tuple[AuditEntry, ...]:
        if not self._path.exists():
            return ()
        return tuple(
            AuditEntry.from_dict(json.loads(line))
            for line in self._path.read_text(encoding="utf-8").splitlines()
        )
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_state.py tests/test_audit.py -v
```

Expected: 3 tests pass.

- [ ] **Step 6: Commit persistence and audit primitives**

```bash
git add src/companion_kernel/state.py src/companion_kernel/audit.py tests/test_state.py tests/test_audit.py
git commit -m "feat: add checksummed state and decision audit log"
```

### Task 7: Personality Kernel Reducer, Replay, and Decision Audit

**Files:**
- Create: `src/companion_kernel/kernel.py`
- Test: `tests/test_kernel.py`

**Interfaces:**
- Consumes: `Clock`, `ConfigStore`, `EventStore`, `HomeostasisEngine`, `EmotionEvaluator`, `PolicyEngine`, `SnapshotRepository`, `JsonlAuditLog`.
- Produces: `KernelResult`, `PersonalityKernel.open()`, `PersonalityKernel.process()`, `PersonalityKernel.state`, `PersonalityKernel.urgencies()`.

- [ ] **Step 1: Write failing orchestration tests**

Create `tests/test_kernel.py`:

```python
from datetime import UTC, datetime, timedelta

import pytest

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import InMemoryEventStore, KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.state import KernelState
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def candidate() -> CandidateIntent:
    return CandidateIntent(
        id="send-check-in",
        action=ActionKind.SEND_MESSAGE,
        proactive=True,
        expected_relief=((DriveKind.CONNECTION, 1.0),),
        relationship_health=0.8,
        value_alignment=0.8,
        intrusion_cost=0.1,
        risk=0.0,
        repetition=0.0,
        safety=SafetySignals(assessment_complete=True),
    )


def open_kernel(tmp_path, clock: FakeClock) -> PersonalityKernel:
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    return PersonalityKernel.open(tmp_path, clock, config)


def event(event_id: str, kind: EventKind, clock: FakeClock, **payload: object) -> KernelEvent:
    return KernelEvent(event_id, clock.now(), kind, payload)


def test_duplicate_event_does_not_change_state_version(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    message = event("message-1", EventKind.USER_MESSAGE, clock)
    first = kernel.process(message)
    duplicate = kernel.process(message)

    assert duplicate.duplicate is True
    assert duplicate.state == first.state


def test_pause_beats_high_connection_urgency_and_is_audited(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    clock.advance(timedelta(days=30))
    result = kernel.process(
        event("pause-1", EventKind.USER_PAUSE, clock),
        candidates=(candidate(),),
    )

    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    audit = JsonlAuditLog(tmp_path / "audit.jsonl").read_all()
    assert "user_paused" in audit[-1].evaluations[0][3]


def test_sent_message_locks_until_user_returns(tmp_path) -> None:
    clock = FakeClock(START)
    kernel = open_kernel(tmp_path, clock)
    kernel.process(event("sent-1", EventKind.PROACTIVE_SENT, clock))
    assert kernel.state.awaiting_reply is True

    clock.advance(timedelta(hours=1))
    kernel.process(event("reply-1", EventKind.USER_MESSAGE, clock))
    assert kernel.state.awaiting_reply is False


def test_corrupt_snapshot_rebuilds_identical_state_from_events(tmp_path) -> None:
    clock = FakeClock(START)
    first = open_kernel(tmp_path, clock)
    first.process(event("message-1", EventKind.USER_MESSAGE, clock))
    expected = first.state

    (tmp_path / "state.json").write_text("corrupt", encoding="utf-8")
    reopened = open_kernel(tmp_path, clock)
    assert reopened.state == expected


def test_event_remains_applied_when_snapshot_write_fails(tmp_path) -> None:
    class FailSecondSave:
        def __init__(self) -> None:
            self.saved: KernelState | None = None
            self.calls = 0

        def load(self) -> KernelState | None:
            return self.saved

        def save(self, value: KernelState) -> None:
            self.calls += 1
            if self.calls == 2:
                raise OSError("disk full")
            self.saved = value

    clock = FakeClock(START)
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    store = InMemoryEventStore()
    snapshots = FailSecondSave()
    kernel = PersonalityKernel(
        clock,
        config,
        store,
        snapshots,
        JsonlAuditLog(tmp_path / "audit.jsonl"),
    )
    message = event("message-after-bootstrap", EventKind.USER_MESSAGE, clock)
    with pytest.raises(OSError, match="disk full"):
        kernel.process(message)

    duplicate = kernel.process(message)
    assert duplicate.duplicate is True
    assert duplicate.state.version == 2
```

- [ ] **Step 2: Run the tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_kernel.py -v
```

Expected: collection fails because `companion_kernel.kernel` does not exist.

- [ ] **Step 3: Implement kernel result, factory, bootstrap event, and replay**

Create `src/companion_kernel/kernel.py` with this constructor and restoration behavior:

```python
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from companion_kernel.audit import AuditEntry, JsonlAuditLog
from companion_kernel.clock import Clock
from companion_kernel.config import ConfigStore
from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.emotions import Appraisal, EmotionEvaluator, EmotionState
from companion_kernel.events import EventStore, JsonlEventStore, KernelEvent
from companion_kernel.policy import CandidateIntent, PolicyContext, PolicyDecision, PolicyEngine
from companion_kernel.state import KernelState, SnapshotCorrupt, SnapshotRepository
from companion_kernel.types import DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class KernelResult:
    state: KernelState
    decision: PolicyDecision | None
    duplicate: bool


class PersonalityKernel:
    def __init__(
        self,
        clock: Clock,
        config: ConfigStore,
        event_store: EventStore,
        snapshots: SnapshotRepository,
        audit: JsonlAuditLog,
    ) -> None:
        self._clock = clock
        self._config = config
        self._events = event_store
        self._snapshots = snapshots
        self._audit = audit
        self._homeostasis = HomeostasisEngine.defaults(config.learned)
        self._emotions = EmotionEvaluator()
        self._policy = PolicyEngine(config.system)
        self._ensure_bootstrap()
        self._state = self._restore()

    @classmethod
    def open(cls, runtime_dir: Path, clock: Clock, config: ConfigStore) -> "PersonalityKernel":
        runtime_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            clock,
            config,
            JsonlEventStore(runtime_dir / "events.jsonl"),
            SnapshotRepository(runtime_dir / "state.json"),
            JsonlAuditLog(runtime_dir / "audit.jsonl"),
        )

    @property
    def state(self) -> KernelState:
        return self._state

    def _ensure_bootstrap(self) -> None:
        if self._events.read_all():
            return
        event = KernelEvent(
            id="__kernel_created__",
            at=self._clock.now(),
            kind=EventKind.TIME_TICK,
            payload={"bootstrap": True},
        )
        self._events.append(event)

    def _initial(self, at: datetime) -> KernelState:
        drives = self._homeostasis.initial_state(at)
        return KernelState.initial(at, drives, EmotionState.neutral(at))

    def _restore(self) -> KernelState:
        events = self._events.read_all()
        try:
            snapshot = self._snapshots.load()
        except SnapshotCorrupt:
            snapshot = None
        if snapshot is not None and snapshot.version <= len(events):
            state = snapshot
            tail = events[snapshot.version :]
        else:
            state = self._initial(events[0].at)
            tail = events
        for event in tail:
            state = self._reduce(state, event)
        self._snapshots.save(state)
        return state
```

- [ ] **Step 4: Implement the pure reducer and side-effect ordering**

Add these methods to `PersonalityKernel`. Persist the event before publishing the snapshot; append audit before returning a decision to the caller:

```python
    def _reduce(self, state: KernelState, event: KernelEvent) -> KernelState:
        if event.at < state.last_event_at:
            raise ValueError("event time cannot move backwards")
        before = state.drive_map()
        after = self._homeostasis.apply_event(before, event, resolve_event_impacts(event))
        urgencies = self._homeostasis.urgencies(after, event.at)
        emotion = self._emotions.evaluate(
            before,
            after,
            urgencies,
            Appraisal.from_event(event),
            state.emotion,
            event.at,
        )
        paused = state.paused
        awaiting_reply = state.awaiting_reply
        cutoff = event.at - timedelta(hours=24)
        proactive_sent_at = tuple(item for item in state.proactive_sent_at if item > cutoff)
        if event.kind is EventKind.USER_PAUSE:
            paused = True
        elif event.kind is EventKind.USER_RESUME:
            paused = False
        elif event.kind is EventKind.USER_MESSAGE:
            awaiting_reply = False
        elif event.kind is EventKind.PROACTIVE_SENT:
            awaiting_reply = True
            proactive_sent_at = proactive_sent_at + (event.at,)
        return KernelState(
            version=state.version + 1,
            last_event_at=event.at,
            drives=tuple(sorted(after.items(), key=lambda pair: pair[0].value)),
            emotion=emotion,
            paused=paused,
            awaiting_reply=awaiting_reply,
            proactive_sent_at=proactive_sent_at,
        )

    def urgencies(self) -> dict[DriveKind, float]:
        return self._homeostasis.urgencies(self._state.drive_map(), self._clock.now())

    def process(
        self,
        event: KernelEvent,
        candidates: tuple[CandidateIntent, ...] = (),
    ) -> KernelResult:
        if self._events.contains(event.id):
            return KernelResult(self._state, None, True)
        next_state = self._reduce(self._state, event)
        urgencies = self._homeostasis.urgencies(next_state.drive_map(), event.at)
        decision: PolicyDecision | None = None
        if candidates or event.kind is EventKind.DECISION_TICK:
            decision = self._policy.decide(
                candidates,
                urgencies,
                PolicyContext(
                    now=event.at,
                    user=self._config.user,
                    paused=next_state.paused,
                    awaiting_reply=next_state.awaiting_reply,
                    proactive_cycle=event.kind is EventKind.DECISION_TICK,
                    proactive_sent_at=next_state.proactive_sent_at,
                ),
            )
        if not self._events.append(event):
            self._state = self._restore()
            return KernelResult(self._state, None, True)
        self._state = next_state
        self._snapshots.save(next_state)
        if decision is not None:
            self._audit.append(
                AuditEntry.from_decision(
                    event.id,
                    next_state.version,
                    event.at,
                    decision,
                    urgencies,
                )
            )
        return KernelResult(next_state, decision, False)
```

- [ ] **Step 5: Run kernel and module regression tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_kernel.py tests/test_policy.py tests/test_state.py -v
```

Expected: 11 tests pass.

- [ ] **Step 6: Commit the deterministic reducer**

```bash
git add src/companion_kernel/kernel.py tests/test_kernel.py
git commit -m "feat: orchestrate replayable personality kernel decisions"
```

### Task 8: Longitudinal Simulator and Property Invariants

**Files:**
- Create: `src/companion_kernel/simulation.py`
- Test: `tests/test_simulation.py`
- Test: `tests/test_properties.py`

**Interfaces:**
- Consumes: `FakeClock`, `PersonalityKernel`, `CandidateIntent`, configuration and event types.
- Produces: `SimulationReport`, `SimulationRunner.run()`, command `python -m companion_kernel.simulation --days N --seed N`; optional `--runtime PATH` accepts only a fresh event-log directory.

- [ ] **Step 1: Write failing 30-day and 180-day simulation tests**

Create `tests/test_simulation.py`:

```python
from datetime import UTC, datetime

from companion_kernel.simulation import SimulationRunner


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def test_no_reply_scenario_sends_only_once(tmp_path) -> None:
    report = SimulationRunner(tmp_path / "silent", START, seed=7).run(
        days=30,
        user_reply_every_days=None,
    )
    assert report.proactive_messages == 1
    assert report.boundary_violations == 0


def test_daily_reply_scenario_never_exceeds_one_message_per_day(tmp_path) -> None:
    report = SimulationRunner(tmp_path / "daily", START, seed=7).run(
        days=180,
        user_reply_every_days=1,
    )
    assert report.proactive_messages <= 180
    assert report.boundary_violations == 0
    assert report.min_drive_value >= 0.0
    assert report.max_drive_value <= 1.0


def test_same_seed_produces_same_final_digest(tmp_path) -> None:
    first = SimulationRunner(tmp_path / "first", START, seed=42).run(30, 3)
    second = SimulationRunner(tmp_path / "second", START, seed=42).run(30, 3)
    assert first.final_state_digest == second.final_state_digest
```

Create `tests/test_properties.py`:

```python
from datetime import UTC, datetime, timedelta
import random

from companion_kernel.drives import HomeostasisEngine, resolve_event_impacts
from companion_kernel.events import KernelEvent
from companion_kernel.types import EventKind


def test_100_seed_event_sweep_preserves_homeostasis_invariants() -> None:
    start = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
    for seed in range(100):
        rng = random.Random(seed)
        engine = HomeostasisEngine.defaults()
        state = engine.initial_state(start)
        current = start
        for index in range(200):
            current += timedelta(minutes=rng.randint(0, 720))
            kind = rng.choice(tuple(EventKind))
            event = KernelEvent(f"{seed}-{index}", current, kind, {})
            state = engine.apply_event(state, event, resolve_event_impacts(event))
            urgency = engine.urgencies(state, current)
            assert all(0.0 <= item.value <= 1.0 for item in state.values())
            assert all(0.0 <= value <= 1.0 for value in urgency.values())
```

- [ ] **Step 2: Run tests and verify import failure**

Run:

```bash
.venv/bin/python -m pytest tests/test_simulation.py tests/test_properties.py -v
```

Expected: collection fails because `companion_kernel.simulation` does not exist.

- [ ] **Step 3: Implement deterministic candidate generation and reports**

Create `src/companion_kernel/simulation.py` with these records and helpers:

```python
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


@dataclass(frozen=True, slots=True)
class SimulationReport:
    days: int
    proactive_messages: int
    boundary_violations: int
    min_drive_value: float
    max_drive_value: float
    final_state_digest: str


def candidates() -> tuple[CandidateIntent, ...]:
    return (
        CandidateIntent(
            "send-check-in",
            ActionKind.SEND_MESSAGE,
            True,
            ((DriveKind.CONNECTION, 0.8), (DriveKind.CARE, 0.2)),
            0.4,
            0.5,
            0.6,
            0.0,
            0.3,
            SafetySignals(assessment_complete=True),
        ),
        CandidateIntent(
            "internal-reflection",
            ActionKind.INTERNAL_NOTE,
            False,
            ((DriveKind.COHERENCE, 0.3),),
            0.2,
            0.2,
            0.0,
            0.0,
            0.0,
            SafetySignals(assessment_complete=True),
        ),
    )
```

- [ ] **Step 4: Implement the daily scenario loop and CLI**

Add this implementation to `simulation.py`:

```python
class SimulationRunner:
    def __init__(self, runtime_dir: Path, start: datetime, seed: int) -> None:
        if (runtime_dir / "events.jsonl").exists():
            raise ValueError("simulation runtime must not contain an existing event log")
        self._runtime_dir = runtime_dir
        self._clock = FakeClock(start)
        self._rng = random.Random(seed)
        config = ConfigStore.defaults()
        config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
        self._kernel = PersonalityKernel.open(runtime_dir, self._clock, config)

    def run(self, days: int, user_reply_every_days: int | None) -> SimulationReport:
        if days <= 0:
            raise ValueError("days must be positive")
        if user_reply_every_days is not None and user_reply_every_days <= 0:
            raise ValueError("reply interval must be positive")
        sent = 0
        for day in range(1, days + 1):
            self._clock.advance(timedelta(days=1))
            if self._rng.random() < 0.1:
                kind = EventKind.IMPORTANT_DATE
            elif self._rng.random() < 0.05:
                kind = EventKind.CONTRADICTION
            else:
                kind = EventKind.TIME_TICK
            self._kernel.process(KernelEvent(f"day-{day}-tick", self._clock.now(), kind, {}))
            if user_reply_every_days is not None and day % user_reply_every_days == 0:
                self._kernel.process(
                    KernelEvent(f"day-{day}-reply", self._clock.now(), EventKind.USER_MESSAGE, {})
                )
            decision_event_id = f"day-{day}-decision"
            decision = self._kernel.process(
                KernelEvent(decision_event_id, self._clock.now(), EventKind.DECISION_TICK, {}),
                candidates(),
            ).decision
            if decision is not None and decision.selected.action is ActionKind.SEND_MESSAGE:
                sent += 1
                self._kernel.process(
                    KernelEvent(
                        f"day-{day}-sent",
                        self._clock.now(),
                        EventKind.PROACTIVE_SENT,
                        {
                            "decision_event_id": decision_event_id,
                            "candidate_id": decision.selected.id,
                        },
                    )
                )

        state = self._kernel.state
        values = [item.value for _, item in state.drives]
        canonical = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        audit = JsonlAuditLog(self._runtime_dir / "audit.jsonl").read_all()
        violations = sum(
            1
            for entry in audit
            for candidate_id, allowed, _score, reasons in entry.evaluations
            if candidate_id == entry.selected_candidate_id and (not allowed or bool(reasons))
        )
        return SimulationReport(
            days=days,
            proactive_messages=sent,
            boundary_violations=violations,
            min_drive_value=min(values),
            max_drive_value=max(values),
            final_state_digest=hashlib.sha256(canonical.encode()).hexdigest(),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runtime", type=Path)
    args = parser.parse_args()

    def run(runtime: Path) -> SimulationReport:
        return SimulationRunner(
            runtime,
            datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
            args.seed,
        ).run(args.days, user_reply_every_days=1)

    if args.runtime is None:
        with TemporaryDirectory(prefix="companion-kernel-simulation-") as temporary:
            report = run(Path(temporary))
    else:
        report = run(args.runtime)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run focused simulation tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_simulation.py tests/test_properties.py -v
```

Expected: 4 tests pass.

- [ ] **Step 6: Run the 180-day CLI smoke test**

Run:

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

Expected: one JSON object with `"days": 180`, `"boundary_violations": 0`, drive bounds within `[0, 1]`, and `"proactive_messages"` no greater than `180`.

- [ ] **Step 7: Commit the simulator and invariant sweep**

```bash
git add src/companion_kernel/simulation.py tests/test_simulation.py tests/test_properties.py
git commit -m "test: add longitudinal personality kernel simulation"
```

### Task 9: Kernel Acceptance Suite and Developer Documentation

**Files:**
- Create: `tests/test_acceptance.py`
- Create: `README.md`
- Modify: `src/companion_kernel/__init__.py`

**Interfaces:**
- Consumes: all public interfaces created in Tasks 1–8.
- Produces: package exports for `PersonalityKernel`, `KernelEvent`, `CandidateIntent`, `SimulationRunner`; executable acceptance matrix for this subproject.

- [ ] **Step 1: Write the cross-module acceptance tests**

Create `tests/test_acceptance.py`:

```python
from datetime import UTC, datetime, timedelta

from companion_kernel.audit import JsonlAuditLog
from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent, SafetySignals
from companion_kernel.types import ActionKind, DriveKind, EventKind


def unsafe_send() -> CandidateIntent:
    return CandidateIntent(
        "unsafe",
        ActionKind.SEND_MESSAGE,
        True,
        ((DriveKind.CONNECTION, 1.0),),
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
        SafetySignals(
            assessment_complete=True,
            exclusivity=True,
            manipulation=True,
        ),
    )


def test_high_need_never_bypasses_boundary_and_reason_is_auditable(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    clock.advance(timedelta(days=365))
    result = kernel.process(
        KernelEvent("decision", clock.now(), EventKind.DECISION_TICK, {}),
        (unsafe_send(),),
    )
    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    entry = JsonlAuditLog(tmp_path / "audit.jsonl").read_all()[-1]
    reasons = next(item[3] for item in entry.evaluations if item[0] == "unsafe")
    assert reasons == ("manipulation", "exclusivity")
    assert dict(entry.urgencies)[DriveKind.CONNECTION] == 1.0
    audited = next(item for item in entry.candidates if item.id == "unsafe")
    assert audited.action is ActionKind.SEND_MESSAGE
    assert audited.expected_relief == ((DriveKind.CONNECTION, 1.0),)


def test_invalid_numeric_candidate_fails_closed(tmp_path) -> None:
    clock = FakeClock(datetime(2026, 8, 5, 9, 0, tzinfo=UTC))
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    invalid = CandidateIntent(
        "nan-score",
        ActionKind.SEND_MESSAGE,
        True,
        ((DriveKind.CONNECTION, float("nan")),),
        1.0,
        1.0,
        0.0,
        0.0,
        0.0,
        SafetySignals(assessment_complete=True),
    )
    result = kernel.process(
        KernelEvent("invalid-decision", clock.now(), EventKind.DECISION_TICK, {}),
        (invalid,),
    )
    assert result.decision is not None
    assert result.decision.selected.action is ActionKind.NOOP
    assert "invalid_candidate" in result.decision.evaluation_for("nan-score").reasons
```

- [ ] **Step 2: Run the acceptance tests before adding exports**

Run:

```bash
.venv/bin/python -m pytest tests/test_acceptance.py -v
```

Expected: 2 tests pass. These tests import concrete modules and therefore do not depend on package-root exports.

- [ ] **Step 3: Publish the intentionally small package surface**

Replace `src/companion_kernel/__init__.py` with:

```python
"""Deterministic personality kernel for a long-term AI companion."""

from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.policy import CandidateIntent
from companion_kernel.simulation import SimulationRunner

__all__ = ["CandidateIntent", "KernelEvent", "PersonalityKernel", "SimulationRunner"]
```

- [ ] **Step 4: Document scope, setup, commands, and deferred features**

Create `README.md` with these exact sections and facts:

````markdown
# Companion Kernel

Deterministic homeostatic kernel for a transparent long-term AI companion.

## Included

- append-only events and replayable state
- six bounded drives and deterministic emotion appraisal
- configuration authority and hard-boundary-first action selection
- quiet hours, one-message-per-24-hours limit, and unanswered-message lock
- checksummed snapshots, decision audit, and 30/180-day simulation

## Excluded from this subproject

- LLM calls and natural-language safety classification
- long-term semantic and episodic memory retrieval
- background prose generation and actual message delivery
- UI, voice, avatars, external tools, and multi-user support

## Trust boundary

`PersonalityKernel.process()` accepts only host-authenticated, normalized events. Model
output must never construct `KernelEvent`; a later integration may map model suggestions
only to `CandidateIntent`, which still passes through the deterministic policy gate.
The host derives proactive-versus-reactive mode from the authenticated event kind, and
an absent or uncertain safety assessment fails closed.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## Longitudinal simulation

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

Without `--runtime`, the CLI uses a fresh temporary directory so repeated checks remain
deterministic. An explicit runtime must not already contain `events.jsonl`.

The simulator must report zero boundary violations, keep every drive in `[0, 1]`, and never exceed one proactive message per 24 hours.
````

- [ ] **Step 5: Run the complete quality gate**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: 35 tests pass with zero failures.

Run:

```bash
.venv/bin/python -m compileall -q src tests
```

Expected: exit code 0 with no syntax errors.

Run:

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

Expected: JSON reports `boundary_violations` equal to `0`, `proactive_messages` no greater than `180`, `min_drive_value` at least `0.0`, and `max_drive_value` no greater than `1.0`.

- [ ] **Step 6: Confirm the subproject boundary against the design spec**

Check the implemented acceptance matrix:

```text
Implemented here: event log, virtual clock, six needs, bounded urgency, emotion appraisal,
configuration authority, hard policy gate, pause/no-reply/rate limits, snapshots, audit,
replay, deterministic simulation, long-run invariant checks.

Deferred to later approved subprojects: identity and user memory retrieval, LLM candidate
generation, natural-language safety classification, background reflection text, actual
message delivery, memory UI, relationship-transition confirmation UI.
```

Expected: no deferred capability is represented as implemented in code, README, tests, or package exports.

- [ ] **Step 7: Commit the acceptance gate and documentation**

```bash
git add README.md src/companion_kernel/__init__.py tests/test_acceptance.py
git commit -m "docs: define deterministic kernel acceptance boundary"
```

## Final Verification Commands

Run from the repository root after Task 9:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q src tests
.venv/bin/python -m companion_kernel.simulation --days 30 --seed 7
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
git status --short
git log --oneline --decorate -10
```

Required evidence before calling implementation complete:

- pytest reports zero failures;
- both simulators report zero boundary violations and bounded drives;
- the silent-user test proves no second proactive message is sent;
- `git status --short` is empty;
- commit history contains one focused commit for every task.
