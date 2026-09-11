# Provider guide

How providers work in Concilium and how to add one.

## Concepts

- **Provider type** — an adapter class registered with
  `@register_provider("key")` (e.g. `openai`, `ollama`, `openai_compatible`).
- **Provider instance** — a row in the `provider_configs` table. Most providers
  have one built-in (disabled until configured); the custom OpenAI-compatible
  type supports many instances added at runtime.
- **Model** — a `ModelInfo` (context window, output limit, capability tags,
  optional price estimates, free-tier flag, local/remote tier). Models come
  from either the static **catalog** or a provider's live `list_models()`
  endpoint (the two are merged).

Capability tags: `chat, vision, reasoning, tools, structured, code,
long_context, embeddings, image_gen, local`.

## Adapter interface (full reference)

```python
class ProviderAdapter:
    type_key: str
    display_name: str
    kind: str                     # "remote" | "local"
    docs_url: str
    signup_url: str
    secret_fields: tuple[str,...] # values encrypted at rest, never sent to UI
    setting_fields: tuple[str,...]
    default_base_url: str
    supports_multiple_instances: bool

    def __init__(instance_id, label, settings, enabled)
    def is_configured(self) -> bool
    async def list_models(self) -> list[ModelInfo]
    async def get_models(force_refresh=False) -> list[ModelInfo]
    async def generate(messages, model, options: GenerateOptions) -> GenerationResult
    async def stream(messages, model, options) -> AsyncIterator[str]
    async def health_check() -> {"status": ok|error|unconfigured, "models": n, "detail"}
    def get_capabilities(model_id) -> set[Capability]
    def get_limits(model_id) -> {"context_window", "max_output"}
```

Supporting types: `ChatMessage(role, content, name, tool_calls, tool_call_id)`,
`GenerateOptions(temperature, max_tokens, tools, response_json, stop, timeout)`,
`GenerationResult(text, tool_calls, usage(input/output/cost), finish_reason,
latency_ms, raw)`, `ToolSpec(name, description, parameters JSON schema)`,
`ToolCall(id, name, arguments)` (see `app/providers/base.py`).

### Error contract (important)

Raise only typed errors — the resilient runner reacts to them:

| Exception | Meaning | Runner behavior |
|---|---|---|
| `AuthError` (401/403) | bad/missing key | record, fail over |
| `RateLimitError` (429, quota text) | legitimate limit hit | cooldown (honor `retry_after`), fail over |
| `ModelNotFoundError` (404 model) | unknown model | record, fail over |
| `MalformedResponseError` | unparseable/empty body | retry w/ backoff, then fail over |
| `ProviderUnavailableError` (5xx/network) | transient | retry w/ backoff, cooldown after repeats |

`_util.raise_for_http_status()` performs this mapping for HTTP adapters and
already detects quota-style 400 responses. **Never** retry around a 429 or try
to rotate credentials to dodge a limit — the cooldown/failover path is the
intended, ToS-respecting behavior.

## Example 1: OpenAI-compatible API (most common)

```python
# app/providers/deepinfra.py
from .openai_compatible import OpenAICompatibleAdapter
from .registry import register_provider

@register_provider("deepinfra")
class DeepInfraAdapter(OpenAICompatibleAdapter):
    display_name = "DeepInfra"
    default_base_url = "https://api.deepinfra.com/v1/openai"
    signup_url = "https://deepinfra.com/dash/api_keys"
    docs_url = "https://deepinfra.com/docs"
```

That's enough: chat, streaming, tool calling, live `/models` discovery,
catalog merge and error mapping are inherited. Add static metadata in
`catalog.py` under `"deepinfra"` (prices/context/capabilities) and the provider
appears everywhere — wizard, pickers, routing, quotas, usage. Add the env-key
mapping in `app/config.py::PROVIDER_ENV_KEYS` if you want env-var credentials.

## Example 2: native HTTP API

```python
@register_provider("acme")
class AcmeAdapter(ProviderAdapter):
    display_name = "Acme AI"
    default_base_url = "https://api.acme.ai"
    secret_fields = ("api_key",)

    def is_configured(self): return bool(self.setting("api_key"))

    async def list_models(self):
        static = {m.id: m for m in await super().list_models()}
        if self.is_configured():
            async with httpx.AsyncClient(...) as c:
                r = await c.get("/models")
            for item in r.json()["models"]:
                static.setdefault(item["id"], ModelInfo(
                    id=item["id"], provider="acme",
                    context_window=item.get("context", 32768),
                    capabilities=["chat", "tools"]))
        return list(static.values())

    async def generate(self, messages, model, options):
        started = now_ms()
        payload = self._build(messages, model, options)  # your format mapping
        async with httpx.AsyncClient(base_url=self.base_url(), timeout=options.timeout,
                                     headers={"Authorization": f"Bearer {self.setting('api_key')}"}) as c:
            r = await c.post("/v1/chat", json=payload)
        if r.status_code != 200:
            raise_for_http_status(r.status_code, r.text, r.headers)
        data = r.json()
        return GenerationResult(
            text=data["choices"][0]["message"]["content"],
            tool_calls=...,            # map native tool calls if supported
            model=model, provider="acme",
            usage=Usage(data["usage"]["input_tokens"], data["usage"]["output_tokens"],
                        estimate_cost("acme", model, i, o)),
            latency_ms=now_ms() - started, raw=data)
```

Mapping notes:
- System instructions: some APIs (Anthropic, Cohere v2, Gemini) take a
  top-level `system` parameter rather than a message — move it out.
- Tool calling: convert `ToolSpec` to the vendor schema and vendor responses
  back into `ToolCall(id, name, arguments: dict)`.
- Streaming: parse the vendor's SSE frames in `stream()` (see Anthropic/Gemini
  adapters). If omitted, the base implementation falls back to `generate`.

## Example 3: local process

See `ollama.py`: no secret fields, `is_configured()` true when the URL is set,
`health_check()` distinguishes daemon-down from no-models-pulled, and token
counts come from local API stats (or char estimates).

## Registration / discovery checklist

1. File exists in `app/providers/` and imports `register_provider`.
2. (Optional) catalog entries in `catalog.py`.
3. (Optional) `PROVIDER_ENV_KEYS` entry in `app/config.py`.
4. (Optional, built-in single instance) add the type to
   `BUILTIN_PROVIDER_TYPES` in `app/seed.py` — then restart (seed is idempotent).
5. Tests with a fake transport: never hit real APIs in tests; use the mock
   provider patterns.

## Testing an adapter manually

```bash
# UI: Providers → Test / Discover models
curl -X POST localhost:8000/api/providers   # create instance
curl      localhost:8000/api/providers/{id}/health
curl      "localhost:8000/api/providers/{id}/models?refresh=1"
```

## What NOT to do

- No headless browsers driving consumer chat sites.
- No CAPTCHA/paywall/quota bypass, no credential sharing tricks.
- Don't swallow errors or return fake "successful" text — raise the typed error
  so failover does its job (the mock provider is the only allowed simulated
  backend, and it is explicitly labeled).
