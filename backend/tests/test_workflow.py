"""Custom workflow DAG engine."""
from __future__ import annotations

import pytest

from app.orchestrator.workflow import _topo_levels, run_workflow
from app.providers.registry import registry


def test_topological_levels():
    nodes = [
        {"id": "a"}, {"id": "b", "depends_on": ["a"]},
        {"id": "c", "depends_on": ["a"]}, {"id": "d", "depends_on": ["b", "c"]},
    ]
    levels = _topo_levels(nodes)
    assert [n["id"] for n in levels[0]] == ["a"]
    assert {n["id"] for n in levels[1]} == {"b", "c"}
    assert levels[2][0]["id"] == "d"


def test_cycle_detected():
    nodes = [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}]
    with pytest.raises(ValueError):
        _topo_levels(nodes)


async def test_research_to_synthesis_workflow():
    defn = {
        "nodes": [
            {"id": "research", "role": "researcher", "count": 2},
            {"id": "critique", "role": "critic", "count": 1, "depends_on": ["research"]},
            {"id": "write", "role": "synthesizer", "count": 1,
             "depends_on": ["critique", "research"], "terminal": True},
        ]
    }
    result = await run_workflow(
        run_id="wf1", question="Plan a launch", history=[], definition=defn,
        adapters=registry.enabled())
    assert result["final"]
    assert result["totals"]["calls"] == 4
    assert len(result["nodes"]) == 3


async def test_coding_gauntlet_builtin():
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Workflow

    async with SessionLocal() as s:
        wf = (await s.execute(
            select(Workflow).where(Workflow.name == "Coding gauntlet"))).scalar_one()
    result = await run_workflow(
        run_id="wf2", question="Implement a LRU cache", history=[],
        definition=wf.definition, adapters=registry.enabled())
    assert result["final"]
    assert result["totals"]["calls"] >= 5


async def test_workflow_terminal_auto_detected():
    defn = {"nodes": [
        {"id": "a", "role": "researcher", "count": 1},
        {"id": "b", "role": "judge", "count": 1, "depends_on": ["a"]},
    ]}
    result = await run_workflow(
        run_id="wf3", question="x", history=[], definition=defn,
        adapters=registry.enabled())
    assert result["final"]
