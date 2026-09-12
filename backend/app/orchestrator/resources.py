"""Generic computational resources available to Concilium.

Resources intentionally sit above individual models.

A provider adapter becomes an LLM resource. The existing router then
selects the actual model within that resource. This keeps the scheduler
scalable when Concilium eventually has a very large number of providers,
APIs, local engines, tools, and execution backends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceSpec:
    id: str
    kind: str
    name: str = ""
    provider: str = ""
    capabilities: list[str] = field(default_factory=list)
    free: bool = False
    local: bool = False
    estimated_latency_ms: int | None = None
    estimated_cost: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name or self.id,
            "provider": self.provider,
            "capabilities": list(self.capabilities),
            "free": self.free,
            "local": self.local,
            "estimated_latency_ms": self.estimated_latency_ms,
            "estimated_cost": self.estimated_cost,
            "metadata": dict(self.metadata),
        }


@dataclass
class ResourceAssignment:
    resource: ResourceSpec
    score: float
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource.to_dict(),
            "score": round(self.score, 3),
            "reasons": list(self.reasons),
        }


class ResourceRegistry:
    """Registry for heterogeneous computational resources.

    Resources are keyed by stable IDs. Providers can register and
    unregister dynamically, which is important for a large and changing
    provider ecosystem.
    """

    def __init__(self) -> None:
        self._resources: dict[str, ResourceSpec] = {}

    def register(self, resource: ResourceSpec) -> None:
        resource_id = resource.id.strip()
        if not resource_id:
            raise ValueError("Resource id cannot be empty")
        self._resources[resource_id] = resource

    def register_many(self, resources: list[ResourceSpec]) -> None:
        for resource in resources:
            self.register(resource)

    def unregister(self, resource_id: str) -> None:
        self._resources.pop(resource_id, None)

    def get(self, resource_id: str) -> ResourceSpec | None:
        return self._resources.get(resource_id)

    def all(self) -> list[ResourceSpec]:
        return list(self._resources.values())

    def by_kind(self, kind: str) -> list[ResourceSpec]:
        return [r for r in self._resources.values() if r.kind == kind]

    def clear(self) -> None:
        self._resources.clear()

    def __len__(self) -> int:
        return len(self._resources)


def _provider_id(adapter: Any) -> str:
    return str(
        getattr(adapter, "instance_id", None)
        or getattr(adapter, "id", None)
        or getattr(adapter, "provider_id", None)
        or getattr(adapter, "provider_type", None)
        or adapter.__class__.__name__
    )


def _provider_type(adapter: Any) -> str:
    return str(
        getattr(adapter, "provider_type", None)
        or getattr(adapter, "type", None)
        or adapter.__class__.__name__
    )


def _provider_label(adapter: Any) -> str:
    return str(
        getattr(adapter, "label", None)
        or getattr(adapter, "name", None)
        or _provider_id(adapter)
    )


def _model_capabilities(models: list[Any]) -> list[str]:
    capabilities: set[str] = {"chat"}

    for model in models:
        for capability in getattr(model, "capabilities", []) or []:
            value = getattr(capability, "value", capability)
            capabilities.add(str(value))

    return sorted(capabilities)


async def register_llm_adapters(
    registry: ResourceRegistry,
    adapters: list[Any],
) -> list[ResourceSpec]:
    """Turn provider adapters into scheduler resources.

    One resource represents one provider adapter, NOT one model.

    This is deliberate: with hundreds or thousands of models, the
    scheduler should not need to duplicate the model router's job.
    """

    resources: list[ResourceSpec] = []

    for adapter in adapters:
        if not getattr(adapter, "enabled", True):
            continue

        try:
            models = await adapter.get_models()
        except Exception:
            # A broken provider must not prevent other providers from
            # entering the resource pool.
            models = []

        models = list(models or [])

        if not models:
            continue

        provider_id = _provider_id(adapter)
        provider_type = _provider_type(adapter)

        free = any(bool(getattr(model, "free_tier", False)) for model in models)
        local = provider_type.lower() in {"ollama", "lmstudio", "local"}

        context_windows = [
            int(getattr(model, "context_window", 0) or 0)
            for model in models
            if getattr(model, "context_window", None)
        ]

        capabilities = _model_capabilities(models)

        resource = ResourceSpec(
            id=f"llm:{provider_id}",
            kind="llm",
            name=_provider_label(adapter),
            provider=provider_type,
            capabilities=capabilities,
            free=free,
            local=local,
            metadata={
                "adapter_id": provider_id,
                "provider_type": provider_type,
                "model_count": len(models),
                "context_window": max(context_windows) if context_windows else 8192,
            },
        )

        registry.register(resource)
        resources.append(resource)

    return resources


async def build_resource_registry(
    adapters: list[Any],
) -> ResourceRegistry:
    """Build the current resource view from active provider adapters."""

    registry = ResourceRegistry()
    await register_llm_adapters(registry, adapters)
    return registry
