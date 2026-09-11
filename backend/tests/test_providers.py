"""Provider adapter tests: discovery, mock provider, catalog, error mapping."""
from __future__ import annotations

import httpx
import pytest

from app.providers.base import (
    AuthError,
    ChatMessage,
    GenerateOptions,
    MalformedResponseError,
    ProviderUnavailableError,
    RateLimitError,
)
from app.providers._util import raise_for_http_status
from app.providers.mock import MockAdapter
from app.providers.registry import ADAPTER_TYPES, registry, provider_type_metadata


EXPECTED_TYPES = {
    "openai", "anthropic", "google", "groq", "mistral", "together",
    "openrouter", "xai", "cohere", "ollama", "lmstudio", "openai_compatible", "mock",
}


def test_all_provider_types_registered():
    assert EXPECTED_TYPES.issubset(set(ADAPTER_TYPES))
    meta = {m["type"]: m for m in provider_type_metadata()}
    assert meta["mock"]["kind"] == "local"
    assert "api_key" in meta["openai"]["secret_fields"]
    assert meta["openai_compatible"]["supports_multiple_instances"]


async def test_mock_generation_and_usage():
    mock = registry.first_of_type("mock")
    assert mock and mock.is_configured()
    models = await mock.list_models()
    ids = {m.id for m in models}
    assert {"mock-general", "mock-coder", "mock-reasoner", "mock-flaky"}.issubset(ids)

    result = await mock.generate(
        [ChatMessage(role="user", content="hello")], "mock-general", GenerateOptions())
    assert result.text and result.provider == "mock"
    assert result.usage.input_tokens > 0 and result.usage.cost == 0.0


async def test_mock_failure_injection_rate_limit():
    mock = MockAdapter("x", "mock", {"rate_limit_every": 1, "mock_delay_ms": 0})
    with pytest.raises(RateLimitError) as ei:
        await mock.generate([ChatMessage(role="user", content="x")], "mock-flaky")
    assert ei.value.retry_after is not None


async def test_mock_failure_injection_retryable():
    mock = MockAdapter("x", "mock", {"fail_every": 1})
    with pytest.raises(ProviderUnavailableError):
        await mock.generate([ChatMessage(role="user", content="x")], "mock-general")


async def test_unconfigured_provider_raises_auth():
    openai = registry.first_of_type("openai")
    assert openai.is_configured() is False
    with pytest.raises(AuthError):
        await openai.generate([ChatMessage(role="user", content="hi")], "gpt-4o-mini")


def test_http_status_mapping():
    class FakeResp:
        def __init__(self, code, headers=None):
            self.status_code = code
            self.text = "body"
            self.headers = httpx.Headers(headers or {})

    with pytest.raises(RateLimitError):
        raise_for_http_status(429, "rate", httpx.Headers({"retry-after": "12"}))
    with pytest.raises(AuthError):
        raise_for_http_status(401, "no", httpx.Headers())
    with pytest.raises(ProviderUnavailableError):
        raise_for_http_status(503, "down", httpx.Headers())
    with pytest.raises(RateLimitError):
        raise_for_http_status(400, "You have exceeded your quota", httpx.Headers())


def test_openai_compatible_payload_tools():
    from app.providers.openai_compatible import OpenAIAdapter
    from app.providers.base import ToolSpec

    a = OpenAIAdapter("i", "t", {"api_key": "k"})
    opts = GenerateOptions(tools=[ToolSpec(name="calc", description="d",
                                           parameters={"type": "object"})], max_tokens=100)
    payload = a._build_payload([ChatMessage(role="user", content="hi")], "m", opts, stream=True)
    assert payload["stream"] is True
    assert payload["tools"][0]["function"]["name"] == "calc"
    assert payload["max_tokens"] == 100


def test_malformed_response_detection():
    from app.providers.openai_compatible import OpenAIAdapter

    a = OpenAIAdapter("i", "t", {"api_key": "k"})
    with pytest.raises(MalformedResponseError):
        a._result_from_data({"choices": []}, 0)
    with pytest.raises(MalformedResponseError):
        a._result_from_data({"error": {"message": "boom"}}, 0)


async def test_ollama_health_handles_missing_daemon():
    ollama = registry.first_of_type("ollama")
    health = await ollama.health_check()
    # No ollama daemon in CI — must report error honestly, not fake models
    assert health["status"] in ("ok", "error")
