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

The simulator must report zero boundary violations, keep every drive in `[0, 1]`, and
never exceed one proactive message per 24 hours.
