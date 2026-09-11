"""Authentication and server status endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import hmac

from ..config import get_settings
from ..security import sign_token
from .deps import require_auth

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


class LoginIn(BaseModel):
    password: str


@router.get("/status")
async def auth_status() -> dict:
    return {"password_required": bool(settings.password), "authenticated": not bool(settings.password)}


@router.post("/login")
async def login(body: LoginIn) -> dict:
    if not settings.password:
        return {"token": sign_token({"sub": "local"})}
    # Single-user local mode: constant-time compare to CONCILIUM_PASSWORD.
    if not hmac.compare_digest(body.password or "", settings.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    return {"token": sign_token({"sub": "local"})}


@router.get("/me")
async def me(user: dict = Depends(require_auth)) -> dict:
    return user
