"""Automatic model routing / specialization.

Analyzes the task and ranks every available (configured, enabled) model by:
capability coverage, context fit, free-tier preference, speed, cost and live
health (cooldowns). Fully deterministic, transparent and configurable — the
ranked plan (with reasons) is shown in the UI.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .quotas import quota_manager
from ..providers.base import Capability, ModelInfo, ProviderAdapter

# ---------------------------------------------------------------------------
# Task analysis
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(
    r"(\bdef \w+\b|\bclass \w+\b|\bfunction\b|\bconsole\.log\b|\bgit\b|\bstack ?trace\b|"
    r"\bdebug\b|\bpython\b|\bjavascript\b|\btypescript\b|\breact\b|\bsql\b|\bapi\b|"
    r"\bcompile\b|\bregex\b|\bdocker\b|```|\bnullpointer\b|\bexception\b|=>|;\s*$)",
    re.IGNORECASE,
)
_MATH_RE = re.compile(
    r"(\bcalculate\b|\bcompute\b|\bderivative\b|\bintegral\b|\bprobability\b|"
    r"\bequation\b|\bsolve for\b|\bmatrix\b|\btheorem\b|\b\d+\s*[+\-*/^=]\s*\d+\b|"
    r"\bsquare root\b|\bpolynomial\b)", re.IGNORECASE,
)
_RESEARCH_RE = re.compile(
    r"(\bresearch\b|\blatest\b|\bnews\b|\brecent\b|\bcurrent\b|\b202[4-9]\b|"
    r"\bsearch (the )?web\b|\bwhat'?s new\b|\bup to date\b|\bwho is the current\b)",
    re.IGNORECASE,
)
_REASON_RE = re.compile(
    r"(\bprove\b|\bproof\b|\bwhy does\b|\blogic\b|\bpuzzle\b|\briddle\b|"
    r"\bstep by step\b|\banalyze\b|\bcompare\b|\btrade-?off\b|\bshould i\b|"
    r"\bevaluate the argument\b)", re.IGNORECASE,
)
_CREATIVE_RE = re.compile(
    r"(\bwrite (a|an|the)\b|\bstory\b|\bpoem\b|\bessay\b|\bsong\b|\bbrainstorm\b|"
    r"\bmarketing\b|\bcatchy\b|\bcreative\b|\bdraft(ing)?\b|\bblog post\b)", re.IGNORECASE,
)
_SIMPLE_RE = re.compile(r"^(hi|hello|hey|thanks|thank you|ok|yes|no|good (morning|evening))\b[!.?]*$", re.IGNORECASE)

PROVIDER_SPEED = {
    "groq": 3, "lmstudio": 3, "ollama": 2, "google": 3, "openrouter": 2,
    "openai": 2, "mistral": 2, "cohere": 2, "together": 2, "xai": 1,
    "anthropic": 1, "mock": 4,
}


@dataclass
class TaskProfile:
    text: str
    role: str = ""
    estimated_tokens: int = 0
    needs_vision: bool = False
    needs_code: bool = False
    needs_math: bool = False
    needs_reasoning: bool = False
    needs_research: bool = False
    creative: bool = False
    simple: bool = False
    required: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def analyze_task(
    text: str,
    has_images: bool = False,
    attached_tokens: int = 0,
    role: str = "",
) -> TaskProfile:
    t = text or ""
    est = max(1, len(t) // 4) + attached_tokens
    p = TaskProfile(
        text=t,
        role=role,
        estimated_tokens=est,
        needs_vision=has_images,
    )
    if has_images:
        p.required.append(Capability.VISION.value)
        p.reasons.append("image input requires a vision model")
    if _CODE_RE.search(t):
        p.needs_code = True
        p.required.append(Capability.CODE.value)
        p.reasons.append("coding signals detected")
    if _MATH_RE.search(t):
        p.needs_math = True
        p.reasons.append("mathematical/quantitative task")
    if _REASON_RE.search(t):
        p.needs_reasoning = True
        p.required.append(Capability.REASONING.value)
        p.reasons.append("reasoning/analysis signals detected")
    if _RESEARCH_RE.search(t):
        p.needs_research = True
        p.reasons.append("recency/research signals — prefer models with tool access")
    if _CREATIVE_RE.search(t):
        p.creative = True
        p.reasons.append("creative/writing task")
    if _SIMPLE_RE.match(t.strip()) and est < 40:
        p.simple = True
        p.reasons.append("simple/small prompt — prefer fast & cheap")
    if est > 60_000:
        p.required.append(Capability.LONG_CONTEXT.value)
        p.reasons.append(f"long context (~{est:,} tokens)")
    return p


@dataclass
class Candidate:
    provider_id: str
    provider_type: str
    provider_label: str
    model: ModelInfo
    score: float
    reasons: list[str]
    cooling: bool

    def ref(self) -> dict[str, str]:
        return {"provider_id": self.provider_id, "model": self.model.id}

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "provider_type": self.provider_type,
            "provider_label": self.provider_label,
            "model": self.model.id,
            "model_info": self.model.to_dict(),
            "score": round(self.score, 3),
            "reasons": self.reasons,
            "cooling": self.cooling,
            "uid": f"{self.provider_id}:{self.model.id}",
        }


def _has_cap(model: ModelInfo, cap: str) -> bool:
    if cap == Capability.CHAT.value:
        return True
    return cap in model.capabilities


async def rank_candidates(
    adapters: list[ProviderAdapter],
    profile: TaskProfile,
    *,
    prefer_free: bool = True,
    n: int | None = None,
    diverse: bool = False,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    for adapter in adapters:
        if not adapter.enabled:
            continue
        try:
            models = await adapter.get_models()  # type: ignore[attr-defined]
        except Exception:
            models = adapter.sync_catalog()
        for m in models:
            missing = [c for c in profile.required if not _has_cap(m, c)]
            reasons: list[str] = []
            score = 1.0
            if missing:
                # hard requirement miss → only as last-resort with heavy penalty
                score -= 5 + len(missing)
                reasons.append(f"missing capability: {', '.join(missing)}")
            if m.context_window < profile.estimated_tokens:
                score -= 10
                reasons.append("context window smaller than prompt")
            elif m.context_window >= profile.estimated_tokens * 2:
                score += 0.5
                reasons.append("comfortable context headroom")
            if prefer_free and (m.free_tier or m.tier == "local"):
                score += 3
                reasons.append("free/local")
            score += PROVIDER_SPEED.get(adapter.type_key, 1) * 0.6
            if PROVIDER_SPEED.get(adapter.type_key, 1) >= 3:
                reasons.append("fast provider")
            if m.input_price_per_1m is not None:
                # cheaper is better; log-scaled penalty
                import math

                score -= min(2.0, math.log10(1 + m.input_price_per_1m) * 0.5)
            if Capability.REASONING.value in m.capabilities and profile.needs_reasoning:
                score += 2
                reasons.append("strong reasoning fit")
            if Capability.CODE.value in m.capabilities and profile.needs_code:
                score += 2
                reasons.append("coding-specialized")
            if Capability.TOOLS.value in m.capabilities and profile.needs_research:
                score += 1.5
                reasons.append("tool-calling for research")

            # Role-aware specialization.
            # These are soft preferences layered on top of hard capabilities.
            role = profile.role.lower().strip()

            if role in {"mathematician", "math", "theorist"}:
                if Capability.REASONING.value in m.capabilities:
                    score += 2.5
                    reasons.append("math/theory role fit via reasoning")
                if m.family and any(
                    token in m.family.lower()
                    for token in ("reason", "math", "think")
                ):
                    score += 1.5
                    reasons.append("math/reasoning model family")

            elif role in {"programmer", "coder", "developer"}:
                if Capability.CODE.value in m.capabilities:
                    score += 2.5
                    reasons.append("programming role fit")
                if Capability.TOOLS.value in m.capabilities:
                    score += 0.5
                    reasons.append("tools useful for programming")

            elif role in {
                "researcher",
                "fact_checker",
                "fact-checker",
                "web_researcher",
            }:
                if Capability.TOOLS.value in m.capabilities:
                    score += 2.5
                    reasons.append("research role fit via tools")
                if m.context_window >= 16000:
                    score += 0.5
                    reasons.append("large context useful for research")

            elif role in {
                "critic",
                "counterexample_hunter",
                "counterexample-hunter",
                "verifier",
            }:
                if Capability.REASONING.value in m.capabilities:
                    score += 2.5
                    reasons.append("verification/critique role fit")
                if Capability.CODE.value in m.capabilities and profile.needs_code:
                    score += 1.0
                    reasons.append("code support useful for verification")

            elif role in {"synthesizer", "planner"}:
                if Capability.REASONING.value in m.capabilities:
                    score += 2.0
                    reasons.append("planning/synthesis reasoning fit")
                if m.context_window >= 16000:
                    score += 1.0
                    reasons.append("large context useful for synthesis")

            # live health
            st = quota_manager.state(quota_manager.key(adapter.type_key, m.id))
            import time

            cooling = st.cooldown_until > time.time()
            if cooling:
                score -= 8
                reasons.append("currently in cooldown")
            if st.consecutive_failures:
                score -= 0.5 * st.consecutive_failures
            if st.successes:
                score += min(1.0, st.successes * 0.05)
                reasons.append("previously healthy here")
            candidates.append(Candidate(
                provider_id=adapter.instance_id, provider_type=adapter.type_key,
                provider_label=adapter.label, model=m, score=score, reasons=reasons, cooling=cooling,
            ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    target = n or len(candidates)
    if diverse:
        # prefer provider diversity (cap 2/type), then backfill so that
        # single-provider setups (e.g. mock-only) still get enough models
        picked: list[Candidate] = []
        per_type: dict[str, int] = {}
        for c in candidates:
            if per_type.get(c.provider_type, 0) >= 2:
                continue
            per_type[c.provider_type] = per_type.get(c.provider_type, 0) + 1
            picked.append(c)
        if len(picked) < target:
            picked_ids = {id(c) for c in picked}
            picked.extend(c for c in candidates if id(c) not in picked_ids)
        candidates = picked
    return candidates[:target]


async def build_plan(text: str, adapters: list[ProviderAdapter], *, has_images: bool = False,
                     prefer_free: bool = True) -> dict[str, Any]:
    profile = analyze_task(text, has_images=has_images)
    ranked = await rank_candidates(adapters, profile, prefer_free=prefer_free, n=8)
    return {
        "profile": profile.to_dict(),
        "plan": [c.to_dict() for c in ranked],
        "selected": ranked[0].to_dict() if ranked else None,
    }
