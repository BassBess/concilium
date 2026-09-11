"""Anthropic Messages API adapter (https://docs.anthropic.com)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ._util import estimate_cost, raise_for_http_status, safe_json
from .base import (
    ChatMessage,
    GenerateOptions,
    GenerationResult,
    MalformedResponseError,
    ProviderAdapter,
    ToolCall,
    ToolSpec,
    Usage,
    now_ms,
)
from .registry import register_provider

ANTHROPIC_VERSION = "2023-06-01"


@register_provider("anthropic")
class AnthropicAdapter(ProviderAdapter):
    display_name = "Anthropic Claude"
    default_base_url = "https://api.anthropic.com"
    docs_url = "https://docs.anthropic.com/en/api/getting-started"
    signup_url = "https://console.anthropic.com/settings/keys"

    def is_configured(self) -> bool:
        return bool(self.setting("api_key"))

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.setting("base_url") or self.default_base_url,
            headers={
                "x-api-key": self.setting("api_key", ""),
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    # -- message conversion --------------------------------------------------
    def _convert(self, messages: list[ChatMessage]) -> tuple[str, list[dict]]:
        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        out: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": m.tool_call_id or "toolu_0",
                             "content": m.content}
                        ],
                    }
                )
            elif m.role == "assistant" and m.tool_calls:
                out.append(
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                            for tc in m.tool_calls
                        ],
                    }
                )
            else:
                out.append({"role": m.role if m.role in ("user", "assistant") else "user",
                            "content": m.content})
        # Anthropic requires alternating roles — merge consecutive same-role msgs.
        merged: list[dict] = []
        for msg in out:
            if merged and merged[-1]["role"] == msg["role"] and isinstance(merged[-1]["content"], str):
                if isinstance(msg["content"], str):
                    merged[-1]["content"] += "\n\n" + msg["content"]
                    continue
            merged.append(msg)
        return "\n\n".join(system_parts), merged

    def _tools(self, tools: list[ToolSpec] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    def _payload(self, messages, model, options, stream):
        system, converted = self._convert(messages)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": options.max_tokens or 8192,
            "temperature": options.temperature,
            "messages": converted,
            "stream": stream,
        }
        if system:
            payload["system"] = system
        tools = self._tools(options.tools)
        if tools:
            payload["tools"] = tools
        if options.stop:
            payload["stop_sequences"] = options.stop
        return payload

    def _parse(self, data: dict, started: int, model: str) -> GenerationResult:
        content = data.get("content") or []
        text_parts, tool_calls = [], []
        for block in content:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.get("id", ""), name=block.get("name", ""), arguments=block.get("input", {})
                ))
        text = "".join(text_parts)
        if not text and not tool_calls:
            if data.get("type") == "error":
                raise MalformedResponseError(f"Anthropic error: {data.get('error')}")
            raise MalformedResponseError(f"Anthropic returned no content: {json.dumps(data)[:200]}")
        u = data.get("usage") or {}
        in_tok = u.get("input_tokens", 0)
        out_tok = u.get("output_tokens", 0)
        return GenerationResult(
            text=text, tool_calls=tool_calls, model=data.get("model", model), provider="anthropic",
            usage=Usage(in_tok, out_tok, estimate_cost("anthropic", model, in_tok, out_tok), u),
            finish_reason="tool_calls" if tool_calls else (data.get("stop_reason") or "stop"),
            latency_ms=now_ms() - started, raw=data,
        )

    async def generate(self, messages, model, options=None) -> GenerationResult:
        options = options or GenerateOptions()
        if not self.is_configured():
            from .base import AuthError
            raise AuthError("Anthropic is not configured (ANTHROPIC_API_KEY).")
        started = now_ms()
        async with self._client(options.timeout) as client:
            resp = await client.post("/v1/messages", json=self._payload(messages, model, options, False))
        if resp.status_code != 200:
            raise_for_http_status(resp.status_code, resp.text, resp.headers)
        return self._parse(safe_json(resp), started, model)

    async def stream(self, messages, model, options=None) -> AsyncIterator[str]:
        options = options or GenerateOptions()
        if not self.is_configured():
            from .base import AuthError
            raise AuthError("Anthropic is not configured (ANTHROPIC_API_KEY).")
        async with self._client(options.timeout) as client:
            async with client.stream("POST", "/v1/messages", json=self._payload(messages, model, options, True)) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise_for_http_status(resp.status_code, body.decode(errors="replace"), resp.headers)
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    try:
                        event = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield delta.get("text", "")
