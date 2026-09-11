"""Saved workflows and prompt templates (reusable orchestration assets)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Project, PromptTemplate, Workflow
from .deps import require_auth

router = APIRouter(prefix="/api", tags=["library"])


# ---------------------------------------------------------------------------
# Workflows
# ---------------------------------------------------------------------------

def _wf_dict(w: Workflow) -> dict:
    return {"id": w.id, "name": w.name, "description": w.description,
            "project_id": w.project_id, "definition": w.definition,
            "builtin": w.builtin, "created_at": w.created_at.isoformat()}


@router.get("/workflows")
async def list_workflows(project_id: str | None = None, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        q = select(Workflow)
        if project_id:
            q = q.where((Workflow.project_id == project_id) | (Workflow.project_id.is_(None)))
        rows = (await s.execute(q.order_by(Workflow.builtin.desc(), Workflow.name))).scalars().all()
        return [_wf_dict(w) for w in rows]


class WorkflowIn(BaseModel):
    name: str = Field(max_length=120)
    description: str = ""
    project_id: str | None = None
    definition: dict = Field(default_factory=dict)


@router.post("/workflows")
async def create_workflow(body: WorkflowIn, _: dict = Depends(require_auth)):
    if not body.definition:
        raise HTTPException(400, "definition is required")
    async with SessionLocal() as s:
        if body.project_id and not await s.get(Project, body.project_id):
            raise HTTPException(404, "project not found")
        w = Workflow(name=body.name, description=body.description, project_id=body.project_id,
                     definition=body.definition)
        s.add(w)
        await s.commit()
        return _wf_dict(w)


class WorkflowPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    definition: dict | None = None


@router.patch("/workflows/{wid}")
async def update_workflow(wid: str, body: WorkflowPatch, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        w = await s.get(Workflow, wid)
        if not w:
            raise HTTPException(404, "workflow not found")
        if body.name is not None:
            w.name = body.name
        if body.description is not None:
            w.description = body.description
        if body.definition is not None:
            w.definition = body.definition
        await s.commit()
        return _wf_dict(w)


@router.delete("/workflows/{wid}")
async def delete_workflow(wid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        w = await s.get(Workflow, wid)
        if not w:
            raise HTTPException(404, "workflow not found")
        if w.builtin:
            raise HTTPException(400, "built-in workflows can't be deleted (duplicate them instead)")
        await s.delete(w)
        await s.commit()
        return {"ok": True}


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

def _tpl_dict(t: PromptTemplate) -> dict:
    from ..orchestrator.prompts import extract_variables

    return {"id": t.id, "name": t.name, "kind": t.kind, "content": t.content,
            "variables": extract_variables(t.content), "created_at": t.created_at.isoformat()}


@router.get("/prompts")
async def list_prompts(kind: str | None = None, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        q = select(PromptTemplate)
        if kind:
            q = q.where(PromptTemplate.kind == kind)
        rows = (await s.execute(q.order_by(PromptTemplate.name))).scalars().all()
        return [_tpl_dict(t) for t in rows]


class PromptIn(BaseModel):
    name: str = Field(max_length=120)
    kind: str = "system"
    content: str = ""


@router.post("/prompts")
async def create_prompt(body: PromptIn, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        t = PromptTemplate(name=body.name, kind=body.kind, content=body.content)
        s.add(t)
        await s.commit()
        return _tpl_dict(t)


class PromptPatch(BaseModel):
    name: str | None = None
    kind: str | None = None
    content: str | None = None


@router.patch("/prompts/{tid}")
async def update_prompt(tid: str, body: PromptPatch, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        t = await s.get(PromptTemplate, tid)
        if not t:
            raise HTTPException(404, "template not found")
        for k in ("name", "kind", "content"):
            v = getattr(body, k)
            if v is not None:
                setattr(t, k, v)
        await s.commit()
        return _tpl_dict(t)


@router.delete("/prompts/{tid}")
async def delete_prompt(tid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        t = await s.get(PromptTemplate, tid)
        if not t:
            raise HTTPException(404, "template not found")
        await s.delete(t)
        await s.commit()
        return {"ok": True}
