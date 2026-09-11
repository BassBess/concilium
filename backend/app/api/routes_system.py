"""App settings, roles, routing preview, and the first-run setup wizard."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Conversation, Message, Project, Run, Setting
from ..orchestrator.engine import execute_run
from ..orchestrator.events import bus
from ..orchestrator.presets import auto_council_config
from ..orchestrator.quotas import quota_manager
from ..orchestrator.router import build_plan
from ..orchestrator.roles import all_builtin_roles
from ..providers.registry import registry
from .deps import require_auth

router = APIRouter(prefix="/api", tags=["system"])


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------

DEFAULT_APP = {
    "default_concurrency": 4,
    "prefer_free": True,
    "failover": True,
    "default_mode": "council",
    "default_council_size": 3,
}


async def _get_setting(key: str, default: dict) -> dict:
    async with SessionLocal() as s:
        row = await s.get(Setting, key)
        return row.value if row else default


async def _set_setting(key: str, value: dict) -> None:
    async with SessionLocal() as s:
        row = await s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))
        await s.commit()


@router.get("/settings/app")
async def get_app_settings(_: dict = Depends(require_auth)):
    return await _get_setting("app", DEFAULT_APP)


@router.put("/settings/app")
async def set_app_settings(body: dict, _: dict = Depends(require_auth)):
    current = await _get_setting("app", DEFAULT_APP)
    current.update(body)
    await _set_setting("app", current)
    return current


@router.get("/roles")
async def list_roles(_: dict = Depends(require_auth)):
    """Built-in roles + custom roles stored as prompt templates."""
    from ..models import PromptTemplate

    async with SessionLocal() as s:
        custom = (await s.execute(
            select(PromptTemplate).where(PromptTemplate.kind == "role")
        )).scalars().all()
    roles = all_builtin_roles()
    for r in custom:
        roles.append({"key": r.id, "name": r.name, "prompt": r.content, "builtin": False})
    return roles


class PlanRequest(BaseModel):
    text: str = Field(max_length=50_000)
    has_images: bool = False
    prefer_free: bool = True


@router.post("/router/preview")
async def router_preview(body: PlanRequest, _: dict = Depends(require_auth)):
    await registry.ensure_loaded()
    return await build_plan(body.text, registry.enabled(), has_images=body.has_images,
                            prefer_free=body.prefer_free)


# ---------------------------------------------------------------------------
# Setup wizard
# ---------------------------------------------------------------------------

@router.get("/wizard/status")
async def wizard_status(_: dict = Depends(require_auth)):
    setup = await _get_setting("setup_completed", {"done": False})
    await registry.ensure_loaded()
    providers = []
    model_count = 0
    for a in registry.all():
        models = []
        try:
            models = await a.get_models()
        except Exception:
            pass
        model_count += len(models) if a.enabled and a.is_configured() else 0
        providers.append({
            "id": a.instance_id, "type": a.type_key, "label": a.label,
            "enabled": a.enabled, "configured": a.is_configured(),
            "kind": a.kind, "model_count": len(models),
        })
    return {"setup_completed": setup.get("done", False),
            "providers": providers, "enabled_model_count": model_count}


class WizardDetectIn(BaseModel):
    enable_local: bool = True
    enable_remote: bool = True


@router.post("/wizard/detect")
async def wizard_detect(body: WizardDetectIn, _: dict = Depends(require_auth)):
    """Refresh model lists and test connections for every enabled provider."""
    await registry.ensure_loaded()
    results = []
    for a in registry.all():
        if a.kind == "local" and not body.enable_local:
            continue
        if a.kind == "remote" and not body.enable_remote:
            continue
        health = await a.health_check()
        models = []
        if health["status"] == "ok":
            a.models_cache = None
            try:
                models = [m.to_dict() for m in await a.get_models(force_refresh=True)]
            except Exception as exc:  # noqa: BLE001
                health = {"status": "error", "models": 0, "detail": str(exc)[:300]}
        results.append({
            "id": a.instance_id, "type": a.type_key, "label": a.label,
            "kind": a.kind, "configured": a.is_configured(),
            "health": health, "models": [m["id"] for m in models],
        })
    return {"results": results}


@router.get("/wizard/default-council")
async def wizard_default_council(size: int = 3, _: dict = Depends(require_auth)):
    return await auto_council_config(max(1, min(size, 6)))


class WizardTestIn(BaseModel):
    question: str = "What are the trade-offs between SQL and NoSQL databases? Be concise."


@router.post("/wizard/test")
async def wizard_test(body: WizardTestIn, _: dict = Depends(require_auth)):
    """Run a real (mock-or-real) council so the user can see the system work."""
    async with SessionLocal() as s:
        project = (await s.execute(select(Project).order_by(Project.created_at).limit(1))).scalars().first()
        if not project:
            raise HTTPException(400, "no default project")
        conv = Conversation(project_id=project.id, title="Setup test", mode="council")
        s.add(conv)
        await s.commit()
        conv_id = conv.id
        config = await auto_council_config(3, body.question)
        run = Run(conversation_id=conv_id, mode="council", question=body.question,
                  status="running", trace={"config": config})
        s.add(run)
        s.add(Message(conversation_id=conv_id, run_id=run.id, role="user",
                      content=body.question, kind="chat"))
        await s.commit()
        run_id, project_id = run.id, project.id
    task = asyncio.create_task(execute_run(run_id, conv_id, "council", config,
                                           body.question, project_id))
    task.add_done_callback(lambda t: None)
    return {"run_id": run_id, "conversation_id": conv_id, "config": config}


class WizardCompleteIn(BaseModel):
    setup_completed: bool = True
    settings: dict = {}


@router.post("/wizard/complete")
async def wizard_complete(body: WizardCompleteIn, _: dict = Depends(require_auth)):
    await _set_setting("setup_completed", {"done": body.setup_completed})
    if body.settings:
        current = await _get_setting("app", DEFAULT_APP)
        current.update(body.settings)
        await _set_setting("app", current)
    return {"ok": True}
