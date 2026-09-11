"""Shared FastAPI dependencies (auth, tool/provider loading)."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Query

from ..config import get_settings
from ..security import verify_token

settings = get_settings()


async def require_auth(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),  # for EventSource (cannot set headers)
) -> dict:
    if not settings.password:
        return {"sub": "local"}
    raw = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:]
    raw = raw or token
    payload = verify_token(raw or "")
    if not payload:
        raise HTTPException(status_code=401, detail="Authentication required")
    return payload


async def load_tool_configs() -> None:
    """Refresh the in-memory tool registry from database ToolConfig rows."""
    from sqlalchemy import select

    from ..db import SessionLocal
    from ..models import ToolConfig
    from ..tools.base import tool_registry

    async with SessionLocal() as session:
        rows = list((await session.execute(select(ToolConfig))).scalars().all())
    # Decrypt tool secrets before handing settings to tool instances.
    from ..security import decrypt, is_encrypted
    for r in rows:
        r.settings = {
            k: (decrypt(v) if isinstance(v, str) and is_encrypted(v) else v)
            for k, v in (r.settings or {}).items()
        }
    tool_registry.configure(rows)
