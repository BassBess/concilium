"""Provider registry: adapter type registry + configured-instance manager.

* Adapter *types* self-register via ``@register_provider("openai")`` and are
  auto-discovered by importing every module in this package.
* Provider *instances* (rows of :class:`ProviderConfig`) are loaded from the
  database; environment-variable credentials are merged in as a fallback.
"""
from __future__ import annotations

import importlib
import pkgutil
from typing import Any

from sqlalchemy import select

from ..config import env_for_provider
from ..db import SessionLocal
from ..models import ProviderConfig as ProviderConfigRow
from ..security import decrypt
from .base import ModelInfo, ProviderAdapter

# ----------------------------------------------------------------------------
# Adapter type registry (populated via decorator / auto-import)
# ----------------------------------------------------------------------------

ADAPTER_TYPES: dict[str, type[ProviderAdapter]] = {}


def register_provider(type_key: str):
    def deco(cls: type[ProviderAdapter]):
        cls.type_key = type_key
        ADAPTER_TYPES[type_key] = cls
        return cls

    return deco


def _autodiscover() -> None:
    import app.providers as providers_pkg

    for mod in pkgutil.iter_modules(providers_pkg.__path__):
        name = mod.name
        if name in ("base", "catalog", "registry") or name.startswith("_"):
            continue
        importlib.import_module(f"app.providers.{name}")


_autodiscover()


def provider_type_metadata() -> list[dict[str, Any]]:
    """Describe all known adapter types for the UI's 'add provider' dialog."""
    out = []
    for key, cls in sorted(ADAPTER_TYPES.items()):
        out.append(
            {
                "type": key,
                "display_name": cls.display_name,
                "kind": cls.kind,
                "docs_url": cls.docs_url,
                "signup_url": cls.signup_url,
                "secret_fields": list(cls.secret_fields),
                "setting_fields": list(cls.setting_fields),
                "default_base_url": cls.default_base_url,
                "supports_multiple_instances": cls.supports_multiple_instances,
            }
        )
    return out


# ----------------------------------------------------------------------------
# Configured-instance manager
# ----------------------------------------------------------------------------

class ProviderRegistry:
    def __init__(self) -> None:
        self._instances: dict[str, ProviderAdapter] = {}
        self._loaded = False

    async def reload(self) -> None:
        instances: dict[str, ProviderAdapter] = {}
        async with SessionLocal() as session:
            rows = (await session.execute(select(ProviderConfigRow))).scalars().all()
            for row in rows:
                adapter = self._build_adapter(row)
                if adapter is not None:
                    instances[row.id] = adapter
        self._instances = instances
        self._loaded = True

    def _build_adapter(self, row: ProviderConfigRow) -> ProviderAdapter | None:
        cls = ADAPTER_TYPES.get(row.type)
        if cls is None:
            return None
        settings = dict(row.settings or {})
        # Decrypt stored secrets
        for k in list(settings.keys()):
            if isinstance(settings[k], str) and settings[k].startswith("enc::"):
                settings[k] = decrypt(settings[k])
        # Merge environment credentials (UI/DB takes precedence)
        for k, v in env_for_provider(row.type).items():
            settings.setdefault(k, v)
        # Built-in local providers get default URLs
        if not settings.get("base_url") and cls.default_base_url:
            settings["base_url"] = cls.default_base_url
        return cls(instance_id=row.id, label=row.label, settings=settings, enabled=row.enabled)

    async def ensure_loaded(self) -> None:
        if not self._loaded:
            await self.reload()

    def all(self) -> list[ProviderAdapter]:
        return list(self._instances.values())

    def enabled(self) -> list[ProviderAdapter]:
        return [a for a in self._instances.values() if a.enabled]

    def get(self, instance_id: str) -> ProviderAdapter | None:
        return self._instances.get(instance_id)

    def first_of_type(self, provider_type: str, enabled_only: bool = False) -> ProviderAdapter | None:
        for adapter in self._instances.values():
            if adapter.type_key == provider_type and (not enabled_only or adapter.enabled):
                return adapter
        return None

    def resolve(self, ref: dict[str, str] | str | None) -> ProviderAdapter | None:
        """Resolve a model-reference to an adapter.

        ref may be ``{"provider_id": "<uuid>"}``, ``{"provider_type": "groq"}``
        or a plain instance id string.
        """
        if not ref:
            return None
        if isinstance(ref, str):
            return self.get(ref) or self.first_of_type(ref)
        if pid := ref.get("provider_id"):
            return self.get(pid)
        if ptype := ref.get("provider_type"):
            return self.first_of_type(ptype, enabled_only=True)
        return None

    async def list_all_models(self) -> list[dict[str, Any]]:
        """Static catalog for every provider type (availability marked).

        Dynamic (live-listed) models are fetched per instance through
        ``GET /api/providers/{id}/models?refresh=1`` to avoid hammering every
        endpoint at startup.
        """
        await self.ensure_loaded()
        out: list[dict[str, Any]] = []
        configured_types: dict[str, list[ProviderAdapter]] = {}
        for adapter in self._instances.values():
            configured_types.setdefault(adapter.type_key, []).append(adapter)

        from .catalog import CATALOG

        for ptype, models in CATALOG.items():
            instances = configured_types.get(ptype, [])
            configured = any(a.is_configured() for a in instances)
            enabled = any(a.enabled and a.is_configured() for a in instances)
            instance_id = instances[0].instance_id if instances else None
            cls = ADAPTER_TYPES.get(ptype)
            for m in models:
                d = m.to_dict()
                d.update(
                    {
                        "provider_type": ptype,
                        "provider_name": cls.display_name if cls else ptype,
                        "instance_id": instance_id,
                        "configured": configured,
                        "available": enabled,
                        "dynamic": False,
                    }
                )
                out.append(d)
        return out


# Module-level singleton
registry = ProviderRegistry()
