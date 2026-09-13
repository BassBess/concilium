"""Quota, rate-limit, cooldown and concurrency management.

This module NEVER tries to circumvent provider limits: when a provider reports
429/quota exhaustion we put the model into an explicit cooldown (honoring
``retry-after`` when supplied, otherwise exponential backoff) and the runner
fails over to another healthy model. Counters drive the usage dashboard.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import SessionLocal
from ..models import UsageRecord

# Conservative, USER-EDITABLE defaults for free tiers (Providers UI).
# These are scheduling hints, not attempts to exceed real limits.
DEFAULT_LIMITS: dict[str, dict[str, int]] = {
    "groq":       {"rpm": 30, "concurrency": 4, "cooldown_seconds": 30},
    "google":     {"rpm": 10, "concurrency": 2, "cooldown_seconds": 45},
    "openrouter": {"rpm": 20, "concurrency": 4, "cooldown_seconds": 30},
    "mistral":    {"rpm": 6,  "concurrency": 2, "cooldown_seconds": 60},
    "cohere":     {"rpm": 10, "concurrency": 2, "cooldown_seconds": 60},
    "ollama":     {"rpm": 60, "concurrency": 1, "cooldown_seconds": 10},
    "lmstudio":   {"rpm": 60, "concurrency": 1, "cooldown_seconds": 10},
    "mock":       {"rpm": 10_000, "concurrency": 8, "cooldown_seconds": 1},
}


class CooldownActive(RuntimeError):
    def __init__(self, key: str, retry_after: float, reason: str):
        super().__init__(f"{key} in cooldown for {retry_after:.0f}s ({reason})")
        self.key = key
        self.retry_after = retry_after
        self.reason = reason


class RateWindowExceeded(RuntimeError):
    def __init__(self, key: str, retry_after: float):
        super().__init__(f"{key} local rate window exceeded; retry in {retry_after:.0f}s")
        self.key = key
        self.retry_after = retry_after


@dataclass
class ModelState:
    key: str
    request_window: list[float] = field(default_factory=list)
    cooldown_until: float = 0.0
    cooldown_reason: str = ""
    consecutive_failures: int = 0
    successes: int = 0
    rate_limits: int = 0
    retries: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: float = 0.0
    by_day: dict[str, dict[str, int]] = field(default_factory=dict)
    by_month: dict[str, dict[str, int]] = field(default_factory=dict)

    def day_bucket(self) -> dict[str, int]:
        d = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.by_day.setdefault(d, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0})

    def month_bucket(self) -> dict[str, int]:
        m = datetime.now(timezone.utc).strftime("%Y-%m")
        return self.by_month.setdefault(m, {"requests": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0})


class QuotaManager:
    def __init__(self) -> None:
        self._states: dict[str, ModelState] = {}
        self._sems: dict[tuple[str, int], asyncio.Semaphore] = {}
        self.settings = get_settings()

    # -- helpers -------------------------------------------------------------
    def key(self, provider_type: str, model: str) -> str:
        return f"{provider_type}/{model}"

    def state(self, key: str) -> ModelState:
        return self._states.setdefault(key, ModelState(key=key))

    def provider_settings(self, adapter) -> dict[str, Any]:
        s = dict(adapter.settings or {})
        defaults = DEFAULT_LIMITS.get(adapter.type_key, {})
        return {
            "rpm": int(s.get("rpm") or defaults.get("rpm", 60)),
            "tpm": int(s.get("tpm") or defaults.get("tpm", 0)),
            "daily_requests": int(s.get("daily_requests") or defaults.get("daily_requests", 0)),
            "daily_tokens": int(s.get("daily_tokens") or defaults.get("daily_tokens", 0)),
            "monthly_requests": int(s.get("monthly_requests") or defaults.get("monthly_requests", 0)),
            "concurrency": int(s.get("concurrency") or defaults.get("concurrency", self.settings.default_concurrency)),
            "cooldown_seconds": int(s.get("cooldown_seconds") or
                                    defaults.get("cooldown_seconds", self.settings.default_cooldown_seconds)),
            "max_retries": int(s.get("max_retries")
                               if s.get("max_retries") is not None
                               else self.settings.default_max_retries),
        }

    def semaphore(self, adapter) -> asyncio.Semaphore:
        # Key includes the limit so that changing concurrency settings (or tests)
        # always produces a correctly-sized semaphore.
        limits = self.provider_settings(adapter)
        key = (adapter.instance_id, max(1, limits["concurrency"]))
        if key not in self._sems:
            self._sems[key] = asyncio.Semaphore(max(1, limits["concurrency"]))
        return self._sems[key]

    # -- pre-flight ----------------------------------------------------------
    def check(self, adapter, model: str) -> None:
        """Raise if the model may not be called right now."""
        key = self.key(adapter.type_key, model)
        st = self.state(key)
        limits = self.provider_settings(adapter)
        now = time.time()

        if st.cooldown_until > now:
            raise CooldownActive(key, st.cooldown_until - now, st.cooldown_reason)

        # RPM sliding window
        window = [t for t in st.request_window if now - t < 60.0]
        st.request_window = window
        if limits["rpm"] and len(window) >= limits["rpm"]:
            raise RateWindowExceeded(key, 60.0 - (now - window[0]))

        day = st.day_bucket()
        if limits["daily_requests"] and day["requests"] >= limits["daily_requests"]:
            # Respect a daily cap: cooldown until end of UTC day
            midnight = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59).timestamp()
            self._cooldown(key, midnight - now, "daily request cap reached (locally configured)")
            raise CooldownActive(key, midnight - now, "daily request cap")

    # -- outcome recording ---------------------------------------------------
    def mark_started(self, adapter, model: str) -> None:
        st = self.state(self.key(adapter.type_key, model))
        st.request_window.append(time.time())

    def mark_success(self, adapter, model: str, input_tokens: int, output_tokens: int,
                     cost: float, latency_ms: int) -> None:
        st = self.state(self.key(adapter.type_key, model))
        st.consecutive_failures = 0
        st.cooldown_until = 0.0
        st.cooldown_reason = ""
        st.successes += 1
        st.latencies_ms.append(latency_ms)
        st.latencies_ms = st.latencies_ms[-50:]
        st.total_input_tokens += input_tokens
        st.total_output_tokens += output_tokens
        st.total_cost += cost
        d, m = st.day_bucket(), st.month_bucket()
        for bucket in (d, m):
            bucket["requests"] += 1
            bucket["input_tokens"] += input_tokens
            bucket["output_tokens"] += output_tokens

    def mark_rate_limited(self, adapter, model: str, retry_after: float | None) -> float:
        """Put the model into cooldown after a *provider-reported* limit."""
        key = self.key(adapter.type_key, model)
        st = self.state(key)
        st.rate_limits += 1
        limits = self.provider_settings(adapter)
        wait = retry_after or min(3600.0, limits["cooldown_seconds"] * (2 ** min(st.rate_limits - 1, 5)))
        self._cooldown(key, wait, "provider rate limit / quota (429)")
        d = st.day_bucket()
        d["errors"] += 1
        return wait

    def mark_failure(self, adapter, model: str) -> float:
        key = self.key(adapter.type_key, model)
        st = self.state(key)
        st.consecutive_failures += 1
        limits = self.provider_settings(adapter)
        d = st.day_bucket()
        d["errors"] += 1
        if st.consecutive_failures >= 2:
            wait = min(1800.0, limits["cooldown_seconds"] * (2 ** (st.consecutive_failures - 2)))
            self._cooldown(key, wait, f"{st.consecutive_failures} consecutive failures")
            return wait
        return 0.0

    def mark_retry(self, adapter, model: str) -> None:
        self.state(self.key(adapter.type_key, model)).retries += 1

    def _cooldown(self, key: str, seconds: float, reason: str) -> None:
        st = self.state(key)
        st.cooldown_until = max(st.cooldown_until, time.time() + seconds)
        st.cooldown_reason = reason

    def reset(self, key: str | None = None) -> None:
        if key:
            self._states.pop(key, None)
        else:
            self._states.clear()

    # -- scheduler health ----------------------------------------------------
    def health(self, adapter, model: str | None = None) -> dict[str, Any]:
        """Return lightweight runtime health information for scheduling."""
        provider = adapter.type_key

        if model is not None:
            st = self.state(self.key(provider, model))
            now = time.time()

            avg_latency = (
                sum(st.latencies_ms) / len(st.latencies_ms)
                if st.latencies_ms
                else None
            )

            return {
                "provider": provider,
                "model": model,
                "in_cooldown": st.cooldown_until > now,
                "consecutive_failures": st.consecutive_failures,
                "successes": st.successes,
                "rate_limits": st.rate_limits,
                "retries": st.retries,
                "avg_latency_ms": avg_latency,
            }

        states = [
            st
            for key, st in self._states.items()
            if key.startswith(f"{provider}/")
        ]

        if not states:
            return {
                "provider": provider,
                "in_cooldown": False,
                "consecutive_failures": 0,
                "successes": 0,
                "rate_limits": 0,
                "retries": 0,
                "avg_latency_ms": None,
            }

        now = time.time()
        latencies = [
            latency
            for st in states
            for latency in st.latencies_ms
        ]

        return {
            "provider": provider,
            "in_cooldown": any(st.cooldown_until > now for st in states),
            "consecutive_failures": max(
                st.consecutive_failures for st in states
            ),
            "successes": sum(st.successes for st in states),
            "rate_limits": sum(st.rate_limits for st in states),
            "retries": sum(st.retries for st in states),
            "avg_latency_ms": (
                sum(latencies) / len(latencies)
                if latencies
                else None
            ),
        }

    # -- persistence ---------------------------------------------------------
    async def record_usage_row(self, provider: str, model: str, *, kind: str = "chat",
                               input_tokens: int = 0, output_tokens: int = 0, cost: float = 0.0,
                               latency_ms: int = 0, status: str = "success", detail: str = "",
                               run_id: str | None = None, conversation_id: str | None = None) -> None:
        try:
            async with SessionLocal() as session:
                session.add(UsageRecord(
                    provider=provider, model=model, kind=kind,
                    input_tokens=input_tokens, output_tokens=output_tokens, cost=cost,
                    latency_ms=latency_ms, status=status, detail=detail[:500],
                    run_id=run_id, conversation_id=conversation_id,
                ))
                await session.commit()
        except Exception:
            pass  # telemetry must never break a response

    # -- snapshots -----------------------------------------------------------
    async def snapshot(self) -> list[dict[str, Any]]:
        out = []
        now = time.time()
        for key, st in self._states.items():
            d = st.day_bucket()
            avg_latency = int(sum(st.latencies_ms) / len(st.latencies_ms)) if st.latencies_ms else 0
            out.append({
                "key": key,
                "provider": key.split("/")[0],
                "model": "/".join(key.split("/")[1:]),
                "in_cooldown": st.cooldown_until > now,
                "cooldown_remaining": max(0, int(st.cooldown_until - now)),
                "cooldown_reason": st.cooldown_reason if st.cooldown_until > now else "",
                "rpm_used": len([t for t in st.request_window if now - t < 60]),
                "successes": st.successes,
                "rate_limits": st.rate_limits,
                "consecutive_failures": st.consecutive_failures,
                "retries": st.retries,
                "avg_latency_ms": avg_latency,
                "today": d,
                "total": {"input_tokens": st.total_input_tokens,
                          "output_tokens": st.total_output_tokens, "cost": round(st.total_cost, 6)},
            })
        return sorted(out, key=lambda x: (not x["in_cooldown"], x["key"]))


quota_manager = QuotaManager()
