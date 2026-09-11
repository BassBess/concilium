"""Cohere adapter (Chat API v2). Free trial keys at https://dashboard.cohere.com."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ._util import estimate_cost
from .base import (
    AuthError,
    ChatMessage,
    GenerateOptions,
    GenerationResult,
    MalformedResponseError,
    ModelInfo,
    ProviderAdapter,
    ProviderUnavailableError,
    RateLimitError,
    ToolCall,
    ToolSpec,
    Usage,
    now_ms,
)
from .registry import register_provider


@register_provider("cohere")
class CohereAdapter(ProviderAdapter):
    display_name = "Cohere"
    default_base_url = "https://api.cohere.com"
    docs_url = "https://docs.cohere.com/docs/the-cohere-platform"
    signup_url = "https://dashboard.cohere.com/api-keys"

    def is_configured(self) -> bool:
        return bool(self.setting("api_key"))

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.setting("base_url") or self.default_base_url,
            headers={"Authorization": f"Bearer {self.setting('api_key', '')}",
                     "Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    async def list_models(self) -> list[ModelInfo]:
        static = {m.id: m for m in await super().list_models()}
        if not self.is_configured():
            return list(static.values())
        try:
            async with self._client(20.0) as client:
                resp = await client.get("/v1/models?page_size=100")
            if resp.status_code == 200:
                for item in resp.json().get("models", []):
                    mid = item.get("name") or item.get("model_id")
                    if mid and mid not in static and item.get("endpoints") and "chat" in item["endpoints"]:
                        static[mid] = ModelInfo(
                            id=mid, provider="cohere", name=mid,
                            context_window=item.get("context_length") or 128_000,
                            capabilities=["chat", "tools", "structured"], free_tier=True,
                        )
        except Exception:
            pass
        return list(static.values())

    def _payload(self, messages: list[ChatMessage], model: str, options: GenerateOptions, stream: bool) -> dict[str, Any]:
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        chat_messages = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                chat_messages.append({"role": "user", "content": f"[tool:{m.name or 'result'}] {m.content}"})
            elif m.role in ("user", "assistant"):
                chat_messages.append({"role": m.role, "content": m.content or "(empty)"})
        payload: dict[str, Any] = {"model": model, "messages": chat_messages, "stream": stream}
        if system:
            payload["system"] = system
        if options.max_tokens:
            payload["max_tokens"] = options.max_tokens
        payload["temperature"] = options.temperature
        if options.tools:
            payload["tools"] = [
                {"type": "function",
                 "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
                for t in options.tools
            ]
        return payload

    def _parse(self, data: dict, started: int, model: str) -> GenerationResult:
        if data.get("message"):
            msg = data["message"]
            texts = [c.get("text", "") for c in msg.get("content", []) if c.get("type") == "text"]
            calls = []
            for tc in msg.get("tool_calls") or []:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=tc.get("id", fn.get("name", "call")), name=fn.get("name", ""), arguments=args))
            text = "\n".join(t for t in texts if t)
        else:
            text, calls = data.get("text", ""), []
        if not text and not calls:
            raise MalformedResponseError(f"Cohere returned no content: {json.dumps(data)[:200]}")
        u = ((data.get("usage") or {}).get("billed_units") or (data.get("usage") or {}).get("tokens") or {})
        in_tok = u.get("input_tokens", 0) or 0
        out_tok = u.get("output_tokens", 0) or 0
        return GenerationResult(
            text=text, tool_calls=calls, model=model, provider="cohere",
            usage=Usage(in_tok, out_tok, estimate_cost("cohere", model, in_tok, out_tok), data.get("usage", {})),
            latency_ms=now_ms() - started, raw=data,
        )

    def _check(self, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            raise RateLimitError("Cohere rate limit reached (trial keys are tightly limited).",
                                 retry_after=float(resp.headers.get("retry-after") or 60))
        if resp.status_code in (401, 403):
            raise AuthError(f"Cohere auth failed ({resp.status_code})")
        if resp.status_code == 404:
            raise MalformedResponseError(f"Cohere model/endpoint not found: {resp.text[:200]}")
        if resp.status_code >= 500:
            raise ProviderUnavailableError(f"Cohere server error {resp.status_code}")
        if resp.status_code != 200:
            raise MalformedResponseError(f"Cohere HTTP {resp.status_code}: {resp.text[:300]}")

    async def generate(self, messages, model, options=None) -> GenerationResult:
        options = options or GenerateOptions()
        if not self.is_configured():
            raise AuthError("Cohere is not configured (COHERE_API_KEY).")
        started = now_ms()
        async with self._client(options.timeout) as client:
            resp = await client.post("/v2/chat", json=self._payload(messages, model, options, False))
        self._check(resp)
        return self._parse(resp.json(), started, model)

    async def stream(self, messages, model, options=None) -> AsyncIterator[str]:
        options = options or GenerateOptions()
        if not self.is_configured():
            raise AuthError("Cohere is not configured (COHERE_API_KEY).")
        async with self._client(options.timeout) as client:
            async with client.stream("POST", "/v2/chat", json=self._payload(messages, model, options, True)) as resp:
                self._check(resp)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        evt = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if evt.get("type") == "content-delta":
                        piece = (evt.get("delta") or {}).get("message", {}).get("content", {}).get("text")
                        if piece:
                            yield piece
                    elif evt.get("type") == "text-generation":
                        if evt.get("text"):
                            yield evt["text"]
