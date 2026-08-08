from datetime import UTC, datetime
import json

import pytest

from companion_kernel.clock import FakeClock
from companion_kernel.config import ConfigActor, ConfigStore, ModelSettings, UserSettings
from companion_kernel.events import KernelEvent
from companion_kernel.kernel import PersonalityKernel
from companion_kernel.model_backend import (
    ModelContext,
    ModelProtocolError,
    parse_model_payload,
)
from companion_kernel.ollama_backend import OllamaBackend
from companion_kernel.openai_backend import OpenAIResponsesBackend
from companion_kernel.types import ActionKind, DriveKind, EventKind


START = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def context(tmp_path) -> ModelContext:
    clock = FakeClock(START)
    config = ConfigStore.defaults()
    config.replace_user(UserSettings(timezone="UTC"), ConfigActor.USER)
    kernel = PersonalityKernel.open(tmp_path, clock, config)
    event = KernelEvent("context-message", START, EventKind.USER_MESSAGE, {"message": "hello"})
    state = kernel.preview(event)
    urgencies = kernel.urgencies_for(state, START)
    return ModelContext(
        event=event,
        state=state,
        urgencies=tuple(sorted(urgencies.items(), key=lambda item: item[0].value)),
        user_message="hello",
        proactive_cycle=False,
        allowed_actions=tuple(ActionKind),
    )


def payload() -> dict[str, object]:
    return {
        "candidates": [
            {
                "id": "reply",
                "action": "send_message",
                "proactive": False,
                "draft_text": "你好，我在这里。",
                "expected_relief": [{"drive": "connection", "value": 0.6}],
                "relationship_health": 0.8,
                "value_alignment": 0.8,
                "intrusion_cost": 0.1,
                "risk": 0.0,
                "repetition": 0.0,
                "tool_requests": [],
            }
        ]
    }


def test_parser_marks_model_safety_as_unassessed() -> None:
    turn = parse_model_payload(payload(), provider="test", max_candidates=3)
    assert turn.provider == "test"
    assert turn.proposals[0].intent.safety.violations() == ("safety_unassessed",)
    assert turn.proposals[0].intent.expected_relief == ((DriveKind.CONNECTION, 0.6),)


def test_parser_rejects_too_many_candidates() -> None:
    raw = payload()
    raw["candidates"] = [payload()["candidates"][0]] * 2
    with pytest.raises(ModelProtocolError, match="too many"):
        parse_model_payload(raw, provider="test", max_candidates=1)


def test_ollama_backend_uses_local_chat_endpoint(monkeypatch, tmp_path) -> None:
    calls: dict[str, object] = {}

    def fake_post(url, *, headers, body, timeout, max_bytes):
        calls.update({"url": url, "headers": headers, "body": body, "timeout": timeout})
        return {"message": {"content": json.dumps(payload(), ensure_ascii=False)}}

    monkeypatch.setattr("companion_kernel.ollama_backend.post_json", fake_post)
    settings = ModelSettings(provider="ollama", model="local-instruct")
    turn = OllamaBackend(settings).propose(context(tmp_path))

    assert calls["url"] == "http://127.0.0.1:11434/api/chat"
    assert calls["body"]["stream"] is False
    assert turn.proposals[0].draft_text == "你好，我在这里。"


def test_openai_backend_uses_responses_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "test-key")
    calls: dict[str, object] = {}

    def fake_post(url, *, headers, body, timeout, max_bytes):
        calls.update({"url": url, "headers": headers, "body": body})
        return {"output_text": json.dumps(payload(), ensure_ascii=False)}

    monkeypatch.setattr("companion_kernel.openai_backend.post_json", fake_post)
    settings = ModelSettings(
        provider="openai_responses",
        model="gpt-5.3-codex",
        base_url="https://api.openai.com/v1",
        api_key_env="TEST_OPENAI_KEY",
    )
    turn = OpenAIResponsesBackend(settings).propose(context(tmp_path))

    assert calls["url"] == "https://api.openai.com/v1/responses"
    assert calls["headers"]["Authorization"] == "Bearer test-key"
    assert calls["body"]["store"] is False
    assert calls["body"]["text"]["format"]["type"] == "json_schema"
    assert turn.proposals[0].intent.action is ActionKind.SEND_MESSAGE

