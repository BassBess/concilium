"""Provider adapter interface.

Every AI backend (OpenAI, Anthropic, Ollama, ...) implements :class:`ProviderAdapter`.
Adapters are auto-discovered and registered by :mod:`app.providers.registry`.

To add a new provider:

1. Create ``app/providers/my_provider.py`` subclassing :class:`ProviderAdapter`.
2. Decorate it with ``@register_provider("my_provider")``.
3. Implement ``is_configured``, ``list_models`` and ``generate`` (and optionally
   ``stream`` / ``health_check``).
4. Add model metadata to :mod:`app.providers.catalog` (optional but recommended).

That is all — the REST API, UI, routing, council and quota systems pick it up
automatically.
"""
from __future__ import annotations

import enum
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


# ============================================================================
# Capabilities / data types
# ============================================================================

class Capability(str, enum.Enum):
    CHAT = "chat"
    VISION = "vision"
    REASONING = "reasoning"
    TOOLS = "tools"            # native function/tool calling
    STRUCTURED = "structured"  # reliable JSON output
    EMBEDDINGS = "embeddings"
    IMAGE_GEN = "image_gen"
    CODE = "code"
    LONG_CONTEXT = "long_context"
    LOCAL = "local"


@dataclass
class ModelInfo:
    id: str
    provider: str          # provider TYPE key (openai, ollama, ...)
    name: str = ""
    context_window: int = 8192
    max_output: int = 4096
    capabilities: list[str] = field(default_factory=list)
    input_price_per_1m: float | None = None    # USD per 1M input tokens; None = unknown
    output_price_per_1m: float | None = None
    tier: str = "remote"                       # remote | local
    free_tier: bool = False
    family: str = ""
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "provider": self.provider,
            "name": self.name or self.id,
            "context_window": self.context_window,
            "max_output": self.max_output,
            "capabilities": self.capabilities,
            "input_price_per_1m": self.input_price_per_1m,
            "output_price_per_1m": self.output_price_per_1m,
            "tier": self.tier,
            "free_tier": self.free_tier,
            "family": self.family,
            "description": self.description,
        }


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw: Any = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema


@dataclass
class GenerationResult:
    text: str
    model: str
    provider: str
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"
    latency_ms: int = 0
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChatMessage:
    role: str  # system | user | assistant | tool
    content: str = ""
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None

    def to_openai(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": __import__("json").dumps(tc.arguments)},
                }
                for tc in self.tool_calls
            ]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


@dataclass
class GenerateOptions:
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[ToolSpec] | None = None
    response_json: bool = False
    stop: list[str] | None = None
    timeout: float = 120.0


# ============================================================================
# Exceptions — the orchestrator reacts to these (retry / cooldown / failover)
# ============================================================================

class ProviderError(Exception):
    """Base class for all provider failures."""

    retryable: bool = True


class AuthError(ProviderError):
    """Invalid / missing credentials. Not retryable."""

    retryable = False


class RateLimitError(ProviderError):
    """The provider reported a legitimate rate limit / quota exhaustion."""

    retryable = False  # handled by cooldown + failover, not immediate retry

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ModelNotFoundError(ProviderError):
    retryable = False


class MalformedResponseError(ProviderError):
    """Provider returned an unparseable / empty response."""

    retryable = True


class ProviderUnavailableError(ProviderError):
    """Network / 5xx failure."""

    retryable = True


# ============================================================================
# Adapter base class
# ============================================================================

class ProviderAdapter:
    """Interface every provider implements.

    Class attributes describe the provider type to the registry/UI.
    """

    type_key: str = "base"
    display_name: str = "Base provider"
    kind: str = "remote"                     # remote | local
    docs_url: str = ""
    secret_fields: tuple[str, ...] = ("api_key",)
    setting_fields: tuple[str, ...] = ("base_url",)
    default_base_url: str = ""
    supports_multiple_instances: bool = False
    signup_url: str = ""

    def __init__(self, instance_id: str, label: str, settings: dict[str, Any], enabled: bool = True):
        self.instance_id = instance_id
        self.label = label
        self.settings = settings or {}
        self.enabled = enabled
        self.models_cache: list[ModelInfo] | None = None

    async def get_models(self, force_refresh: bool = False) -> list[ModelInfo]:
        """Return models, using a cached live listing when available."""
        if force_refresh or self.models_cache is None:
            try:
                self.models_cache = await self.list_models()
            except Exception:
                self.models_cache = self.sync_catalog()
        return self.models_cache

    async def model_info(self, model_id: str, force_refresh: bool = False) -> ModelInfo | None:
        for m in await self.get_models(force_refresh=force_refresh):
            if m.id == model_id:
                return m
        return None

    # -- credential / configuration ----------------------------------------
    def is_configured(self) -> bool:
        """Whether enough configuration exists to attempt requests."""
        raise NotImplementedError

    def setting(self, key: str, default: Any = None) -> Any:
        return self.settings.get(key, default)

    # -- model catalog -------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        """Return models exposed by this instance.

        Default implementation returns static catalog entries. Providers with
        an online model-listing endpoint should override and merge.
        """
        from .catalog import catalog_for_provider

        return catalog_for_provider(self.type_key)

    def get_capabilities(self, model_id: str) -> set[Capability]:
        for m in self.sync_catalog():
            if m.id == model_id:
                return {Capability(c) for c in m.capabilities}
        return {Capability.CHAT}

    def get_limits(self, model_id: str) -> dict[str, Any]:
        for m in self.sync_catalog():
            if m.id == model_id:
                return {"context_window": m.context_window, "max_output": m.max_output}
        return {"context_window": 8192, "max_output": 4096}

    def sync_catalog(self) -> list[ModelInfo]:
        from .catalog import catalog_for_provider

        return catalog_for_provider(self.type_key)

    # -- generation ----------------------------------------------------------
    async def generate(self, messages: list[ChatMessage], model: str, options: GenerateOptions) -> GenerationResult:
        raise NotImplementedError

    async def stream(
        self, messages: list[ChatMessage], model: str, options: GenerateOptions
    ) -> AsyncIterator[str]:  # pragma: no cover - default falls back to generate
        result = await self.generate(messages, model, options)
        if result.text:
            yield result.text

    # -- introspection -------------------------------------------------------
    async def health_check(self) -> dict[str, Any]:
        """Return ``{status: ok|error|unconfigured, models: n, detail}``."""
        if not self.is_configured():
            return {"status": "unconfigured", "models": 0, "detail": "No credentials configured"}
        try:
            models = await self.list_models()
            return {"status": "ok", "models": len(models), "detail": ""}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "models": 0, "detail": str(exc)[:300]}


def now_ms() -> int:
    return int(time.time() * 1000)
