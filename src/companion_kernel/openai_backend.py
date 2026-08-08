"""OpenAI Responses API backend for Codex and other compatible models."""

import json
import os
from typing import Mapping

from companion_kernel.config import ModelSettings
from companion_kernel.model_backend import (
    MODEL_OUTPUT_SCHEMA,
    SYSTEM_INSTRUCTIONS,
    BackendConfigurationError,
    ModelContext,
    ModelProtocolError,
    ModelTurn,
    _parse_json_text,
    parse_model_payload,
    post_json,
)


class OpenAIResponsesBackend:
    """Use a Responses API model as a proposal generator.

    The runtime remains local, but request content is sent to the configured
    provider. Use OllamaBackend when the entire inference path must stay local.
    """

    def __init__(self, settings: ModelSettings) -> None:
        if settings.provider != "openai_responses":
            raise BackendConfigurationError(
                "OpenAIResponsesBackend requires provider='openai_responses'"
            )
        if not settings.model.strip():
            raise BackendConfigurationError("OpenAI model name is not configured")
        self._settings = settings

    def propose(self, context: ModelContext) -> ModelTurn:
        api_key = os.environ.get(self._settings.api_key_env)
        if not api_key:
            raise BackendConfigurationError(
                f"environment variable {self._settings.api_key_env} is not set"
            )
        body: dict[str, object] = {
            "model": self._settings.model,
            "instructions": SYSTEM_INSTRUCTIONS,
            "input": json.dumps(
                context.to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                allow_nan=False,
            ),
            "store": False,
            "max_output_tokens": self._settings.max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "companion_agent_output",
                    "strict": True,
                    "schema": MODEL_OUTPUT_SCHEMA,
                }
            },
        }
        response = post_json(
            self._settings.base_url.rstrip("/") + "/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            body=body,
            timeout=self._settings.timeout_seconds,
            max_bytes=max(64_000, self._settings.max_output_chars * 8),
        )
        content = _response_text(response)
        payload = _parse_json_text(content)
        return parse_model_payload(
            payload,
            provider="openai_responses",
            max_candidates=self._settings.max_candidates,
        )


def _response_text(response: Mapping[str, object]) -> str:
    output_text = response.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = response.get("output")
    if not isinstance(output, list):
        raise ModelProtocolError("Responses API response has no output text")
    for item in output:
        if not isinstance(item, Mapping):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for content_item in content:
            if not isinstance(content_item, Mapping):
                continue
            text = content_item.get("text")
            if isinstance(text, str) and text.strip():
                return text
            if isinstance(text, Mapping):
                value = text.get("value")
                if isinstance(value, str) and value.strip():
                    return value
    raise ModelProtocolError("Responses API response has no usable text output")
