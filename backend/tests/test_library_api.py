"""API tests for workflows and prompt templates."""
from __future__ import annotations


async def test_list_builtin_workflows(client):
    r = await client.get("/api/workflows")
    assert r.status_code == 200
    names = [w["name"] for w in r.json()]
    assert {"Classic council", "Research pipeline", "Coding gauntlet"}.issubset(set(names))
    classic = next(w for w in r.json() if w["name"] == "Classic council")
    assert classic["definition"]["participants"]
    assert classic["builtin"]


async def test_create_and_run_custom_workflow(client):
    body = {
        "name": "tiny", "description": "d",
        "definition": {"nodes": [
            {"id": "a", "role": "researcher", "count": 1},
            {"id": "b", "role": "synthesizer", "count": 1, "depends_on": ["a"], "terminal": True},
        ]},
    }
    wf = (await client.post("/api/workflows", json=body)).json()
    assert wf["id"]
    pid = (await client.post("/api/projects", json={"name": "wf"})).json()["id"]
    cid = (await client.post("/api/conversations",
                              json={"project_id": pid, "mode": "workflow"})).json()["id"]
    r = await client.post(f"/api/conversations/{cid}/runs", json={
        "question": "design a todo app", "mode": "workflow", "config": wf["definition"]})
    run_id = r.json()["run_id"]
    for _ in range(200):
        run = (await client.get(f"/api/runs/{run_id}")).json()
        if run["status"] in ("ok", "failed"):
            break
        import asyncio; await asyncio.sleep(0.1)
    assert run["status"] == "ok", run.get("error")
    assert run["trace"]["totals"]["calls"] == 2


async def test_builtin_workflow_cannot_delete(client):
    wfs = (await client.get("/api/workflows")).json()
    wid = next(w["id"] for w in wfs if w["name"] == "Coding gauntlet")
    r = await client.delete(f"/api/workflows/{wid}")
    assert r.status_code == 400


async def test_prompt_crud_and_variables(client):
    r = await client.get("/api/prompts")
    assert r.status_code == 200
    assert any("{{question}}" in t["content"] for t in r.json())
    created = (await client.post("/api/prompts", json={
        "name": "t-custom", "kind": "role",
        "content": "Role for {{question}} with {{research}} and {{unknown_var}}"})).json()
    assert set(created["variables"]) == {"question", "research", "unknown_var"}
    patched = (await client.patch(f"/api/prompts/{created['id']}",
                                  json={"content": "now {{project_context}}"})).json()
    assert patched["variables"] == ["project_context"]
    await client.delete(f"/api/prompts/{created['id']}")


async def test_roles_include_builtins(client):
    roles = (await client.get("/api/roles")).json()
    keys = {r["key"] for r in roles}
    assert {"researcher", "critic", "skeptic", "synthesizer", "judge", "programmer"}.issubset(keys)


async def test_router_preview(client):
    r = await client.post("/api/router/preview", json={"text": "debug this python stack trace"})
    data = r.json()
    assert data["plan"]
    assert data["selected"]
    assert any("coding" in x for x in data["profile"]["reasons"])
