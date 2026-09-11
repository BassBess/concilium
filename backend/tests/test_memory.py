"""End-to-end API: projects, runs, persistence, documents, usage, masking."""
from __future__ import annotations

import asyncio

import pytest


async def _wait_run(client, run_id, timeout=30):
    for _ in range(timeout * 10):
        r = await client.get(f"/api/runs/{run_id}")
        assert r.status_code == 200
        data = r.json()
        if data["status"] in ("ok", "failed"):
            return data
        await asyncio.sleep(0.1)
    raise AssertionError("run did not finish")


async def test_health_and_catalog(client):
    r = await client.get("/api/health")
    assert r.json()["status"] == "ok"
    models = (await client.get("/api/models")).json()
    assert any(m["provider_type"] == "mock" for m in models)
    mocks = [m for m in models if m["provider_type"] == "mock"]
    assert all(m["uid"] for m in mocks)


async def test_project_and_conversation_lifecycle(client):
    r = await client.post("/api/projects", json={"name": "P", "description": "d"})
    pid = r.json()["id"]
    r = await client.post("/api/conversations",
                          json={"project_id": pid, "mode": "single"})
    cid = r.json()["id"]
    convs = (await client.get(f"/api/projects/{pid}/conversations")).json()
    assert any(c["id"] == cid for c in convs)
    await client.delete(f"/api/conversations/{cid}")
    await client.delete(f"/api/projects/{pid}")


async def test_single_run_persists_final_message(client):
    pid = (await client.post("/api/projects", json={"name": "run"})).json()["id"]
    cid = (await client.post("/api/conversations",
                             json={"project_id": pid, "mode": "single"})).json()["id"]
    providers = (await client.get("/api/providers")).json()
    mock_id = next(p["id"] for p in providers if p["type"] == "mock")
    r = await client.post(f"/api/conversations/{cid}/runs", json={
        "question": "Explain recursion briefly.", "mode": "single",
        "config": {"ref": {"provider_id": mock_id, "model": "mock-general"}}})
    run = await _wait_run(client, r.json()["run_id"])
    assert run["status"] == "ok" and run["final"]
    msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()
    roles = [m["role"] for m in msgs]
    assert "user" in roles and "assistant" in roles
    final = [m for m in msgs if m["kind"] == "final"][0]
    assert final["meta"]["mode"] == "single"


async def test_council_run_over_sse_events(client):
    pid = (await client.post("/api/projects", json={"name": "sse"})).json()["id"]
    cid = (await client.post("/api/conversations",
                             json={"project_id": pid, "mode": "council"})).json()["id"]
    providers = (await client.get("/api/providers")).json()
    mock_id = next(p["id"] for p in providers if p["type"] == "mock")
    cfg = {
        "participants": [
            {"label": "A", "role": "researcher", "ref": {"provider_id": mock_id, "model": "mock-general"}},
            {"label": "B", "role": "critic", "ref": {"provider_id": mock_id, "model": "mock-coder"}},
        ],
        "parallel": True, "critique_rounds": 1, "revision": False,
        "synthesizer": {"ref": {"provider_id": mock_id, "model": "mock-reasoner"}},
    }
    r = await client.post(f"/api/conversations/{cid}/runs", json={
        "question": "Should we rewrite in Rust?", "mode": "council", "config": cfg})
    run = await _wait_run(client, r.json()["run_id"])
    assert run["status"] == "ok"
    types = {e["type"] for e in run["trace"]["events"]}
    assert {"run_started", "round_started", "agent_finished", "synthesis_started",
            "run_finished"}.issubset(types)
    msgs = (await client.get(f"/api/conversations/{cid}/messages")).json()
    assert sum(1 for m in msgs if m["role"] == "agent") >= 4  # answers + critiques


async def test_document_upload_and_project_memory(client):
    pid = (await client.post("/api/projects", json={"name": "docs"})).json()["id"]
    files = {"file": ("facts.txt", b"The secret codeword is BANANA-42.", "text/plain")}
    r = await client.post(f"/api/projects/{pid}/documents", files=files)
    assert r.status_code == 200
    r = await client.post("/api/tools/knowledge_search/invoke",
                          json={"arguments": {"query": "secret codeword"},
                                "project_id": pid})
    assert "BANANA-42" in r.json()["output"]
    # binary type rejected
    r2 = await client.post(f"/api/projects/{pid}/documents",
                           files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")})
    assert r2.status_code == 415


async def test_usage_stats_recorded(client):
    r = await client.get("/api/usage/stats")
    data = r.json()
    assert "totals" in data and "by_provider" in data and "by_day" in data
    assert "mock" in data["by_provider"]


async def test_api_key_never_returned(client):
    # create a custom compatible endpoint with a key
    r = await client.post("/api/providers", json={
        "type": "openai_compatible", "label": "local-vllm",
        "base_url": "http://localhost:9999/v1", "api_key": "sk-secret-1234567890",
        "enabled": False})
    pid_ = r.json()["id"]
    providers = (await client.get("/api/providers")).json()
    row = next(p for p in providers if p["id"] == pid_)
    val = row["settings"]["api_key"]
    assert val["set"] is True
    assert "sk-secret-1234567890" not in str(val)
    assert "•" in val["masked"]


async def test_wizard_status_and_validation(client):
    r = await client.get("/api/wizard/status")
    data = r.json()
    assert "providers" in data
    r = await client.post("/api/conversations",
                          json={"project_id": "nope", "mode": "single"})
    assert r.status_code in (404, 400)
