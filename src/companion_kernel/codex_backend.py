"""Codex CLI backend using the user's saved local Codex authentication."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import (
    MODEL_OUTPUT_SCHEMA,
    SYSTEM_INSTRUCTIONS,
    BackendConfigurationError,
    ModelContext,
    ModelProtocolError,
    ModelTurn,
    ModelUnavailable,
    _parse_json_text,
    parse_model_payload,
)


class CodexCLIBackend:
    """Run ``codex exec`` as an isolated, read-only proposal generator."""

    def __init__(self, settings: ModelSettings) -> None:
        if settings.provider != "codex_cli":
            raise BackendConfigurationError(
                "CodexCLIBackend requires provider='codex_cli'"
            )
        if not settings.model.strip():
            raise BackendConfigurationError("Codex model name is not configured")
        executable = shutil.which("codex")
        if executable is None:
            raise BackendConfigurationError("codex CLI is not installed or not on PATH")
        self._settings = settings
        self._executable = executable

    def propose(self, context: ModelContext) -> ModelTurn:
        prompt = SYSTEM_INSTRUCTIONS + "\nBounded runtime context follows:\n" + json.dumps(
            context.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        with tempfile.TemporaryDirectory(prefix="companion-codex-") as directory:
            root = Path(directory)
            schema_path = root / "proposal-schema.json"
            output_path = root / "proposal.json"
            schema_path.write_text(
                json.dumps(MODEL_OUTPUT_SCHEMA, ensure_ascii=False, allow_nan=False),
                encoding="utf-8",
            )
            command = [
                self._executable,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--color",
                "never",
                "--model",
                self._settings.model,
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    cwd=root,
                    env=_minimal_environment(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=self._settings.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise ModelUnavailable("codex CLI timed out") from exc
            except OSError as exc:
                raise ModelUnavailable("codex CLI could not be started") from exc
            if completed.returncode != 0:
                detail = _safe_error_detail(completed.stderr)
                raise ModelUnavailable(
                    f"codex CLI failed with exit code {completed.returncode}"
                    + (f": {detail}" if detail else "")
                )
            try:
                content = output_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise ModelProtocolError("codex CLI produced no final output") from exc
            if len(content) > self._settings.max_output_chars:
                raise ModelProtocolError("codex CLI output is too large")

        payload = _parse_json_text(content)
        return parse_model_payload(
            payload,
            provider="codex_cli",
            max_candidates=self._settings.max_candidates,
        )


def _minimal_environment() -> dict[str, str]:
    """Keep CLI authentication available without exposing unrelated secrets."""

    allowed = (
        "PATH",
        "HOME",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "NO_PROXY",
    )
    return {name: os.environ[name] for name in allowed if name in os.environ}


def _safe_error_detail(stderr: str) -> str:
    messages = list(re.finditer(r'"message"\s*:\s*"((?:\\.|[^"\\])*)"', stderr))
    if messages:
        message = messages[-1]
        try:
            decoded = json.loads(f'"{message.group(1)}"')
        except json.JSONDecodeError:
            decoded = message.group(1)
        return " ".join(str(decoded).split())[:240]
    lines = [" ".join(line.split()) for line in stderr.splitlines() if line.strip()]
    if not lines:
        return ""
    preferred = next(
        (line for line in reversed(lines) if line.lower().startswith(("error:", "fatal:"))),
        lines[-1],
    )
    return preferred[:240]
