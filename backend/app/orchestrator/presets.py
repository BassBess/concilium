"""Auto-configuration helpers: build a sensible default council from whatever
models are available, and suggest model pools for workflow fan-out.
"""
from __future__ import annotations

from typing import Any

from ..providers.registry import registry
from .roles import role_name
from .router import analyze_task, rank_candidates

DEFAULT_COUNCIL_ROLES = ["researcher", "critic", "creative"]


async def auto_council_config(size: int = 3, question: str = "") -> dict[str, Any]:
    """Build a council config using diverse, preferably free/local, models."""
    await registry.ensure_loaded()
    adapters = registry.enabled()
    profile = analyze_task(question or "General problem solving requiring breadth and rigor.")
    ranked = await rank_candidates(adapters, profile, prefer_free=True, n=max(size + 1, 4), diverse=True)

    participants = []
    used_uids: set[str] = set()
    roles = list(DEFAULT_COUNCIL_ROLES)
    picked = 0
    for c in ranked:
        uid = f"{c.provider_id}:{c.model.id}"
        if uid in used_uids:
            continue
        used_uids.add(uid)
        role = roles[picked % len(roles)] if picked < len(roles) else "researcher"
        participants.append({
            "label": f"{c.provider_label} · {c.model.id} ({role_name(role)})",
            "role": role,
            "ref": {"provider_id": c.provider_id, "model": c.model.id},
            "allow_tools": True,
        })
        picked += 1
        if picked >= size:
            break

    # synthesizer: next unused candidate, else reuse first
    synth = None
    for c in ranked:
        uid = f"{c.provider_id}:{c.model.id}"
        if uid not in used_uids:
            synth = {"provider_id": c.provider_id, "model": c.model.id}
            break
    if synth is None and ranked:
        synth = {"provider_id": ranked[0].provider_id, "model": ranked[0].model.id}

    return {
        "participants": participants,
        "parallel": True,
        "critique_rounds": 1,
        "revision": True,
        "research": False,
        "failover": True,
        "temperature": 0.7,
        "synthesizer": {"role": "synthesizer", "ref": synth},
    }
