"""Shared helpers for provider adapters."""
from __future__ import annotations

import json
from typing import Any

import httpx

from .base import (
    AuthError,
    MalformedResponseError,
    ModelNotFoundError,
    ProviderError,
    ProviderUnavailableError,
    RateLimitError,
)
from .catalog import catalog_for_provider


def estimate_cost(provider_type: str, model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from catalog prices; 0.0 when prices are unknown/free."""
    for m in catalog_for_provider(provider_type):
        if m.id == model_id:
            if m.input_price_per_1m is None and m.output_price_per_1m is None:
                return 0.0
            pin = m.input_price_per_1m or 0.0
            pout = m.output_price_per_1m or 0.0
            return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 8)
    return 0.0


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


def raise_for_http_status(status_code: int, body: str, headers: httpx.Headers) -> None:
    """Map HTTP failures to typed provider exceptions (never bypass limits)."""
    retry_after = parse_retry_after(headers.get("retry-after"))
    detail = body[:500]
    if status_code in (401, 403):
        raise AuthError(f"Authentication failed ({status_code}): {detail}")
    if status_code == 404:
        raise ModelNotFoundError(f"Model/endpoint not found (404): {detail}")
    if status_code == 429:
        raise RateLimitError(
            f"Provider rate limit / quota reached (429). This is a legitimate limit; "
            f"the model enters cooldown and work fails over.",
            retry_after=retry_after,
        )
    if status_code == 400:
        # Some providers return 400 for quota/billing issues with distinctive text;
        # otherwise a malformed request is not retryable.
        low = body.lower()
        if "quota" in low or "rate" in low or "credit" in low or "billing" in low:
            raise RateLimitError(f"Quota/billing error (400): {detail}", retry_after=retry_after)
        raise ProviderError(f"Bad request (400): {detail}")
    if status_code >= 500:
        raise ProviderUnavailableError(f"Provider server error ({status_code}): {detail}")
    raise ProviderError(f"Unexpected HTTP {status_code}: {detail}")


def safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise MalformedResponseError(f"Non-JSON response: {resp.text[:300]}") from exc


def first(d: dict, *keys, default=None):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default
