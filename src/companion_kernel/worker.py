"""Background decision worker for a persistent companion runtime."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from uuid import uuid4

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigStore, ModelSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import create_model_backend
from companion_kernel.types import EventKind
from companion_kernel.types import ActionKind


def run_cycle(runtime: AgentRuntime) -> dict[str, object]:
    now = datetime.now(UTC)
    event = KernelEvent(
        f"background:{uuid4().hex}",
        now,
        EventKind.DECISION_TICK,
        {"proactive_cycle": True},
    )
    result = runtime.handle_event(event)
    selected = result.kernel.decision.selected.action.value if result.kernel.decision else None
    internal_recorded = False
    if (
        result.action_id is not None
        and result.kernel.decision is not None
        and result.kernel.decision.selected.action is ActionKind.INTERNAL_NOTE
    ):
        runtime.acknowledge_action(result.action_id, outcome="delivered", at=now)
        internal_recorded = True
    return {
        "at": now.isoformat(),
        "selected": selected,
        "action_id": result.action_id,
        "response_text": result.response_text,
        "model_error": result.model_error,
        "internal_recorded": internal_recorded,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run background companion decision cycles")
    parser.add_argument("--provider", choices=("ollama", "openai_responses", "codex_cli"), default="ollama")
    parser.add_argument("--model", required=True)
    parser.add_argument("--runtime", type=Path, default=Path("./runtime"))
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--timeout", type=float, default=None)
    parser.add_argument("--interval-seconds", type=float, default=3600.0)
    parser.add_argument("--once", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.once and args.interval_seconds < 30:
        raise SystemExit("interval must be at least 30 seconds")
    base_url = args.base_url or (
        "https://api.openai.com/v1"
        if args.provider == "openai_responses"
        else "http://127.0.0.1:11434"
    )
    settings = ModelSettings(
        provider=args.provider,
        model=args.model,
        base_url=base_url,
        timeout_seconds=args.timeout or (120.0 if args.provider == "codex_cli" else 30.0),
    )
    config = ConfigStore.open(args.runtime)
    kernel = PersonalityKernel.open(args.runtime, SystemClock(), config)
    runtime = AgentRuntime(kernel, create_model_backend(settings))
    while True:
        print(json.dumps(run_cycle(runtime), ensure_ascii=False, sort_keys=True), flush=True)
        if args.once:
            return 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
