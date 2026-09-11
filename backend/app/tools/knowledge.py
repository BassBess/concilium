"""Project knowledge-base search (uploaded documents).

A dependency-free lexical/BM25-ish retriever over a project's documents. Good
enough to ground answers; embeddings/vector search is a documented extension
point (Tool interface + embeddings capability).
"""
from __future__ import annotations

import math
import re
from collections import Counter

from sqlalchemy import select

from ..db import SessionLocal
from ..models import Document
from .base import Tool, ToolContext, ToolResult

_TOKEN_RE = re.compile(r"[a-z0-9_]+")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


class KnowledgeSearchTool(Tool):
    key = "knowledge_search"
    display_name = "Project knowledge search"
    description = (
        "Search the uploaded documents of the current project for relevant passages. "
        "Use when the answer may live in the project's files/knowledge base."
    )
    category = "retrieval"
    enabled_by_default = True
    parameters = {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
        },
        "required": ["query"],
    }

    def available(self) -> bool:
        return True

    async def run(self, arguments, ctx: ToolContext) -> ToolResult:
        query = str(arguments.get("query", "")).strip()
        top_k = int(arguments.get("top_k") or 4)
        project_id = (ctx.extra or {}).get("project_id") or ctx.project_id
        if not project_id:
            return ToolResult(ok=False, error="no project context")
        async with SessionLocal() as session:
            docs = (await session.execute(
                select(Document).where(Document.project_id == project_id)
            )).scalars().all()
        if not docs:
            return ToolResult(ok=True, output="(no documents uploaded to this project)")

        query_terms = _tokenize(query)
        scored: list[tuple[float, str, str, str]] = []
        N = max(1, len(docs))
        doc_freq: Counter[str] = Counter()
        doc_data = []
        for d in docs:
            toks = _tokenize(d.content)
            tf = Counter(toks)
            doc_data.append((d, tf, len(toks)))
            for term in tf:
                doc_freq[term] += 1
        for d, tf, length in doc_data:
            score = 0.0
            for qt in query_terms:
                if qt in tf:
                    idf = math.log(1 + N / (1 + doc_freq[qt]))
                    score += (tf[qt] / max(1, length / 500)) * idf
            if score > 0:
                scored.append((score, d.id, d.filename, d.content))
        scored.sort(reverse=True)
        if not scored:
            return ToolResult(ok=True, output="No relevant passages found.")
        lines = []
        for score, _id, filename, content in scored[:top_k]:
            # pull a window around the first query-term hit
            lower = content.lower()
            idx = min((lower.find(q) for q in query_terms if lower.find(q) >= 0), default=0)
            start = max(0, idx - 300)
            window = content[start : start + 1200]
            lines.append(f"### {filename} (relevance {score:.2f})\n{window}")
        return ToolResult(ok=True, output="\n\n".join(lines))
