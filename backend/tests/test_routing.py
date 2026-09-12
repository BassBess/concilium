"""Smart routing: task analysis and candidate ranking."""
from __future__ import annotations
import pytest

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


def test_scheduler_prefers_resource_matching_specialist_role():
    from app.orchestrator.resources import ResourceRegistry, ResourceSpec
    from app.orchestrator.router import analyze_task
    from app.orchestrator.scheduler import Scheduler

    registry = ResourceRegistry()

    registry.register(ResourceSpec(
        id="generic",
        kind="llm",
        capabilities=["chat"],
        free=True,
        estimated_latency_ms=1000,
    ))

    registry.register(ResourceSpec(
        id="math-model",
        kind="llm",
        capabilities=["chat", "reasoning"],
        free=True,
        estimated_latency_ms=1000,
    ))

    scheduler = Scheduler(registry)
    profile = analyze_task(
        "solve this mathematical problem",
        role="mathematician",
    )

    selected = scheduler.select(profile)

    assert selected is not None
    assert selected.resource.id == "math-model"


def test_scheduler_can_represent_non_llm_resources():
    from app.orchestrator.resources import ResourceRegistry, ResourceSpec
    from app.orchestrator.router import analyze_task
    from app.orchestrator.scheduler import Scheduler

    registry = ResourceRegistry()

    registry.register(ResourceSpec(
        id="python",
        kind="runtime",
        name="Python execution",
        capabilities=["chat", "code"],
        local=True,
        estimated_latency_ms=100,
    ))

    scheduler = Scheduler(registry)

    profile = analyze_task(
        "write and execute code to test this hypothesis",
        role="programmer",
    )

    selected = scheduler.select(profile)

    assert selected is not None
    assert selected.resource.id == "python"


def test_scheduler_rejects_resource_missing_required_capability():
    from app.orchestrator.resources import ResourceRegistry, ResourceSpec
    from app.orchestrator.router import analyze_task
    from app.orchestrator.scheduler import Scheduler

    registry = ResourceRegistry()

    registry.register(ResourceSpec(
        id="chat-only",
        kind="llm",
        capabilities=["chat"],
    ))

    scheduler = Scheduler(registry)

    profile = analyze_task(
        "analyze this image",
        has_images=True,
    )

    assert scheduler.select(profile) is None


def test_scheduler_respects_context_capacity():
    from app.orchestrator.resources import ResourceRegistry, ResourceSpec
    from app.orchestrator.router import analyze_task
    from app.orchestrator.scheduler import Scheduler

    registry = ResourceRegistry()

    registry.register(ResourceSpec(
        id="small",
        kind="llm",
        capabilities=["chat"],
        metadata={"context_window": 100},
    ))

    scheduler = Scheduler(registry)

    profile = analyze_task(
        "x" * 1000,
    )

    assert scheduler.select(profile) is None


@pytest.mark.asyncio
async def test_llm_adapters_become_scheduler_resources():
    from app.orchestrator.resources import build_resource_registry

    class FakeModel:
        capabilities = ["chat", "reasoning"]
        context_window = 32768
        free_tier = True

    class FakeAdapter:
        instance_id = "provider-a"
        provider_type = "fake"
        label = "Provider A"
        enabled = True

        async def get_models(self):
            return [FakeModel()]

    registry = await build_resource_registry([FakeAdapter()])

    resource = registry.get("llm:provider-a")

    assert resource is not None
    assert resource.kind == "llm"
    assert resource.provider == "fake"
    assert resource.free is True
    assert resource.metadata["model_count"] == 1
    assert "reasoning" in resource.capabilities


@pytest.mark.asyncio
async def test_broken_provider_does_not_block_resource_discovery():
    from app.orchestrator.resources import build_resource_registry

    class BrokenAdapter:
        instance_id = "broken"
        provider_type = "broken"
        label = "Broken"
        enabled = True

        async def get_models(self):
            raise RuntimeError("provider unavailable")

    class WorkingModel:
        capabilities = ["chat"]
        context_window = 8192
        free_tier = False

    class WorkingAdapter:
        instance_id = "working"
        provider_type = "working"
        label = "Working"
        enabled = True

        async def get_models(self):
            return [WorkingModel()]

    registry = await build_resource_registry(
        [BrokenAdapter(), WorkingAdapter()]
    )

    assert registry.get("llm:broken") is None
    assert registry.get("llm:working") is not None


def test_scheduler_resource_filter_preserves_global_router():
    from app.orchestrator.router import filter_adapters_by_resource

    class Adapter:
        def __init__(self, instance_id):
            self.instance_id = instance_id

    adapters = [Adapter("a"), Adapter("b")]

    assert filter_adapters_by_resource(adapters, None) == adapters
    assert filter_adapters_by_resource(adapters, "llm:a") == [adapters[0]]
    assert filter_adapters_by_resource(adapters, "llm:missing") == []
