"""Web search via legitimate, keyed APIs only.

Supported backends (configure one in Tools settings):
* Tavily     — free tier, https://tavily.com  (TAVILY_API_KEY)
* SearXNG    — your own instance with JSON output enabled (SEARXNG_URL)

We never scrape search engines or bypass paywalls/captchas. When no backend is
configured the tool reports exactly what credential is required (no fake data).
Returned page text is clearly labeled as UNTRUSTED external content to mitigate
prompt injection (see ARCHITECTURE.md).
"""
from __future__ import annotations

import json

import httpx

from .base import Tool, ToolContext, ToolResult


class WebSearchTool(Tool):
    key = "web_search"
    display_name = "Web search"
    description = (
        "Search the public web for recent or external facts. Returns titles, URLs and "
        "short snippets with citations. Use for anything that may need up-to-date information."
    )
    category = "search"
    enabled_by_default = False
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }

    def _tavily_key(self) -> str:
        import os

        return self.config.get("tavily_api_key") or os.environ.get("TAVILY_API_KEY", "")

    def _searxng_url(self) -> str:
        import os

        return (self.config.get("searxng_url") or os.environ.get("SEARXNG_URL", "")).rstrip("/")

    def available(self) -> bool:
        return bool(self._tavily_key() or self._searxng_url())

    def unavailable_reason(self) -> str:
        return ("No search backend configured. Add a free Tavily API key (https://tavily.com) "
                "or a self-hosted SearXNG URL in Tools settings (or TAVILY_API_KEY / SEARXNG_URL).")

    async def _tavily(self, query: str, max_results: int) -> ToolResult:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {self._tavily_key()}",
                         "Content-Type": "application/json"},
                json={"query": query, "max_results": max_results,
                      "include_answer": True, "search_depth": "basic"},
            )
        if resp.status_code in (401, 403):
            return ToolResult(ok=False, error="Tavily rejected the API key (401/403).")
        if resp.status_code == 429:
            return ToolResult(ok=False, error="Tavily free-tier rate limit reached; try later.")
        if resp.status_code != 200:
            return ToolResult(ok=False, error=f"Tavily HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        lines = []
        if data.get("answer"):
            lines.append(f"Summary: {data['answer']}\n")
        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            lines.append(f"[{i}] {r.get('title', '')}\n    {r.get('url', '')}\n    {r.get('content', '')}")
        return ToolResult(ok=True, output="\n\n".join(lines) or "No results.",
                          data={"results": data.get("results", [])})

    async def _searxng(self, query: str, max_results: int) -> ToolResult:
        url = self._searxng_url()
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(
                f"{url}/search",
                params={"q": query, "format": "json", "language": "en"},
                headers={"User-Agent": "Concilium/1.0"},
            )
        if resp.status_code == 429:
            return ToolResult(ok=False, error="SearXNG rate limited the request.")
        if resp.status_code != 200:
            return ToolResult(ok=False, error=f"SearXNG HTTP {resp.status_code} (is JSON output enabled?)")
        data = resp.json()
        lines = []
        for i, r in enumerate(data.get("results", [])[:max_results], 1):
            lines.append(f"[{i}] {r.get('title', '')}\n    {r.get('url', '')}\n    {r.get('content', '')}")
        return ToolResult(ok=True, output="\n\n".join(lines) or "No results.")

    async def run(self, arguments, ctx: ToolContext) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        if not query:
            return ToolResult(ok=False, error="missing query")
        max_results = int(arguments.get("max_results") or 5)
        if self._tavily_key():
            return await self._tavily(query, max_results)
        if self._searxng_url():
            return await self._searxng(query, max_results)
        return ToolResult(ok=False, error=self.unavailable_reason())
