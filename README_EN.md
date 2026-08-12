# AI Personification

[中文](README.md) · [English](README_EN.md)

A long-term companion runtime with model-external motivation, persistent identity, and background agency.

The personality is not merely a prompt. The runtime owns identity, drives, relationship state, evidence-backed memory, decisions, and action delivery state. Language models interpret and render language, but cannot manufacture a reason to act through self-scoring.

## Runtime loop

1. User input and time changes enter a transactional SQLite event log.
2. A semantic interpreter derives sourced, confidence-bearing facts such as appreciation, boundaries, rejection, conflict, repair, commitments, preferences, and corrections.
3. The homeostasis engine updates connection, care, curiosity, autonomy, coherence, and interaction load.
4. A model-independent motivation engine creates native respond, check-in, reflect, and wait opportunities.
5. The model renders only actions currently authorized by the kernel. Model-authored benefit scores are discarded.
6. Policy compares need relief, relationship state, load, intrusion, repetition, and hard boundaries.
7. Selected actions enter a durable Outbox. Personality state changes only after a channel confirms the persisted `action_id`.

## Implemented

- SQLite WAL event persistence with unique event IDs and legacy JSONL import;
- durable persona, user settings, evidence memories, learned drive weights, and Outbox actions;
- kernel-owned motivation, including a guaranteed wait option;
- semantic relationship updates instead of trust growth per message count;
- strict action acknowledgement by durable ID and cancellation on pause;
- quiet hours, configurable 24-hour cadence limits, and a 72-hour unanswered cooldown before reevaluation;
- interactive chat and a separate background worker;
- Ollama, OpenAI Responses API, and authenticated Codex CLI backends;
- deterministic audit, adversarial tests, and 30/180-day simulations.

This remains a single-user, single-persona text runtime. Full episodic retrieval, external channel adapters, a web UI, and multi-user isolation are not yet included.

## Install and verify

Python 3.12 or newer is required.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest -q -W error
.venv/bin/python -m companion_kernel.simulation --days 30 --seed 7 --reply-every-days 0
.venv/bin/python -m companion_kernel.simulation --days 180 --seed 42 --reply-every-days 1
```

The silent-user simulation should reduce cadence after an unanswered message while remaining able to reevaluate after cooldown. Both simulations should report zero boundary violations and keep all drive values within `[0, 1]`. The 30/180-day durations are test windows only: neither is used as a runtime limit, and there is no lifetime cap on proactive messages.

## Chat

```bash
.venv/bin/companion-chat \
  --provider ollama --model '<local-model>' \
  --runtime ./runtime \
  --persona-name 'Mira' \
  --persona-trait playful --persona-trait direct
```

Persona arguments may be omitted on later starts because configuration is restored from `runtime/runtime.db`.

Use OpenAI Responses:

```bash
OPENAI_API_KEY='...' .venv/bin/companion-chat \
  --provider openai_responses --model '<model-id>' --runtime ./runtime
```

Or reuse local Codex authentication:

```bash
.venv/bin/companion-chat \
  --provider codex_cli --model 'gpt-5.6-sol' --runtime ./runtime
```

## Background worker

No GUI is required. Run a persistent decision worker against the same runtime directory:

```bash
.venv/bin/companion-worker \
  --provider ollama --model '<local-model>' \
  --runtime ./runtime --interval-seconds 3600
```

The worker persists rendered actions but does not claim delivery. A real channel adapter must send the content and acknowledge the durable action ID.

## Python API

```python
from datetime import UTC, datetime
from pathlib import Path

from companion_kernel import AgentRuntime, ConfigStore, KernelEvent, PersonalityKernel
from companion_kernel.clock import SystemClock
from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import create_model_backend
from companion_kernel.types import EventKind

runtime_dir = Path("./runtime")
config = ConfigStore.open(runtime_dir)
kernel = PersonalityKernel.open(runtime_dir, SystemClock(), config)
backend = create_model_backend(ModelSettings(provider="ollama", model="<model>"))
runtime = AgentRuntime(kernel, backend)

event = KernelEvent("message-1", datetime.now(UTC), EventKind.USER_MESSAGE, {"message": "Hello"})
result = runtime.handle_event(event)
if result.response_text is not None and result.action_id is not None:
    runtime.acknowledge_action(result.action_id, outcome="delivered", at=datetime.now(UTC))
```

Unknown or forged action IDs are rejected, and repeated delivery acknowledgement is idempotent.

## Design boundary

The model cannot mutate core events, identity, drives, relationship state, policy, or the Outbox. The default dialogue profile grants no file, shell, or external-service tools. Waiting and internal reflection are valid outcomes; the runtime does not optimize for interaction time or require the user to respond.

## License

No open-source license has been selected. The source is publicly visible, but all rights remain reserved until a license is added.
