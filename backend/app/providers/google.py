"""Google Gemini (Generative Language API) adapter.

Uses the free-tier-enabled AI Studio key: https://aistudio.google.com/app/apikey
"""
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


@register_provider("google")
class GoogleAdapter(ProviderAdapter):
    display_name = "Google Gemini"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta"
    docs_url = "https://ai.google.dev/gemini-api/docs"
    signup_url = "https://aistudio.google.com/app/apikey"

    def is_configured(self) -> bool:
        return bool(self.setting("api_key"))

    def base_url(self) -> str:
        return (self.setting("base_url") or self.default_base_url).rstrip("/")

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url(),
            params={"key": self.setting("api_key", "")},
            headers={"Content-Type": "application/json"},
            timeout=httpx.Timeout(timeout, connect=15.0),
        )

    async def list_models(self) -> list[ModelInfo]:
        static = {m.id: m for m in await super().list_models()}
        if not self.is_configured():
            return list(static.values())
        try:
            async with self._client(20.0) as client:
                resp = await client.get("/models")
            if resp.status_code != 200:
                return list(static.values())
            for item in resp.json().get("models", []):
                name = (item.get("name") or "").removeprefix("models/")
                methods = item.get("supportedGenerationMethods") or []
                if not name or "generateContent" not in methods:
                    continue
                if name not in static and not name.endswith("-vision"):
                    static[name] = ModelInfo(
                        id=name, provider="google", name=name, context_window=item.get("inputTokenLimit") or 32_768,
                        max_output=item.get("outputTokenLimit") or 8192,
                        capabilities=["chat", "tools", "structured"] + (
                            ["vision"] if "vision" in name.lower() or item.get("inputImageTokens") else []),
                        free_tier=True,
                    )
        except Exception:
            return list(static.values())
        return list(static.values())

    # -- conversion ----------------------------------------------------------
    def _contents(self, messages: list[ChatMessage]) -> tuple[dict | None, list[dict]]:
        system = None
        sys_text = "\n\n".join(m.content for m in messages if m.role == "system" and m.content)
        if sys_text:
            system = {"parts": [{"text": sys_text}]}
        contents: list[dict] = []
        for m in messages:
            if m.role == "system":
                continue
            role = "model" if m.role == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.content or "(empty)"}]})
        return system, contents

    def _tools(self, tools: list[ToolSpec] | None) -> list[dict] | None:
        if not tools:
            return None
        return [
            {
                "functionDeclarations": [
                    {"name": t.name, "description": t.description, "parameters": t.parameters}
                    for t in tools
                ]
            }
        ]

    def _payload(self, messages, options: GenerateOptions, stream: bool) -> dict[str, Any]:
        system, contents = self._contents(messages)
        gen_config: dict[str, Any] = {"temperature": options.temperature}
        if options.max_tokens:
            gen_config["maxOutputTokens"] = options.max_tokens
        if options.response_json:
            gen_config["responseMimeType"] = "application/json"
        payload: dict[str, Any] = {"contents": contents, "generationConfig": gen_config}
        if system:
            payload["systemInstruction"] = system
        tools = self._tools(options.tools)
        if tools:
            payload["tools"] = tools
        return payload

    def _check_error(self, status: int, text: str) -> None:
        if status == 429 or "quota" in text.lower() or "rate limit" in text.lower():
            raise RateLimitError(f"Gemini quota/rate limit: {text[:300]}")
        if status in (400, 401, 403) and ("api key" in text.lower() or "permission" in text.lower()):
            raise AuthError(f"Gemini auth error: {text[:300]}")
        if status >= 500:
            raise ProviderUnavailableError(f"Gemini server error {status}: {text[:200]}")
        if status != 200:
            raise MalformedResponseError(f"Gemini HTTP {status}: {text[:300]}")

    def _parse_candidate(self, data: dict, started: int, model: str) -> GenerationResult:
        if data.get("error"):
            raise MalformedResponseError(f"Gemini error: {json.dumps(data['error'])[:300]}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise MalformedResponseError(f"Gemini returned no candidates: {json.dumps(data)[:200]}")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        texts, calls = [], []
        for p in parts:
            if "text" in p:
                texts.append(p["text"])
            if "functionCall" in p:
                fc = p["functionCall"]
                calls.append(ToolCall(id=fc.get("name", "call"), name=fc.get("name", ""),
                                      arguments=fc.get("args") or {}))
        text = "".join(texts)
        if not text and not calls:
            raise MalformedResponseError("Gemini candidate had no text/function parts")
        um = data.get("usageMetadata") or {}
        in_tok = um.get("promptTokenCount", 0)
        out_tok = um.get("candidatesTokenCount", 0)
        return GenerationResult(
            text=text, tool_calls=calls, model=model, provider="google",
            usage=Usage(in_tok, out_tok, estimate_cost("google", model, in_tok, out_tok), um),
            finish_reason=(candidates[0].get("finishReason") or "STOP").lower(),
            latency_ms=now_ms() - started, raw=data,
        )

    async def generate(self, messages, model, options=None) -> GenerationResult:
        options = options or GenerateOptions()
        if not self.is_configured():
            raise AuthError("Gemini is not configured (GOOGLE_API_KEY).")
        started = now_ms()
        async with self._client(options.timeout) as client:
            resp = await client.post(f"/models/{model}:generateContent",
                                     json=self._payload(messages, options, False))
        self._check_error(resp.status_code, resp.text)
        return self._parse_candidate(resp.json(), started, model)

    async def stream(self, messages, model, options=None) -> AsyncIterator[str]:
        options = options or GenerateOptions()
        if not self.is_configured():
            raise AuthError("Gemini is not configured (GOOGLE_API_KEY).")
        async with self._client(options.timeout) as client:
            async with client.stream(
                "POST", f"/models/{model}:streamGenerateContent?alt=sse",
                json=self._payload(messages, options, True),
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    self._check_error(resp.status_code, body.decode(errors="replace"))
                    return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    for c in data.get("candidates", []):
                        for p in (c.get("content") or {}).get("parts", []):
                            if p.get("text"):
                                yield p["text"]
