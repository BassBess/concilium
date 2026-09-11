"""Ollama local model adapter (native API, no API key)."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ._util import raise_for_http_status, safe_json
from .base import (
    Capability,
    ChatMessage,
    GenerateOptions,
    GenerationResult,
    MalformedResponseError,
    ModelInfo,
    ProviderAdapter,
    Usage,
    now_ms,
)
from .registry import register_provider


def _ollama_caps(model_id: str) -> list[str]:
    mid = model_id.lower()
    caps = [Capability.CHAT.value, Capability.LOCAL.value]
    if any(k in mid for k in ("llava", "vision", "moondream", "vl", "granite3.2-vision", "minicpm-v")):
        caps.append(Capability.VISION.value)
    if any(k in mid for k in ("r1", "reason", "think", "qwq", "gpt-oss")):
        caps.append(Capability.REASONING.value)
    if any(k in mid for k in ("coder", "code", "qwen2.5-coder", "starcoder")):
        caps.append(Capability.CODE.value)
    if any(k in mid for k in ("llama3", "qwen", "mistral", "gpt-oss", "gemma", "phi")):
        caps += [Capability.TOOLS.value, Capability.STRUCTURED.value]
    return caps


@register_provider("ollama")
class OllamaAdapter(ProviderAdapter):
    display_name = "Ollama (local)"
    default_base_url = "http://localhost:11434"
    kind = "local"
    allow_without_key = True
    docs_url = "https://github.com/ollama/ollama/blob/main/docs/api.md"
    secret_fields: tuple[str, ...] = ()
    setting_fields = ("base_url",)

    def is_configured(self) -> bool:
        return bool(self.setting("base_url") or self.default_base_url)

    def base_url(self) -> str:
        return (self.setting("base_url") or self.default_base_url).rstrip("/")

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url(), timeout=httpx.Timeout(timeout, connect=5.0)
        )

    async def list_models(self) -> list[ModelInfo]:
        try:
            async with self._client(10.0) as client:
                resp = await client.get("/api/tags")
            if resp.status_code != 200:
                return []
            data = safe_json(resp)
        except Exception:
            return []
        out: list[ModelInfo] = []
        for item in data.get("models", []):
            mid = item.get("name") or item.get("model")
            if not mid:
                continue
            params = (item.get("details") or {}).get("parameter_size", "")
            out.append(
                ModelInfo(
                    id=mid,
                    provider="ollama",
                    name=mid,
                    context_window=item.get("context_window") or 32_768,
                    capabilities=_ollama_caps(mid),
                    tier="local",
                    free_tier=True,
                    family=mid.split(":")[0],
                    description=f"Local Ollama model {params}".strip(),
                )
            )
        return out

    async def health_check(self) -> dict[str, Any]:
        if not self.is_configured():
            return {"status": "unconfigured", "models": 0, "detail": "Set OLLAMA_BASE_URL"}
        try:
            models = await self.list_models()
            if not models:
                return {"status": "error", "models": 0,
                        "detail": "Ollama reachable but no models pulled (run: ollama pull llama3.1)"}
            return {"status": "ok", "models": len(models), "detail": f"{len(models)} local models"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "models": 0,
                    "detail": f"Cannot reach Ollama at {self.base_url()}: {exc}"}

    def _payload(self, messages: list[ChatMessage], model: str, options: GenerateOptions, stream: bool) -> dict:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in messages if m.role in ("system", "user", "assistant")
            ],
            "stream": stream,
            "options": {"temperature": options.temperature},
        }
        if options.max_tokens:
            payload["options"]["num_predict"] = options.max_tokens
        return payload

    async def generate(self, messages: list[ChatMessage], model: str, options: GenerateOptions | None = None) -> GenerationResult:
        options = options or GenerateOptions()
        started = now_ms()
        async with self._client(options.timeout) as client:
            resp = await client.post("/api/chat", json=self._payload(messages, model, options, False))
        if resp.status_code != 200:
            raise_for_http_status(resp.status_code, resp.text, resp.headers)
        data = safe_json(resp)
        text = (data.get("message") or {}).get("content", "")
        if not text:
            raise MalformedResponseError(f"Ollama returned no content: {json.dumps(data)[:200]}")
        in_tok = data.get("prompt_eval_count", 0) or 0
        out_tok = data.get("eval_count", 0) or max(1, len(text) // 4)
        return GenerationResult(
            text=text, model=model, provider="ollama",
            usage=Usage(in_tok, out_tok, 0.0),
            latency_ms=now_ms() - started, raw=data,
        )

    async def stream(
        self, messages: list[ChatMessage], model: str, options: GenerateOptions | None = None
    ) -> AsyncIterator[str]:
        options = options or GenerateOptions()
        async with self._client(options.timeout) as client:
            async with client.stream("POST", "/api/chat", json=self._payload(messages, model, options, True)) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise_for_http_status(resp.status_code, body.decode(errors="replace"), resp.headers)
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    piece = (data.get("message") or {}).get("content")
                    if piece:
                        yield piece
