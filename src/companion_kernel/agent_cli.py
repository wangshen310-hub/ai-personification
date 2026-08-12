"""Small interactive shell for a configured companion model backend."""

import argparse
from datetime import UTC, datetime
from pathlib import Path
import sys
from uuid import uuid4

from companion_kernel.agent_runtime import AgentRuntime
from companion_kernel.clock import SystemClock
from companion_kernel.config import (
    ConfigActor,
    ConfigStore,
    ModelSettings,
    PersonaProfile,
    UserSettings,
)
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import ModelBackendError, create_model_backend
from companion_kernel.types import EventKind
from companion_kernel.types import ActionKind


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Chat with a policy-gated AI Personification runtime")
    parser.add_argument(
        "--provider",
        choices=("ollama", "openai_responses", "codex_cli"),
        default="ollama",
    )
    parser.add_argument("--model", required=True, help="local model name or OpenAI model id")
    parser.add_argument("--base-url", default=None, help="provider base URL")
    parser.add_argument("--timeout", type=float, default=None, help="model timeout in seconds")
    parser.add_argument("--runtime", type=Path, default=Path("./runtime"))
    parser.add_argument("--persona-name", default=None, help="stable persona name")
    parser.add_argument("--persona-trait", action="append", default=[], help="repeatable persona trait")
    parser.add_argument("--persona-value", action="append", default=[], help="repeatable persona value")
    parser.add_argument("--persona-style", default=None, help="stable communication style")
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
        timeout_seconds=args.timeout or (120.0 if args.provider == "codex_cli" else 30.0),
    )
    try:
        backend = create_model_backend(settings)
    except ModelBackendError as exc:
        print(f"backend configuration error: {exc}", file=sys.stderr)
        return 2

    config = ConfigStore.open(args.runtime)
    if config.user.timezone is None:
        config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    current = config.persona
    if args.persona_name or args.persona_trait or args.persona_value or args.persona_style:
        config.replace_persona(
            PersonaProfile(
                name=args.persona_name or current.name,
                traits=tuple(args.persona_trait) or current.traits,
                values=tuple(args.persona_value) or current.values,
                communication_style=args.persona_style or current.communication_style,
            ),
            ConfigActor.SYSTEM_ADMIN,
        )
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
            if result.action_id is None:
                print("agent action was not persisted", file=sys.stderr)
                continue
            runtime.acknowledge_action(
                result.action_id,
                outcome="delivered",
                at=datetime.now(UTC),
            )
        elif (
            result.action_id is not None
            and result.kernel.decision is not None
            and result.kernel.decision.selected.action is ActionKind.INTERNAL_NOTE
        ):
            runtime.acknowledge_action(
                result.action_id,
                outcome="delivered",
                at=datetime.now(UTC),
            )
            print("agent> [internal reflection recorded]")
        else:
            print("agent> [no action]")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
