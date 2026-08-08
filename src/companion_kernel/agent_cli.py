"""Small interactive shell for a configured companion model backend."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import uuid4

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import SystemClock
from companion_kernel.config import ConfigActor, ConfigStore, ModelSettings, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import ModelBackendError, create_model_backend
from companion_kernel.types import EventKind


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with a policy-gated AI Personification runtime")
    parser.add_argument("--provider", choices=("ollama", "openai_responses"), default="ollama")
    parser.add_argument("--model", required=True, help="local model name or OpenAI model id")
    parser.add_argument("--base-url", default=None, help="provider base URL")
    parser.add_argument("--runtime", type=Path, default=Path("./runtime"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_url = args.base_url or (
        "https://api.openai.com/v1"
        if args.provider == "openai_responses"
        else "http://127.0.0.1:11434"
    )
    settings = ModelSettings(
        provider=args.provider,
        model=args.model,
        base_url=base_url,
    )
    try:
        backend = create_model_backend(settings)
    except ModelBackendError as exc:
        print(f"backend configuration error: {exc}", file=sys.stderr)
        return 2

    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(args.runtime, SystemClock(), config)
    runtime = AgentRuntime(kernel, backend)

    print("AI Personification chat. Type /exit to stop.")
    while True:
        try:
            line = input("you> ")
        except EOFError:
            print()
            return 0
        if line.strip().lower() in {"/exit", "/quit"}:
            return 0
        if not line.strip():
            continue
        event = KernelEvent(
            f"user-{uuid4().hex}",
            datetime.now(UTC),
            EventKind.USER_MESSAGE,
            {"message": line},
        )
        result = runtime.handle_event(event)
        if result.model_error:
            print(f"agent unavailable: {result.model_error}", file=sys.stderr)
        elif result.response_text is not None:
            print(f"agent> {result.response_text}")
        else:
            print("agent> [no action]")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

