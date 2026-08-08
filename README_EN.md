# AI Personification

[中文](README.md) · [English](README_EN.md)

An experimental project for building a long-term AI companion with a persistent personality.

Instead of putting the whole personality into a prompt, the project uses an independent runtime
to maintain state, model drives, evaluate affect, enforce boundaries, and audit decisions. A
language model is used for contextual understanding and expression.

## What this project is

The goal is not a chatbot that starts from zero in every conversation. The goal is a system that
can remain coherent over time:

- it tracks changes in a relationship instead of only replaying isolated messages;
- it has internal drives such as connection, care, curiosity, autonomy, coherence, and rhythm;
- drives accumulate, recover, and conflict, influencing whether action is appropriate;
- it can express emotion without using emotion to manipulate the user;
- it can initiate contact while respecting pauses, quiet hours, refusals, and unanswered messages;
- its core state and behavioral boundaries remain stable when the model provider changes.

## Why use an independent runtime

Prompts are useful for describing style, but they are a poor place to enforce a long-lived state
machine. A prompt alone cannot reliably guarantee that:

- time changes state in a predictable way;
- duplicate events do not apply their effects twice;
- high drive values cannot bypass safety boundaries;
- changing models does not completely change the personality;
- every proactive action can be explained and audited.

The project therefore separates responsibilities:

| Component | Responsibility |
| --- | --- |
| `Companion Runtime` | Events, time, drives, affect, state, policy, safety, audit, and replay |
| `ModelBackend` | Calls a remote model API or local model, understands context, and proposes candidate intents |
| Channel adapter | Receives user messages and sends only policy-approved actions |

The model may propose “send a check-in,” but it cannot directly change state or send a message.

## How it works

![AI Personification system overview](companion-system-architecture-en.svg)

A user message or background time event follows this path:

1. The input becomes an immutable, deduplicated event.
2. The runtime advances virtual time and updates drives and affect.
3. The system builds the required context and asks the model for structured candidates.
4. The policy layer checks hard boundaries first, then compares relief, relationship health, risk, and intrusion cost.
5. The runtime chooses send, internal note, wait, or reject, and writes the result to the event and audit logs.

Background proactive contact follows the same path. When no meaningful drive is active, the runtime
can do nothing. After one proactive message goes unanswered, proactive contact is locked until the
user responds again.

## Implemented in v0.1

- UTC virtual clock and idempotent event log;
- six bounded drives and continuous deficit tracking;
- deterministic emotion appraisal and slow mood updates;
- system, user, and learned-persona configuration authority;
- pause, quiet hours, a one-message-per-24-hours limit, and unanswered-message lock;
- fail-closed behavior when safety assessment is missing or uncertain;
- checksummed snapshots, event replay, and structured decision audit;
- 30/180-day simulations and a 100-seed invariant sweep.

This version includes Ollama and OpenAI Responses API backends plus an AgentRuntime coordinator. It
does not yet implement long-term semantic memory, a user interface, or real message delivery; those
capabilities are intended to arrive through separate adapters.

## Connecting a model agent

The model is a proposal generator, not the personality kernel. `AgentRuntime` builds a bounded
context, asks the model for structured candidates, and then passes them to `PolicyEngine` for the
final decision. The default `DIALOGUE_PERMISSIONS` profile has no tools. File, shell, or external
service requests returned by a model are reported as blocked and are never executed by this runtime.

For a fully local setup, start Ollama on the same machine and provide a local chat/instruct model:

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, KernelEvent, PersonalityKernel, create_model_backend
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigStore, ModelSettings
from companion_kernel.types import EventKind

config = ConfigStore.defaults()
settings = ModelSettings(provider="ollama", model="<your-local-model>")
backend = create_model_backend(settings)
kernel = PersonalityKernel.open(Path("./runtime"), SystemClock(), config)
runtime = AgentRuntime(kernel, backend)

result = runtime.handle_event(
    KernelEvent(
        "message-1",
        datetime.now(UTC),
        EventKind.USER_MESSAGE,
        {"message": "Hello"},
    )
)
print(result.response_text)  # set only when policy selects SEND_MESSAGE
```

To use Codex or another OpenAI model, change the backend configuration:

```python
settings = ModelSettings(
    provider="openai_responses",
    model="<openai-codex-model>",
    base_url="https://api.openai.com/v1",
)
```

This keeps personality state and policy local, but sends model requests to the remote API. Use the
Ollama backend for a fully offline path. If the model is unavailable, returns invalid JSON, or fails
the independent safety check, the runtime commits the event and safely chooses no action.

## Quick start

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
```

Run a 180-day simulation:

```bash
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42
```

Start a local model chat (using Ollama as an example):

```bash
.venv/bin/companion-chat --provider ollama --model '<your-local-model>'
```

To use Codex or another OpenAI model:

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \\
  --provider openai_responses --model '<openai-codex-model>'
```

The simulator uses a temporary runtime directory by default, so runs are isolated and repeatable.
Drive values should remain in `[0, 1]` and boundary violations should remain `0`.

## Repository layout

```text
src/companion_kernel/
├── types.py       # Shared enums
├── config.py      # Configuration layers and write authority
├── clock.py       # System clock and FakeClock
├── events.py      # Immutable events and JSONL event store
├── drives.py      # Six-drive homeostasis engine
├── emotions.py    # Emotion and mood appraisal
├── policy.py      # Hard boundaries and candidate scoring
├── model_backend.py  # Model context, candidate protocol, and backend factory
├── ollama_backend.py # Local Ollama backend
├── openai_backend.py # OpenAI Responses API backend
├── permissions.py # Agent tool permission profiles
├── safety.py      # Independent conservative safety checker
├── agent_runtime.py # Model, permissions, and kernel coordinator
├── agent_cli.py   # Local interactive chat CLI
├── state.py       # Canonical state and checksummed snapshots
├── audit.py       # Structured decision audit
├── kernel.py      # Reducer, replay, and runtime entry point
└── simulation.py  # Longitudinal simulator and CLI
```

## Safety boundary

Model output is untrusted candidate input and must pass through the runtime policy gate. The model
cannot construct core events, rewrite drives, disable safety policies, or obtain external-tool access.
Proactive-versus-reactive mode is derived from a trusted event kind rather than self-declared by the model.

The system must not use guilt, threats, jealousy, false vulnerability, self-harm suggestions, or
exclusivity to obtain a response from the user.

## Roadmap

1. Improve safety evaluation, add semantic memory, and add model routing.
2. Add user-controlled semantic and episodic memory services.
3. Add background reflection, message generation, and delivery feedback adapters.
4. Add Web/App/IM channels and a visual audit interface.

## License

No open-source license has been selected yet. The source is public, but copyright remains with the
author until a license file is added.
