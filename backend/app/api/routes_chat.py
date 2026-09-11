"""Projects, conversations, messages, documents and run execution (SSE)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..db import SessionLocal
from ..models import Conversation, Document, Message, Project, Run
from ..orchestrator.engine import execute_run
from ..orchestrator.events import bus
from .deps import require_auth

router = APIRouter(prefix="/api", tags=["chat"])

_BG_TASKS: set[asyncio.Task] = set()

# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str = "New project"
    description: str = ""
    config: dict = Field(default_factory=dict)


@router.get("/projects")
async def list_projects(_: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        rows = (await s.execute(select(Project).order_by(Project.created_at))).scalars().all()
        return [_project_dict(p) for p in rows]


@router.post("/projects")
async def create_project(body: ProjectIn, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        p = Project(name=body.name[:200], description=body.description, config=body.config)
        s.add(p)
        await s.commit()
        return _project_dict(p)


@router.patch("/projects/{pid}")
async def update_project(pid: str, body: dict, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        p = await s.get(Project, pid)
        if not p:
            raise HTTPException(404, "project not found")
        for k in ("name", "description", "config"):
            if k in body:
                setattr(p, k, body[k])
        await s.commit()
        return _project_dict(p)


@router.delete("/projects/{pid}")
async def delete_project(pid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        p = await s.get(Project, pid)
        if p:
            await s.delete(p)
            await s.commit()
        return {"ok": True}


def _project_dict(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description,
            "config": p.config or {}, "created_at": p.created_at.isoformat()}


# ---------------------------------------------------------------------------
# Conversations
# ---------------------------------------------------------------------------

class ConversationIn(BaseModel):
    project_id: str
    title: str = "New conversation"
    mode: str = "council"
    config: dict = Field(default_factory=dict)


@router.get("/projects/{pid}/conversations")
async def list_conversations(pid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Conversation).where(Conversation.project_id == pid)
            .order_by(Conversation.updated_at.desc())
        )).scalars().all()
        return [_conv_dict(c) for c in rows]


@router.post("/conversations")
async def create_conversation(body: ConversationIn, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        if not await s.get(Project, body.project_id):
            raise HTTPException(404, "project not found")
        c = Conversation(project_id=body.project_id, title=body.title[:300],
                         mode=body.mode, config=body.config)
        s.add(c)
        await s.commit()
        return _conv_dict(c)


@router.patch("/conversations/{cid}")
async def update_conversation(cid: str, body: dict, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        c = await s.get(Conversation, cid)
        if not c:
            raise HTTPException(404, "conversation not found")
        for k in ("title", "mode", "config"):
            if k in body:
                setattr(c, k, body[k])
        await s.commit()
        return _conv_dict(c)


@router.delete("/conversations/{cid}")
async def delete_conversation(cid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        c = await s.get(Conversation, cid)
        if c:
            await s.delete(c)
            await s.commit()
        return {"ok": True}


def _conv_dict(c: Conversation) -> dict:
    return {"id": c.id, "project_id": c.project_id, "title": c.title, "mode": c.mode,
            "config": c.config or {}, "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat()}


@router.get("/conversations/{cid}/messages")
async def list_messages(cid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.created_at)
        )).scalars().all()
        return [_msg_dict(m) for m in rows]


def _msg_dict(m: Message) -> dict:
    return {
        "id": m.id, "conversation_id": m.conversation_id, "run_id": m.run_id,
        "role": m.role, "content": m.content, "agent": m.agent,
        "provider": m.provider, "model": m.model,
        "input_tokens": m.input_tokens, "output_tokens": m.output_tokens,
        "cost": m.cost, "latency_ms": m.latency_ms, "status": m.status,
        "kind": m.kind, "meta": m.meta or {}, "created_at": m.created_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Documents (project memory / knowledge base)
# ---------------------------------------------------------------------------

_TEXT_EXTS = {".txt", ".md", ".markdown", ".csv", ".json", ".log", ".py", ".js",
              ".ts", ".tsx", ".jsx", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".html", ".css", ".rst"}


@router.get("/projects/{pid}/documents")
async def list_documents(pid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Document).where(Document.project_id == pid).order_by(Document.created_at.desc())
        )).scalars().all()
        return [{"id": d.id, "filename": d.filename, "mime": d.mime, "size": d.size,
                 "preview": d.content[:200], "created_at": d.created_at.isoformat()} for d in rows]


@router.post("/projects/{pid}/documents")
async def upload_document(pid: str, file: UploadFile, _: dict = Depends(require_auth)):
    fname = (file.filename or "document").lower()
    ext = "." + fname.rsplit(".", 1)[-1] if "." in fname else ""
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "file too large (2MB limit per document)")
    if ext in _TEXT_EXTS or (file.content_type or "").startswith("text") or fname.endswith(".md"):
        content = raw.decode("utf-8", errors="replace")
    elif ext in (".pdf", ".docx", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip"):
        raise HTTPException(415, f"{ext or 'binary'} files are not parsed in this version; "
                                "paste text or upload .txt/.md/.csv/.json/.py")
    else:
        # best-effort text decode
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, "only UTF-8 text documents are supported") from None
    async with SessionLocal() as s:
        if not await s.get(Project, pid):
            raise HTTPException(404, "project not found")
        d = Document(project_id=pid, filename=file.filename or "document",
                     mime=file.content_type or "text/plain", content=content, size=len(raw))
        s.add(d)
        await s.commit()
        return {"id": d.id, "filename": d.filename, "size": d.size}


@router.delete("/documents/{did}")
async def delete_document(did: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        d = await s.get(Document, did)
        if d:
            await s.delete(d)
            await s.commit()
        return {"ok": True}


# ---------------------------------------------------------------------------
# Runs (orchestrated executions)
# ---------------------------------------------------------------------------

class RunStart(BaseModel):
    question: str = Field(max_length=200_000)
    mode: str = "council"  # single|router|council|workflow|dynamic
    config: dict[str, Any] = Field(default_factory=dict)


def _validate_run(mode: str, config: dict) -> None:
    if mode not in ("single", "router", "council", "workflow", "dynamic"):
        raise HTTPException(400, f"unknown mode {mode}")
    if mode == "council":
        n = len(config.get("participants", []))
        if n == 0:
            raise HTTPException(400, "council needs at least one participant")
        if n > 12:
            raise HTTPException(400, "at most 12 council participants")
        if int(config.get("critique_rounds", 1)) > 5:
            raise HTTPException(400, "at most 5 critique rounds")
    if mode == "workflow":
        nodes = (config.get("workflow") or config).get("nodes", [])
        if not nodes or len(nodes) > 30:
            raise HTTPException(400, "workflow needs 1-30 nodes")


@router.post("/conversations/{cid}/runs")
async def start_run(cid: str, body: RunStart, _: dict = Depends(require_auth)):
    _validate_run(body.mode, body.config)
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "empty question")
    async with SessionLocal() as s:
        conv = await s.get(Conversation, cid)
        if not conv:
            raise HTTPException(404, "conversation not found")
        conv.mode = body.mode
        run = Run(conversation_id=cid, mode=body.mode, question=question,
                  status="running", trace={"config": body.config})
        s.add(run)
        s.add(Message(conversation_id=cid, run_id=run.id, role="user",
                      content=question, kind="chat"))
        await s.commit()
        run_id, project_id = run.id, conv.project_id
    task = asyncio.create_task(execute_run(run_id, cid, body.mode, body.config,
                                           question, project_id))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return {"run_id": run_id}


@router.get("/runs/{run_id}")
async def get_run(run_id: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        r = await s.get(Run, run_id)
        if not r:
            raise HTTPException(404, "run not found")
        return {"id": r.id, "conversation_id": r.conversation_id, "mode": r.mode,
                "status": r.status, "question": r.question, "final": r.final,
                "error": r.error, "trace": r.trace or {}, "created_at": r.created_at.isoformat()}


@router.get("/conversations/{cid}/runs")
async def list_runs(cid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(Run).where(Run.conversation_id == cid).order_by(Run.created_at.desc()).limit(50)
        )).scalars().all()
        return [{"id": r.id, "mode": r.mode, "status": r.status, "question": r.question,
                 "final": (r.final or "")[:400], "error": r.error,
                 "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, token: str | None = None):
    """Server-Sent Events: replays trace history then live events."""
    # Auth via query token for EventSource compatibility.
    from ..config import get_settings as _gs
    from ..security import verify_token

    if _gs().password:
        if not verify_token(token or ""):
            raise HTTPException(401, "auth required")

    async with SessionLocal() as s:
        run = await s.get(Run, run_id)
        persisted = list((run.trace or {}).get("events", [])) if run else []
        finished = run.status if run else None
        final_text = run.final if run else ""
        error_text = run.error if run else ""

    async def generator():
        sent = 0
        # replay persisted events (covers reconnects / server-side joins)
        for evt in persisted:
            yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"
            sent += 1
        if finished in ("ok", "failed") and sent >= len(persisted):
            # nothing live to wait for
            if finished == "ok":
                yield f"event: run_finished\ndata: {json.dumps({'type': 'run_finished', 'final': final_text}, ensure_ascii=False)}\n\n"
            else:
                yield f"event: run_failed\ndata: {json.dumps({'type': 'run_failed', 'error': error_text})}\n\n"
            return
        async for evt in bus.subscribe(run_id):
            yield f"event: {evt['type']}\ndata: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(generator(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
