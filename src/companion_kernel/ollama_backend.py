"""Ollama HTTP backend for fully local model inference."""

import json
from typing import Mapping

from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import (
    SYSTEM_INSTRUCTIONS,
    BackendConfigurationError,
    ModelContext,
    ModelProtocolError,
    ModelTurn,
    parse_model_payload,
    post_json,
)


class OllamaBackend:
    """Call a local Ollama server and parse its structured proposal response."""

    def __init__(self, settings: ModelSettings) -> None:
        if settings.provider != "ollama":
            raise BackendConfigurationError("OllamaBackend requires provider='ollama'")
        if not settings.model.strip():
            raise BackendConfigurationError("Ollama model name is not configured")
        self._settings = settings

    def propose(self, context: ModelContext) -> ModelTurn:
        body: dict[str, object] = {
            "model": self._settings.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                {
                    "role": "user",
                    "content": json.dumps(
                        context.to_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                        allow_nan=False,
                    ),
                },
            ],
            "format": _schema_for_ollama(),
            "options": {
                "temperature": 0.2,
                "num_predict": self._settings.max_output_tokens,
            },
        }
        response = post_json(
            self._settings.base_url.rstrip("/") + "/api/chat",
            headers={},
            body=body,
            timeout=self._settings.timeout_seconds,
            max_bytes=max(64_000, self._settings.max_output_chars * 8),
        )
        message = response.get("message")
        if not isinstance(message, Mapping):
            raise ModelProtocolError("Ollama response has no message object")
        content = message.get("content")
        if not isinstance(content, str):
            raise ModelProtocolError("Ollama response has no text content")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ModelProtocolError("Ollama content is not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise ModelProtocolError("Ollama content must be a JSON object")
        return parse_model_payload(
            payload,
            provider="ollama",
            max_candidates=self._settings.max_candidates,
        )


def _schema_for_ollama() -> dict[str, object]:
    # Imported lazily to keep this module easy to inspect and to avoid exposing
    # a mutable shared schema object to provider-specific code.
    from companion_kernel.model_backend import MODEL_OUTPUT_SCHEMA

    return json.loads(json.dumps(MODEL_OUTPUT_SCHEMA))

