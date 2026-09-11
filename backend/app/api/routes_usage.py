"""Usage & cost dashboard, quota/cooldown introspection and control."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.sql import func

from ..db import SessionLocal
from ..models import UsageRecord
from ..orchestrator.quotas import quota_manager
from .deps import require_auth

router = APIRouter(prefix="/api/usage", tags=["usage"])


@router.get("/quotas")
async def quotas(_: dict = Depends(require_auth)):
    return await quota_manager.snapshot()


class ResetIn(BaseModel):
    key: str | None = None  # "provider/model" or null for everything
    cooldowns_only: bool = True


@router.post("/reset")
async def reset(body: ResetIn, _: dict = Depends(require_auth)):
    if body.cooldowns_only and body.key:
        st = quota_manager.state(body.key)
        st.cooldown_until = 0.0
        st.cooldown_reason = ""
        st.consecutive_failures = 0
    elif not body.cooldowns_only:
        quota_manager.reset(body.key)
    return {"ok": True}


@router.get("/stats")
async def stats(days: int = 30, _: dict = Depends(require_auth)):
    since = datetime.now(timezone.utc) - timedelta(days=max(1, min(days, 365)))
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(UsageRecord).where(UsageRecord.ts >= since)
        )).scalars().all()

    totals = {"requests": 0, "success": 0, "ratelimit": 0, "errors": 0, "retries": 0,
              "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "latency_ms": []}
    by_provider: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
                 "errors": 0, "ratelimit": 0, "latency_ms": []})
    by_model: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
                 "errors": 0, "latency_ms": []})
    by_day: dict[str, dict] = defaultdict(
        lambda: {"requests": 0, "errors": 0, "cost": 0.0, "tokens": 0})

    for r in rows:
        totals["requests"] += 1
        totals["input_tokens"] += r.input_tokens
        totals["output_tokens"] += r.output_tokens
        totals["cost"] += r.cost
        if r.latency_ms:
            totals["latency_ms"].append(r.latency_ms)
        if r.status == "success":
            totals["success"] += 1
        elif r.status == "ratelimit":
            totals["ratelimit"] += 1
        elif r.status == "retry":
            totals["retries"] += 1
        else:
            totals["errors"] += 1

        p = by_provider[r.provider]
        p["requests"] += 1
        p["input_tokens"] += r.input_tokens
        p["output_tokens"] += r.output_tokens
        p["cost"] += r.cost
        p["latency_ms"].append(r.latency_ms) if r.latency_ms else None
        if r.status in ("error", "ratelimit"):
            p["errors"] += 1
            if r.status == "ratelimit":
                p["ratelimit"] += 1

        mk = f"{r.provider}/{r.model}"
        m = by_model[mk]
        m["requests"] += 1
        m["input_tokens"] += r.input_tokens
        m["output_tokens"] += r.output_tokens
        m["cost"] += r.cost
        m["latency_ms"].append(r.latency_ms) if r.latency_ms else None
        if r.status in ("error", "ratelimit"):
            m["errors"] += 1

        day = r.ts.strftime("%Y-%m-%d")
        by_day[day]["requests"] += 1
        by_day[day]["tokens"] += r.input_tokens + r.output_tokens
        by_day[day]["cost"] += r.cost
        if r.status in ("error", "ratelimit"):
            by_day[day]["errors"] += 1

    def finalize(d: dict) -> dict:
        lat = d.pop("latency_ms", [])
        d["avg_latency_ms"] = int(sum(lat) / len(lat)) if lat else 0
        d["cost"] = round(d["cost"], 6)
        return d

    totals["cost"] = round(totals["cost"], 6)
    lat = totals.pop("latency_ms")
    totals["avg_latency_ms"] = int(sum(lat) / len(lat)) if lat else 0
    live = await quota_manager.snapshot()
    return {
        "totals": totals,
        "by_provider": {k: finalize(v) for k, v in sorted(by_provider.items())},
        "by_model": {k: finalize(v) for k, v in sorted(by_model.items())},
        "by_day": dict(sorted(by_day.items())),
        "cooldowns": [c for c in live if c["in_cooldown"]],
    }
