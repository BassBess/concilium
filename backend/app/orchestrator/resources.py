"""Generic computational resources available to Concilium."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceSpec:
    """A computational resource Concilium can potentially schedule."""

    id: str
    kind: str
    name: str = ""
    provider: str = ""
    capabilities: list[str] = field(default_factory=list)

    # Scheduling hints.
    free: bool = False
    local: bool = False
    estimated_latency_ms: int | None = None
    estimated_cost: float | None = None

    # Arbitrary resource-specific information.
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
    """A scheduler decision."""

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
    """Registry of resources currently available to Concilium."""

    def __init__(self) -> None:
        self._resources: dict[str, ResourceSpec] = {}

    def register(self, resource: ResourceSpec) -> None:
        if not resource.id.strip():
            raise ValueError("Resource id cannot be empty")
        self._resources[resource.id] = resource

    def unregister(self, resource_id: str) -> None:
        self._resources.pop(resource_id, None)

    def get(self, resource_id: str) -> ResourceSpec | None:
        return self._resources.get(resource_id)

    def all(self) -> list[ResourceSpec]:
        return list(self._resources.values())

    def clear(self) -> None:
        self._resources.clear()
