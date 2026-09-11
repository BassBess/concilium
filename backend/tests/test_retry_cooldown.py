"""Retry with exponential backoff, provider cooldowns, automatic failover."""
from __future__ import annotations

import pytest

from app.orchestrator.quotas import CooldownActive, quota_manager
from app.orchestrator.runner import AgentSpec, AllCandidatesFailed, ModelRef, resilient_call
from app.providers.base import ChatMessage, RateLimitError
from app.providers.registry import registry


def _mock_id() -> str:
    return registry.first_of_type("mock").instance_id


def _spec(model: str, **kw) -> AgentSpec:
    return AgentSpec(label=f"t-{model}", ref=ModelRef(model=model, provider_id=_mock_id()), **kw)


def _msgs():
    return [ChatMessage(role="user", content="question text")]


async def test_retry_then_success():
    mock = registry.first_of_type("mock")
    mock.settings = {"mock_delay_ms": 0, "fail_once": True}
    result = await resilient_call(_spec("mock-general"), _msgs(), run_id="t1", node_id="n")
    assert result.text
    st = quota_manager.state("mock/mock-general")
    assert st.retries == 1
    assert st.successes == 1


async def test_rate_limit_triggers_failover_to_other_model():
    mock = registry.first_of_type("mock")
    mock.settings = {"mock_delay_ms": 0}
    mock.rate_limited_models = {"mock-general"}
    result = await resilient_call(_spec("mock-general"), _msgs(), run_id="t2", node_id="n")
    # The panel survives: another mock model answered.
    assert result.text
    assert result.model != "mock-general"
    snap = {s["key"]: s for s in await quota_manager.snapshot()}
    assert snap["mock/mock-general"]["in_cooldown"]


async def test_cooldown_skips_cooled_candidate():
    mock = registry.first_of_type("mock")
    wait = quota_manager.mark_rate_limited(mock, "mock-coder", retry_after=60)
    assert wait >= 60
    with pytest.raises((CooldownActive,)):
        quota_manager.check(mock, "mock-coder")
    snap = {s["key"]: s for s in await quota_manager.snapshot()}
    assert snap["mock/mock-coder"]["in_cooldown"]
    assert snap["mock/mock-coder"]["cooldown_remaining"] > 50


async def test_repeated_failures_induce_backoff_cooldown():
    mock = registry.first_of_type("mock")
    quota_manager.mark_failure(mock, "mock-reasoner")
    wait = quota_manager.mark_failure(mock, "mock-reasoner")
    assert wait > 0
    with pytest.raises(CooldownActive):
        quota_manager.check(mock, "mock-reasoner")


async def test_all_failures_raises_collected_errors():
    mock = registry.first_of_type("mock")
    mock.force_rate_limit = True
    # cool every model so the whole chain is exhausted
    for model in ("mock-general", "mock-coder", "mock-reasoner", "mock-vision",
                  "mock-longcontext", "mock-flaky"):
        quota_manager.mark_rate_limited(mock, model, retry_after=300)
    with pytest.raises(AllCandidatesFailed):
        await resilient_call(_spec("mock-general"), _msgs(), run_id="t3", node_id="n")
    mock.force_rate_limit = False


async def test_missing_provider_instance_errors():
    spec = AgentSpec(label="x", ref=ModelRef(model="m", provider_id="does-not-exist"),
                     failover=False)
    with pytest.raises(AllCandidatesFailed):
        await resilient_call(spec, _msgs(), run_id="t4", node_id="n")
