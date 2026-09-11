"""Quota windows, daily caps, counters and usage-record persistence."""
from __future__ import annotations

import pytest

from app.orchestrator.quotas import CooldownActive, RateWindowExceeded, quota_manager
from app.providers.registry import registry


@pytest.fixture
def mock_adapter():
    a = registry.first_of_type("mock")
    original = dict(a.settings)
    yield a
    a.settings = original


def test_daily_request_cap(mock_adapter):
    mock_adapter.settings = {"daily_requests": 1, "cooldown_seconds": 5}
    quota_manager.check(mock_adapter, "mock-general")
    quota_manager.mark_started(mock_adapter, "mock-general")
    quota_manager.mark_success(mock_adapter, "mock-general", 10, 20, 0.0, 5)
    with pytest.raises(CooldownActive):
        quota_manager.check(mock_adapter, "mock-general")
    bucket = quota_manager.state("mock/mock-general").day_bucket()
    assert bucket["requests"] == 1
    assert bucket["input_tokens"] == 10


def test_rpm_window(mock_adapter):
    mock_adapter.settings = {"rpm": 1}
    quota_manager.check(mock_adapter, "mock-coder")
    quota_manager.mark_started(mock_adapter, "mock-coder")
    with pytest.raises(RateWindowExceeded):
        quota_manager.check(mock_adapter, "mock-coder")


async def test_usage_row_persisted():
    mock_adapter = registry.first_of_type("mock")
    await quota_manager.record_usage_row(
        "mock", "mock-general", input_tokens=11, output_tokens=7,
        cost=0.0, latency_ms=42, status="success", run_id="r1")
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import UsageRecord

    async with SessionLocal() as s:
        rows = (await s.execute(
            select(UsageRecord).where(UsageRecord.run_id == "r1"))).scalars().all()
    assert rows and rows[0].input_tokens == 11


async def test_snapshot_structure(mock_adapter):
    quota_manager.mark_success(mock_adapter, "mock-general", 1, 1, 0.0, 12)
    snap = await quota_manager.snapshot()
    entry = next(x for x in snap if x["key"] == "mock/mock-general")
    assert entry["successes"] == 1
    assert entry["avg_latency_ms"] == 12
    assert entry["today"]["requests"] == 1


def test_reset_clears_state():
    a = registry.first_of_type("mock")
    quota_manager.mark_success(a, "mock-general", 1, 1, 0.0, 1)
    quota_manager.reset()
    assert quota_manager._states == {}
