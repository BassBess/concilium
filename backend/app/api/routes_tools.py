"""Tool registry: listing, permission/configuration changes, manual invoke."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from ..db import SessionLocal
from ..models import ToolConfig
from ..security import encrypt
from ..tools.base import ToolContext, tool_registry
from ..tools.calculator import CalculatorTool, DateTimeTool
from ..tools.knowledge import KnowledgeSearchTool
from ..tools.python_sandbox import PythonSandboxTool
from ..tools.websearch import WebSearchTool
from .deps import load_tool_configs, require_auth

# Ensure types are registered (import order safety).
for _t in (CalculatorTool, DateTimeTool, KnowledgeSearchTool, WebSearchTool, PythonSandboxTool):
    tool_registry.register_type(_t)

router = APIRouter(prefix="/api/tools", tags=["tools"])

SECRET_TOOL_FIELDS = {"tavily_api_key"}


@router.get("")
async def list_tools(_: dict = Depends(require_auth)):
    await load_tool_configs()
    descriptors = tool_registry.all_descriptors()
    # include config values (secrets masked)
    async with SessionLocal() as s:
        rows = {r.key: r for r in (await s.execute(select(ToolConfig))).scalars().all()}
    from ..security import decrypt, is_encrypted
    for r in rows.values():
        r.settings = {k: (decrypt(v) if isinstance(v, str) and is_encrypted(v) else v)
                      for k, v in (r.settings or {}).items()}
    for d in descriptors:
        r = rows.get(d["key"])
        settings = dict(r.settings) if r else {}
        masked = {}
        for k, v in settings.items():
            if k in SECRET_TOOL_FIELDS:
                masked[k] = {"set": bool(v), "masked": ("•" * 8 + str(v)[-4:]) if v else ""}
            else:
                masked[k] = v
        d["settings"] = masked
    return descriptors


class ToolUpdate(BaseModel):
    enabled: bool | None = None
    settings: dict | None = None
    permissions: dict | None = None


@router.patch("/{key}")
async def update_tool(key: str, body: ToolUpdate, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        r = await s.scalar(select(ToolConfig).where(ToolConfig.key == key))
        if not r:
            r = ToolConfig(key=key, enabled=False)
            s.add(r)
        if body.enabled is not None:
            r.enabled = body.enabled
        if body.permissions is not None:
            r.permissions = {**(r.permissions or {}), **body.permissions}
        if body.settings is not None:
            merged = dict(r.settings or {})
            for k, v in body.settings.items():
                if k in SECRET_TOOL_FIELDS:
                    if v == "" or v is None:
                        merged.pop(k, None)
                    elif isinstance(v, str) and "•" not in v:
                        merged[k] = encrypt(v.strip())
                    # masked → unchanged
                else:
                    merged[k] = v
            r.settings = merged
        await s.commit()
    await load_tool_configs()
    return {"ok": True}


class ToolInvoke(BaseModel):
    arguments: dict = {}
    project_id: str | None = None


@router.post("/{key}/invoke")
async def invoke_tool(key: str, body: ToolInvoke, _: dict = Depends(require_auth)):
    """Manual test invocation from the Tools page."""
    await load_tool_configs()
    ctx = ToolContext(project_id=body.project_id, extra={"project_id": body.project_id})
    result = await tool_registry.execute(key, body.arguments, ctx)
    return {"ok": result.ok, "output": result.output, "error": result.error,
            "data": result.data}
