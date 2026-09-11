"""Provider instances, model catalog, health checks and discovery."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from ..config import env_for_provider
from ..db import SessionLocal
from ..models import ProviderConfig
from ..providers.catalog import CATALOG
from ..providers.registry import ADAPTER_TYPES, registry
from ..security import decrypt, encrypt, is_encrypted, mask_secret
from .deps import require_auth

router = APIRouter(prefix="/api", tags=["providers"])

LIMIT_FIELDS = {"rpm", "tpm", "daily_requests", "daily_tokens", "monthly_requests",
                "concurrency", "cooldown_seconds", "max_retries"}


def _public_settings(row: ProviderConfig) -> dict[str, Any]:
    cls = ADAPTER_TYPES.get(row.type)
    secret_fields = cls.secret_fields if cls else ("api_key",)
    out: dict[str, Any] = {}
    for k, v in (row.settings or {}).items():
        if k in secret_fields:
            plain = decrypt(v) if is_encrypted(v) else v
            out[k] = {"set": bool(plain), "masked": mask_secret(plain)}
        else:
            out[k] = v
    # environment fallback info
    env = env_for_provider(row.type)
    for k, v in env.items():
        if k in secret_fields and k not in out:
            out[k] = {"set": True, "masked": mask_secret(v), "source": "environment"}
    return out


def _instance_dict(row: ProviderConfig) -> dict[str, Any]:
    cls = ADAPTER_TYPES.get(row.type)
    adapter = registry.get(row.id)
    configured = False
    if adapter:
        # merge env like the runtime does
        configured = adapter.is_configured()
    return {
        "id": row.id, "type": row.type,
        "display_name": cls.display_name if cls else row.type,
        "kind": cls.kind if cls else "remote",
        "label": row.label, "enabled": row.enabled, "builtin": row.builtin,
        "settings": _public_settings(row),
        "configured": configured,
        "docs_url": cls.docs_url if cls else "",
        "signup_url": cls.signup_url if cls else "",
        "secret_fields": list(cls.secret_fields) if cls else ["api_key"],
        "setting_fields": list(cls.setting_fields) if cls else ["base_url"],
        "supports_multiple_instances": bool(getattr(cls, "supports_multiple_instances", False)),
    }


@router.get("/providers")
async def list_providers(_: dict = Depends(require_auth)):
    await registry.ensure_loaded()
    async with SessionLocal() as s:
        rows = (await s.execute(select(ProviderConfig).order_by(ProviderConfig.type))).scalars().all()
        return [_instance_dict(r) for r in rows]


@router.get("/providers/types")
async def provider_types(_: dict = Depends(require_auth)):
    from ..providers.registry import provider_type_metadata

    return provider_type_metadata()


class ProviderCreate(BaseModel):
    type: str
    label: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool = False
    settings: dict[str, Any] = {}


@router.post("/providers")
async def create_provider(body: ProviderCreate, _: dict = Depends(require_auth)):
    cls = ADAPTER_TYPES.get(body.type)
    if cls is None:
        raise HTTPException(400, f"unknown provider type '{body.type}'")
    if not cls.supports_multiple_instances:
        async with SessionLocal() as s:
            existing = (await s.execute(
                select(ProviderConfig).where(ProviderConfig.type == body.type)
            )).scalars().first()
        if existing:
            raise HTTPException(409, f"{cls.display_name} already exists; edit it instead")
    settings = dict(body.settings)
    if body.base_url:
        settings["base_url"] = body.base_url
    if body.api_key:
        settings["api_key"] = encrypt(body.api_key.strip())
    row = ProviderConfig(type=body.type, label=body.label or cls.display_name,
                         enabled=body.enabled, settings=settings, builtin=False)
    async with SessionLocal() as s:
        s.add(row)
        await s.commit()
    await registry.reload()
    return {"id": row.id}


class ProviderUpdate(BaseModel):
    label: str | None = None
    enabled: bool | None = None
    settings: dict[str, Any] | None = None
    # convenience for credential forms
    api_key: str | None = None
    base_url: str | None = None


@router.patch("/providers/{pid}")
async def update_provider(pid: str, body: ProviderUpdate, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        row = await s.get(ProviderConfig, pid)
        if not row:
            raise HTTPException(404, "provider not found")
        cls = ADAPTER_TYPES.get(row.type)
        if body.label is not None:
            row.label = body.label[:120]
        if body.enabled is not None:
            row.enabled = body.enabled
        settings = dict(row.settings or {})
        if body.settings:
            for k, v in body.settings.items():
                if k in cls.secret_fields:
                    if v and not str(v).startswith("enc::") and "•" not in str(v):
                        settings[k] = encrypt(str(v).strip())
                    # masked/unchanged → leave as-is; empty string clears it
                    if v == "":
                        settings.pop(k, None)
                else:
                    settings[k] = v
        if body.api_key:
            settings["api_key"] = encrypt(body.api_key.strip())
        if body.base_url is not None:
            settings["base_url"] = body.base_url.rstrip("/") if body.base_url else ""
        row.settings = settings
        await s.commit()
    await registry.reload()
    # new credentials/models may exist → bust live model caches
    for adapter in registry.all():
        adapter.models_cache = None
    return {"ok": True}


@router.delete("/providers/{pid}")
async def delete_provider(pid: str, _: dict = Depends(require_auth)):
    async with SessionLocal() as s:
        row = await s.get(ProviderConfig, pid)
        if not row:
            raise HTTPException(404, "provider not found")
        if row.builtin and row.type != "openai_compatible":
            raise HTTPException(400, "built-in providers can be disabled but not deleted")
        await s.delete(row)
        await s.commit()
    await registry.reload()
    return {"ok": True}


@router.get("/providers/{pid}/health")
async def provider_health(pid: str, _: dict = Depends(require_auth)):
    await registry.ensure_loaded()
    adapter = registry.get(pid)
    if not adapter:
        raise HTTPException(404, "provider instance not found")
    result = await adapter.health_check()
    result["enabled"] = adapter.enabled
    result["configured"] = adapter.is_configured()
    return result


@router.get("/providers/{pid}/models")
async def provider_models(pid: str, refresh: int = 0, _: dict = Depends(require_auth)):
    await registry.ensure_loaded()
    adapter = registry.get(pid)
    if not adapter:
        raise HTTPException(404, "provider instance not found")
    try:
        models = await adapter.get_models(force_refresh=bool(refresh))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"model discovery failed: {exc}") from exc
    return [
        {**m.to_dict(), "instance_id": pid, "provider_instance": adapter.label,
         "configured": adapter.is_configured(), "available": adapter.enabled and adapter.is_configured()}
        for m in models
    ]


# ---------------------------------------------------------------------------
# Combined model catalog (static + availability)
# ---------------------------------------------------------------------------

@router.get("/models")
async def model_catalog(_: dict = Depends(require_auth)):
    models = await registry.list_all_models()
    # annotate uid used by the UI
    for m in models:
        if m.get("instance_id"):
            m["uid"] = f"{m['instance_id']}:{m['id']}"
        else:
            m["uid"] = f"type:{m['provider_type']}:{m['id']}"
    return models


@router.get("/models/live")
async def live_models(_: dict = Depends(require_auth)):
    """Models across all ENABLED instances, refreshing live listings."""
    await registry.ensure_loaded()
    out = []
    for adapter in registry.enabled():
        try:
            models = await adapter.get_models(force_refresh=True)
        except Exception as exc:  # noqa: BLE001
            out.append({"error": str(exc), "instance_id": adapter.instance_id,
                        "provider": adapter.type_key, "models": []})
            continue
        out.append({
            "instance_id": adapter.instance_id, "provider": adapter.type_key,
            "label": adapter.label,
            "models": [m.to_dict() for m in models],
        })
    return out
