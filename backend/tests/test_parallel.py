"""Parallel execution: independent agents run concurrently and aggregate."""
from __future__ import annotations

import asyncio
import time

from app.orchestrator.runner import AgentSpec, ModelRef, resilient_call
from app.providers.base import ChatMessage
from app.providers.registry import registry

MODELS = ["mock-general", "mock-coder", "mock-reasoner", "mock-vision", "mock-longcontext"]


def _spec(model: str) -> AgentSpec:
    mock_id = registry.first_of_type("mock").instance_id
    return AgentSpec(label=model, ref=ModelRef(model=model, provider_id=mock_id),
                     failover=False)


async def test_parallel_faster_than_sequential():
    mock = registry.first_of_type("mock")
    mock.settings = {"mock_delay_ms": 120}
    msgs = [ChatMessage(role="user", content="parallel timing test")]

    t0 = time.perf_counter()
    seq_results = []
    for m in MODELS:
        seq_results.append(await resilient_call(_spec(m), msgs, run_id="par", node_id=m))
    seq_elapsed = time.perf_counter() - t0

    quota_manager_reset = None
    t0 = time.perf_counter()
    par_results = await asyncio.gather(*[
        resilient_call(_spec(m), msgs, run_id="par2", node_id=m) for m in MODELS])
    par_elapsed = time.perf_counter() - t0

    assert len(seq_results) == len(par_results) == 5
    assert all(r.text for r in par_results)
    # 5 x 120ms sequential should clearly exceed parallel (cap 8 concurrent)
    assert par_elapsed < seq_elapsed * 0.6, (seq_elapsed, par_elapsed)
    mock.settings = {"mock_delay_ms": 0}


async def test_single_provider_concurrency_limit():
    mock = registry.first_of_type("mock")
    mock.settings = {"mock_delay_ms": 60, "concurrency": 2}
    msgs = [ChatMessage(role="user", content="semaphore test")]
    t0 = time.perf_counter()
    await asyncio.gather(*[
        resilient_call(_spec(m), msgs, run_id="sem", node_id=f"{m}{i}")
        for i, m in enumerate(MODELS + MODELS[:2])])
    elapsed = time.perf_counter() - t0
    # concurrency 2 across 7 calls at 60ms -> at least 3 batches (~180ms)
    assert elapsed >= 0.15
    mock.settings = {"mock_delay_ms": 0}
