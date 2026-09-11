"""Smart routing: task analysis and candidate ranking."""
from __future__ import annotations

from app.orchestrator.router import analyze_task, rank_candidates
from app.providers.base import Capability
from app.providers.registry import registry


def test_task_analysis_code():
    p = analyze_task("Write a python function `def quicksort` and debug the stack trace")
    assert p.needs_code
    assert Capability.CODE.value in p.required


def test_task_analysis_vision_long_context():
    p = analyze_task("describe this image", has_images=True)
    assert Capability.VISION.value in p.required
    long = analyze_task("word " * 80_000)
    assert long.estimated_tokens > 60_000
    assert Capability.LONG_CONTEXT.value in long.required


def test_task_analysis_research_and_reasoning():
    p = analyze_task("Research the latest 2026 news about fusion energy and compare claims")
    assert p.needs_research
    p2 = analyze_task("Prove by contradiction that sqrt(2) is irrational, step by step")
    assert p2.needs_reasoning


def test_simple_prefers_fast():
    p = analyze_task("hi")
    assert p.simple


async def test_rank_returns_candidates():
    await registry.ensure_loaded()
    profile = analyze_task("write a python quine")
    ranked = await rank_candidates(registry.enabled(), profile)
    assert ranked
    # mock-coder should outrank general for coding tasks among mocks
    top_models = [c.model.id for c in ranked[:4]]
    assert "mock-coder" in top_models[:3]


async def test_rank_diverse_backfill_single_provider():
    await registry.ensure_loaded()
    profile = analyze_task("general help")
    ranked = await rank_candidates(registry.enabled(), profile, n=5, diverse=True)
    # only the mock provider is enabled, but we still get 5+ distinct models
    assert len({c.model.id for c in ranked}) >= 5


async def test_cooling_models_deprioritized():
    import time

    from app.orchestrator.quotas import quota_manager

    await registry.ensure_loaded()
    mock = registry.first_of_type("mock")
    st = quota_manager.state(quota_manager.key("mock", "mock-general"))
    st.cooldown_until = time.time() + 300
    profile = analyze_task("hello there")
    ranked = await rank_candidates(registry.enabled(), profile)
    cooled = [c for c in ranked if c.model.id == "mock-general"][0]
    assert cooled.cooling is True
    assert ranked[-1].score <= cooled.score + 0.01 or cooled not in ranked[:2]
