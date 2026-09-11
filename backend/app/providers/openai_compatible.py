"""Base adapter for OpenAI-compatible Chat Completions APIs.

Powers: OpenAI, Groq, Together, OpenRouter, xAI, Mistral, LM Studio and any
user-defined OpenAI-compatible endpoint (llama.cpp, vLLM, LocalAI, ...).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ._util import estimate_cost, raise_for_http_status, safe_json
from .base import (
    Capability,
    ChatMessage,
    GenerationResult,
    GenerateOptions,
    MalformedResponseError,
    ModelInfo,
    ProviderAdapter,
    RateLimitError,
    ToolCall,
    ToolSpec,
    now_ms,
)


def _heuristic_capabilities(model_id: str) -> list[str]:
    mid = model_id.lower()
    caps = [Capability.CHAT.value]
    if any(k in mid for k in ("vision", "gpt-4o", "gpt-4.1", "gemini", "vl", "multimodal", "claude", "grok")):
        caps.append(Capability.VISION.value)
    if any(k in mid for k in ("r1", "reason", "o1", "o3", "o4", "gpt-oss", "think")):
        caps.append(Capability.REASONING.value)
    if any(k in mid for k in ("coder", "code", "codestral", "qwen2.5-coder")):
        caps.append(Capability.CODE.value)
    if any(k in mid for k in ("gpt-4", "gpt-oss", "llama-3", "mistral", "qwen", "grok", "gemini", "claude")):
        caps += [Capability.TOOLS.value, Capability.STRUCTURED.value]
    return caps


class OpenAICompatibleAdapter(ProviderAdapter):
    """Base class — concrete providers set ``default_base_url`` and key flags."""

    default_base_url = "https://api.openai.com/v1"
    allow_without_key = False
    extra_headers: dict[str, str] = {}

    def base_url(self) -> str:
        return (self.setting("base_url") or self.default_base_url).rstrip("/")

    def is_configured(self) -> bool:
        if self.setting("api_key"):
            return True
        return bool(self.allow_without_key and self.base_url())

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json", **self.extra_headers}
        key = self.setting("api_key")
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url(),
            headers=self._headers(),
            timeout=httpx.Timeout(timeout, connect=15.0),
            follow_redirects=True,
        )

    # -- model listing -------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        static = {m.id: m for m in await super().list_models()}
        if not self.is_configured():
            return list(static.values())
        try:
            async with self._client(20.0) as client:
                resp = await client.get("/models")
            if resp.status_code != 200:
                return list(static.values())
            data = safe_json(resp)
        except Exception:
            return list(static.values())
        live_ids = [item["id"] for item in data.get("data", []) if item.get("id")]
        for mid in live_ids:
            if mid not in static:
                static[mid] = ModelInfo(
                    id=mid, provider=self.type_key, name=mid,
                    context_window=32_768, capabilities=_heuristic_capabilities(mid),
                    tier=self.kind, free_tier=mid.endswith(":free"),
                )
        return list(static.values())

    # -- payload -------------------------------------------------------------
    def _build_payload(self, messages: list[ChatMessage], model: str, options: GenerateOptions, stream: bool):
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.to_openai() for m in messages],
            "temperature": options.temperature,
            "stream": stream,
        }
        if options.max_tokens:
            # Some newer APIs prefer max_completion_tokens; max_tokens remains
            # the universally supported field across OpenAI-compatible servers.
            payload["max_tokens"] = options.max_tokens
        if options.stop:
            payload["stop"] = options.stop
        if options.response_json:
            payload["response_format"] = {"type": "json_object"}
        if options.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in options.tools
            ]
            payload["tool_choice"] = "auto"
        return payload

    def _parse_tool_calls(self, choice: dict) -> list[ToolCall]:
        out = []
        for tc in choice.get("message", {}).get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments", "")}
            out.append(ToolCall(id=tc.get("id", fn.get("name", "call")), name=fn.get("name", ""), arguments=args, raw=tc))
        return out

    def _result_from_data(self, data: dict, started: int) -> GenerationResult:
        if not data.get("choices"):
            # Some providers return an error body with HTTP 200
            if data.get("error"):
                msg = data["error"].get("message", str(data["error"])) if isinstance(data["error"], dict) else str(data["error"])
                raise MalformedResponseError(f"Empty choices: {msg}")
            raise MalformedResponseError(f"Empty choices: {json.dumps(data)[:300]}")
        choice = data["choices"][0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        in_tok = usage.get("prompt_tokens", 0) or 0
        out_tok = usage.get("completion_tokens", 0) or 0
        return GenerationResult(
            text=msg.get("content") or "",
            tool_calls=self._parse_tool_calls(choice),
            model=data.get("model", ""),
            provider=self.type_key,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=now_ms() - started,
            raw=data,
        )

    async def generate(self, messages: list[ChatMessage], model: str, options: GenerateOptions | None = None) -> GenerationResult:
        options = options or GenerateOptions()
        if not self.is_configured():
            from .base import AuthError

            raise AuthError(f"{self.display_name} is not configured (missing credential/endpoint).")
        payload = self._build_payload(messages, model, options, stream=False)
        started = now_ms()
        async with self._client(options.timeout) as client:
            resp = await client.post("/chat/completions", json=payload)
        if resp.status_code != 200:
            raise_for_http_status(resp.status_code, resp.text, resp.headers)
        data = safe_json(resp)
        result = self._result_from_data(data, started)
        usage = data.get("usage") or {}
        result.usage.input_tokens = usage.get("prompt_tokens", 0) or 0
        result.usage.output_tokens = usage.get("completion_tokens", 0) or 0
        result.usage.cost = estimate_cost(self.type_key, model, result.usage.input_tokens, result.usage.output_tokens)
        result.usage.raw = usage
        return result

    async def stream(
        self, messages: list[ChatMessage], model: str, options: GenerateOptions | None = None
    ) -> AsyncIterator[str]:
        options = options or GenerateOptions()
        if not self.is_configured():
            from .base import AuthError

            raise AuthError(f"{self.display_name} is not configured (missing credential/endpoint).")
        payload = self._build_payload(messages, model, options, stream=True)
        async with self._client(options.timeout) as client:
            async with client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise_for_http_status(resp.status_code, body.decode(errors="replace"), resp.headers)
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    chunk = line[5:].strip()
                    if chunk == "[DONE]":
                        break
                    try:
                        data = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        yield piece


# ============================================================================
# Concrete OpenAI-compatible providers
# ============================================================================

from .registry import register_provider  # noqa: E402


@register_provider("openai")
class OpenAIAdapter(OpenAICompatibleAdapter):
    display_name = "OpenAI"
    default_base_url = "https://api.openai.com/v1"
    docs_url = "https://platform.openai.com/docs"
    signup_url = "https://platform.openai.com/api-keys"


@register_provider("groq")
class GroqAdapter(OpenAICompatibleAdapter):
    display_name = "Groq"
    default_base_url = "https://api.groq.com/openai/v1"
    docs_url = "https://console.groq.com/docs/quickstart"
    signup_url = "https://console.groq.com/keys"


@register_provider("together")
class TogetherAdapter(OpenAICompatibleAdapter):
    display_name = "Together AI"
    default_base_url = "https://api.together.xyz/v1"
    docs_url = "https://docs.together.ai/"
    signup_url = "https://api.together.xyz/settings/api-keys"


@register_provider("openrouter")
class OpenRouterAdapter(OpenAICompatibleAdapter):
    display_name = "OpenRouter"
    default_base_url = "https://openrouter.ai/api/v1"
    docs_url = "https://openrouter.ai/docs"
    signup_url = "https://openrouter.ai/keys"

    def _headers(self) -> dict[str, str]:
        h = super()._headers()
        h["HTTP-Referer"] = "https://github.com/concilium/app"
        h["X-Title"] = "Concilium"
        return h

    def _result_from_data(self, data: dict, started: int) -> GenerationResult:
        result = super()._result_from_data(data, started)
        # OpenRouter usage may be withheld or delayed; cost may appear directly.
        if not result.usage.raw and data.get("usage"):
            result.usage.raw = data["usage"]
        return result


@register_provider("xai")
class XAIAdapter(OpenAICompatibleAdapter):
    display_name = "xAI Grok"
    default_base_url = "https://api.x.ai/v1"
    docs_url = "https://docs.x.ai/"
    signup_url = "https://console.x.ai/"


@register_provider("mistral")
class MistralAdapter(OpenAICompatibleAdapter):
    display_name = "Mistral AI"
    default_base_url = "https://api.mistral.ai/v1"
    docs_url = "https://docs.mistral.ai/"
    signup_url = "https://console.mistral.ai/api-keys"


@register_provider("lmstudio")
class LMStudioAdapter(OpenAICompatibleAdapter):
    display_name = "LM Studio (local)"
    default_base_url = "http://localhost:1234/v1"
    kind = "local"
    allow_without_key = True
    docs_url = "https://lmstudio.ai/docs/app/api/endpoints/openai"

    def is_configured(self) -> bool:
        return bool(self.base_url())


@register_provider("openai_compatible")
class CustomOpenAICompatibleAdapter(OpenAICompatibleAdapter):
    display_name = "Custom OpenAI-compatible endpoint"
    default_base_url = "http://localhost:8080/v1"
    kind = "remote"
    allow_without_key = True
    supports_multiple_instances = True
    docs_url = "https://platform.openai.com/docs/api-reference"
    secret_fields = ("api_key",)
    setting_fields = ("base_url",)

    def is_configured(self) -> bool:
        return bool(self.setting("base_url"))
