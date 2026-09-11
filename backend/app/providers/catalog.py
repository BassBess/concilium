"""Static model catalog metadata.

Prices are list-price USD per 1,000,000 tokens at the time of writing and are
always treated as ESTIMATES; providers return live usage where available.
Models/ids change frequently — adapters that expose a model-list endpoint
(OpenAI, Groq, Together, OpenRouter, Ollama, LM Studio, ...) merge live data
on top of this catalog. Nothing here is ever faked: a model missing a key is
simply reported as unavailable.
"""
from __future__ import annotations

from .base import Capability as C
from .base import ModelInfo

CHAT = [C.CHAT.value]
CHAT_TOOLS = [C.CHAT.value, C.TOOLS.value, C.STRUCTURED.value]
VISION = CHAT_TOOLS + [C.VISION.value]
REASON = CHAT_TOOLS + [C.REASONING.value]
CODE = CHAT_TOOLS + [C.CODE.value]
LONG = [C.LONG_CONTEXT.value]


def _m(provider: str, **kw) -> ModelInfo:
    kw.setdefault("name", kw["id"])
    return ModelInfo(provider=provider, **kw)


CATALOG: dict[str, list[ModelInfo]] = {
    "openai": [
        _m("openai", id="gpt-4o", context_window=128_000, capabilities=VISION,
           input_price_per_1m=2.5, output_price_per_1m=10.0, family="gpt-4o"),
        _m("openai", id="gpt-4o-mini", context_window=128_000, capabilities=VISION,
           input_price_per_1m=0.15, output_price_per_1m=0.60, family="gpt-4o",
           free_tier=False),
        _m("openai", id="gpt-4.1", context_window=1_000_000, capabilities=VISION + LONG,
           input_price_per_1m=2.0, output_price_per_1m=8.0, family="gpt-4.1"),
        _m("openai", id="gpt-4.1-mini", context_window=1_000_000, capabilities=VISION + LONG,
           input_price_per_1m=0.40, output_price_per_1m=1.60, family="gpt-4.1"),
        _m("openai", id="o4-mini", context_window=200_000, capabilities=REASON + [C.CODE.value],
           input_price_per_1m=1.10, output_price_per_1m=4.40, family="o"),
        _m("openai", id="gpt-4.1-nano", context_window=1_000_000, capabilities=VISION + LONG,
           input_price_per_1m=0.10, output_price_per_1m=0.40, family="gpt-4.1"),
    ],
    "anthropic": [
        _m("anthropic", id="claude-opus-4-1-20250805", name="claude-opus-4.1",
           context_window=200_000, max_output=32_000, capabilities=VISION + LONG,
           input_price_per_1m=15.0, output_price_per_1m=75.0, family="claude-4"),
        _m("anthropic", id="claude-sonnet-4-5-20250929", name="claude-sonnet-4.5",
           context_window=200_000, max_output=64_000, capabilities=VISION + [C.CODE.value] + LONG,
           input_price_per_1m=3.0, output_price_per_1m=15.0, family="claude-4"),
        _m("anthropic", id="claude-3-5-haiku-latest", name="claude-3.5-haiku",
           context_window=200_000, capabilities=VISION,
           input_price_per_1m=0.80, output_price_per_1m=4.0, family="claude-3.5"),
    ],
    "google": [
        _m("google", id="gemini-2.5-flash", context_window=1_048_576, capabilities=VISION + LONG,
           input_price_per_1m=0.30, output_price_per_1m=2.50, free_tier=True, family="gemini-2.5",
           description="Free tier available via Google AI Studio"),
        _m("google", id="gemini-2.5-pro", context_window=1_048_576, capabilities=REASON + VISION + LONG,
           input_price_per_1m=1.25, output_price_per_1m=10.0, free_tier=True, family="gemini-2.5"),
        _m("google", id="gemini-2.0-flash", context_window=1_048_576, capabilities=VISION + LONG,
           input_price_per_1m=0.10, output_price_per_1m=0.40, free_tier=True, family="gemini-2.0"),
        _m("google", id="gemini-2.5-flash-lite", context_window=1_048_576, capabilities=VISION + LONG,
           input_price_per_1m=0.10, output_price_per_1m=0.40, free_tier=True, family="gemini-2.5"),
    ],
    "groq": [
        # Live model ids are fetched from /openai/v1/models — these are common anchors.
        _m("groq", id="llama-3.3-70b-versatile", context_window=128_000, capabilities=CHAT_TOOLS,
           free_tier=True, family="llama-3.3", description="Free Groq developer tier"),
        _m("groq", id="llama-3.1-8b-instant", context_window=128_000, capabilities=CHAT_TOOLS,
           free_tier=True, family="llama-3.1"),
        _m("groq", id="openai/gpt-oss-120b", context_window=128_000, capabilities=REASON + [C.CODE.value],
           free_tier=True, family="gpt-oss"),
        _m("groq", id="openai/gpt-oss-20b", context_window=128_000, capabilities=REASON + [C.CODE.value],
           free_tier=True, family="gpt-oss"),
        _m("groq", id="qwen/qwen-2.5-coder-32b", name="qwen-2.5-coder-32b", context_window=128_000,
           capabilities=CODE, free_tier=True, family="qwen-coder"),
    ],
    "mistral": [
        _m("mistral", id="mistral-small-latest", name="mistral-small", context_window=128_000,
           capabilities=CHAT_TOOLS, free_tier=True, input_price_per_1m=0.2, output_price_per_1m=0.6,
           family="mistral-small", description="Free experimentation tier available"),
        _m("mistral", id="mistral-large-latest", name="mistral-large", context_window=128_000,
           capabilities=CHAT_TOOLS, input_price_per_1m=2.0, output_price_per_1m=6.0, family="mistral-large"),
        _m("mistral", id="codestral-latest", name="codestral", context_window=256_000, capabilities=CODE,
           input_price_per_1m=0.3, output_price_per_1m=0.9, family="codestral"),
        _m("mistral", id="open-mistral-nemo", context_window=128_000, capabilities=CHAT_TOOLS,
           free_tier=True, family="mistral-nemo"),
    ],
    "together": [
        # Together hosts hundreds of open models — live list merged at runtime.
        _m("together", id="meta-llama/Llama-3.3-70B-Instruct-Turbo", context_window=128_000,
           capabilities=CHAT_TOOLS, input_price_per_1m=0.88, output_price_per_1m=0.88, family="llama"),
        _m("together", id="Qwen/Qwen2.5-Coder-32B-Instruct", context_window=128_000, capabilities=CODE,
           input_price_per_1m=0.80, output_price_per_1m=0.80, family="qwen-coder"),
        _m("together", id="deepseek-ai/DeepSeek-R1", context_window=128_000, capabilities=REASON,
           input_price_per_1m=3.0, output_price_per_1m=7.0, family="deepseek-r1"),
        _m("together", id="meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo", context_window=128_000,
           capabilities=CHAT_TOOLS, free_tier=True, family="llama",
           description="Together currently offers a free-tier model selection in the playground"),
    ],
    "openrouter": [
        # OpenRouter exposes a live /models endpoint incl. ":free" variants; the
        # adapter merges those dynamically. These ids rotate — verify live.
        _m("openrouter", id="meta-llama/llama-3.3-70b-instruct:free", context_window=128_000,
           capabilities=CHAT, free_tier=True, family="llama", description="Free variant (rate limited)"),
        _m("openrouter", id="google/gemini-2.0-flash-exp:free", context_window=1_000_000,
           capabilities=VISION + LONG, free_tier=True, family="gemini"),
        _m("openrouter", id="deepseek/deepseek-r1:free", context_window=128_000, capabilities=REASON,
           free_tier=True, family="deepseek"),
        _m("openrouter", id="meta-llama/llama-3.1-8b-instruct:free", context_window=128_000,
           capabilities=CHAT, free_tier=True, family="llama"),
    ],
    "xai": [
        _m("xai", id="grok-4", context_window=256_000, capabilities=REASON + VISION + [C.CODE.value],
           input_price_per_1m=3.0, output_price_per_1m=15.0, family="grok-4"),
        _m("xai", id="grok-4-fast", context_window=256_000, capabilities=CHAT_TOOLS + VISION,
           input_price_per_1m=0.20, output_price_per_1m=2.0, family="grok-4"),
        _m("xai", id="grok-3-mini", context_window=131_072, capabilities=REASON + CHAT_TOOLS,
           input_price_per_1m=0.30, output_price_per_1m=0.50, family="grok-3"),
    ],
    "cohere": [
        _m("cohere", id="command-r-plus-08-2024", name="command-r-plus", context_window=128_000,
           capabilities=CHAT_TOOLS + [C.CODE.value], free_tier=True, family="command-r",
           input_price_per_1m=2.5, output_price_per_1m=10.0),
        _m("cohere", id="command-r-08-2024", name="command-r", context_window=128_000,
           capabilities=CHAT_TOOLS, free_tier=True, input_price_per_1m=0.15, output_price_per_1m=0.60,
           family="command-r"),
        _m("cohere", id="command-a-03-2025", name="command-a", context_window=256_000,
           capabilities=CHAT_TOOLS + LONG, input_price_per_1m=2.5, output_price_per_1m=10.0,
           family="command-a"),
    ],
    "ollama": [],   # populated live from the local daemon
    "lmstudio": [],  # populated live from the local server
    "openai_compatible": [],  # user-defined endpoints; models listed live
    "mock": [
        _m("mock", id="mock-general", tier="local", capabilities=CHAT_TOOLS, free_tier=True,
           context_window=128_000, family="mock", description="Built-in deterministic test model"),
        _m("mock", id="mock-coder", tier="local", capabilities=CODE, free_tier=True,
           context_window=128_000, family="mock"),
        _m("mock", id="mock-reasoner", tier="local", capabilities=REASON, free_tier=True,
           context_window=128_000, family="mock"),
        _m("mock", id="mock-vision", tier="local", capabilities=VISION, free_tier=True,
           context_window=128_000, family="mock"),
        _m("mock", id="mock-longcontext", tier="local", capabilities=CHAT + LONG, free_tier=True,
           context_window=1_000_000, family="mock"),
        _m("mock", id="mock-flaky", tier="local", capabilities=CHAT, free_tier=True,
           context_window=8_192, family="mock",
           description="Test model that fails/rate-limits on demand (for resilience testing)"),
    ],
}


def catalog_for_provider(provider_type: str) -> list[ModelInfo]:
    return CATALOG.get(provider_type, [])


def all_catalog_entries() -> list[ModelInfo]:
    out: list[ModelInfo] = []
    for models in CATALOG.values():
        out.extend(models)
    return out
