"""Council workflows: independent → critique → revision → synthesis, and
failure isolation (one bad contributor / failed synthesizer)."""
from __future__ import annotations

import pytest

from app.orchestrator.council import run_council
from app.providers.base import ChatMessage
from app.providers.registry import registry
from app.orchestrator.runner import AllCandidatesFailed


@pytest.fixture
def mock_id():
    return registry.first_of_type("mock").instance_id


def _cfg(mock_id, participants=("mock-general", "mock-coder", "mock-reasoner"),
         synth="mock-general", rounds=1, revision=True, synth_valid=True):
    refs = [
        {"label": f"P{i + 1}", "role": role,
         "ref": {"provider_id": mock_id, "model": model}}
        for i, (model, role) in enumerate(zip(participants,
                                              ["researcher", "critic", "creative", "programmer"]))
    ]
    synth_ref = {"provider_id": mock_id, "model": synth} if synth_valid else \
        {"provider_id": "missing-instance", "model": "nope"}
    return {
        "participants": refs, "parallel": True, "critique_rounds": rounds,
        "revision": revision,
        "synthesizer": {"role": "synthesizer", "ref": synth_ref},
    }


async def test_full_council_with_all_stages(mock_id):
    cfg = _cfg(mock_id)
    result = await run_council(
        run_id="c1", question="Should our team adopt a monorepo?",
        history=[], config=cfg, project_id=None)
    assert result["final"]
    assert len(result["answers"]) == 3
    assert all(a["ok"] for a in result["answers"])
    assert len(result["critiques"]) == 1 and len(result["critiques"][0]) == 3
    assert len(result["finals"]) == 3
    assert result["totals"]["calls"] >= 9  # 3 + 3 critiques + 3 revisions + synth
    assert result["synthesis"] is not None
    assert len(result["contributors"]) == 3


async def test_council_without_critique_rounds(mock_id):
    cfg = _cfg(mock_id, rounds=0, revision=False)
    result = await run_council(run_id="c2", question="hello", history=[], config=cfg)
    assert result["final"]
    assert result["critiques"] == []


async def test_failed_synthesizer_falls_back_to_aggregation(mock_id):
    cfg = _cfg(mock_id, synth_valid=False)
    result = await run_council(run_id="c3", question="x", history=[], config=cfg)
    assert "auto-aggregated" in result["final"]
    assert all(label in result["final"] for label in ("P1", "P2", "P3"))


async def test_council_survives_one_dead_participant(mock_id):
    cfg = _cfg(mock_id, participants=("mock-general", "mock-coder", "mock-reasoner"))
    # one participant points at a missing instance — it dies, panel continues
    cfg["participants"][0]["ref"] = {"provider_id": "missing-instance", "model": "x"}
    result = await run_council(run_id="c4", question="robustness check", history=[], config=cfg)
    assert result["final"]
    assert sum(1 for a in result["answers"] if a["ok"]) >= 2


async def test_all_participants_dead_raises(mock_id):
    cfg = _cfg(mock_id)
    for p in cfg["participants"]:
        p["ref"] = {"provider_id": "nope", "model": "nope"}
    cfg["failover"] = False  # no failover chains; whole council must fail cleanly
    with pytest.raises(AllCandidatesFailed):
        await run_council(run_id="c5", question="x", history=[], config=cfg)


async def test_sequential_council(mock_id):
    cfg = _cfg(mock_id)
    cfg["parallel"] = False
    result = await run_council(run_id="c6", question="orderly question", history=[], config=cfg)
    assert result["final"]
