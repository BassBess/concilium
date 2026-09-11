"""Tool interface, registry and safety helpers.

Tools are first-class participants in orchestration. Every tool declares a JSON
schema for its parameters; execution is permission-gated by its ToolConfig row.
"""
from __future__ import annotations

import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# SSRF protection — used before fetching any URL influenced at runtime.
# Admin-configured service base URLs (SearXNG, local model servers) are set by
# the operator and are not passed through this check.
# ---------------------------------------------------------------------------

_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),     # includes cloud metadata 169.254.169.254
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def assert_public_url(url: str) -> None:
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("only http(s) URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL missing host")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValueError(f"host cannot resolve: {host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        for net in _BLOCKED_NETWORKS:
            if ip in net:
                raise ValueError(f"refusing to fetch private/link-local address ({ip}) — SSRF protection")


@dataclass
class ToolResult:
    ok: bool
    output: str = ""
    error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def text(self) -> str:
        return self.output if self.ok else f"ERROR: {self.error}"


@dataclass
class ToolContext:
    run_id: str = ""
    project_id: str | None = None
    emit: Callable[..., Awaitable[None]] | None = None  # async event callback
    extra: dict[str, Any] = field(default_factory=dict)


class Tool:
    key: str = "tool"
    display_name: str = "Tool"
    description: str = ""
    category: str = "general"
    enabled_by_default: bool = False
    # JSON schema for arguments
    parameters: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self, config: dict | None = None, permissions: dict | None = None):
        self.config = config or {}
        self.permissions = permissions or {}

    def available(self) -> bool:
        """Whether the tool is configured and usable right now."""
        return True

    def unavailable_reason(self) -> str:
        return ""

    def spec_dict(self) -> dict[str, Any]:
        return {"name": self.key, "description": self.description, "parameters": self.parameters}

    async def run(self, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    def __init__(self) -> None:
        self._tool_types: dict[str, type[Tool]] = {}
        self._instances: dict[str, Tool] = {}

    def register_type(self, cls: type[Tool]) -> type[Tool]:
        self._tool_types[cls.key] = cls
        return cls

    def configure(self, rows: list[Any]) -> None:
        """rows: ToolConfig ORM objects."""
        self._instances = {}
        enabled_keys = {r.key for r in rows if r.enabled}
        for r in rows:
            cls = self._tool_types.get(r.key)
            if not cls:
                continue
            inst = cls(config=r.settings or {}, permissions=r.permissions or {})
            self._instances[r.key] = inst
            inst.config["_enabled"] = r.enabled
        # Make sure all known types have an instance (disabled) for listing.
        for key, cls in self._tool_types.items():
            if key not in self._instances:
                inst = cls(config={"_enabled": False})
                self._instances[key] = inst

    def get(self, key: str) -> Tool | None:
        return self._instances.get(key)

    def enabled(self) -> list[Tool]:
        return [t for t in self._instances.values()
                if t.config.get("_enabled") and t.available()]

    def enabled_specs(self) -> list[dict[str, Any]]:
        return [t.spec_dict() for t in self.enabled()]

    def all_descriptors(self) -> list[dict[str, Any]]:
        out = []
        for key, t in sorted(self._instances.items()):
            enabled = bool(t.config.get("_enabled"))
            out.append({
                "key": key,
                "display_name": t.display_name,
                "description": t.description,
                "category": t.category,
                "enabled": enabled,
                "available": t.available() if enabled else False,
                "unavailable_reason": t.unavailable_reason() if enabled else "",
                "parameters": t.parameters,
                "permissions": t.permissions,
            })
        return out

    async def execute(self, key: str, arguments: dict[str, Any], ctx: ToolContext) -> ToolResult:
        tool = self.get(key)
        if tool is None or not tool.config.get("_enabled"):
            return ToolResult(ok=False, error=f"Tool '{key}' is disabled")
        if not tool.available():
            return ToolResult(ok=False, error=tool.unavailable_reason() or f"Tool '{key}' is not configured")
        try:
            result = await tool.run(arguments or {}, ctx)
        except Exception as exc:  # noqa: BLE001 — tools must never crash a run
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}")
        return result


tool_registry = ToolRegistry()
