"""Resource scheduling for heterogeneous Concilium computation."""

from __future__ import annotations

import math

from .resources import ResourceAssignment, ResourceRegistry, ResourceSpec
from .router import TaskProfile


class Scheduler:
    """Choose computational resources for a task.

    This is intentionally separate from the LLM router.

    The router answers:
        "Which model is best?"

    The scheduler answers:
        "Which computational resource should perform this task?"

    Today the registry can contain LLM resources. Later it can contain
    Python, C++, web, symbolic, database, vision, or external API resources.
    """

    def __init__(self, registry: ResourceRegistry | None = None) -> None:
        self.registry = registry or ResourceRegistry()

    @staticmethod
    def _score(
        resource: ResourceSpec,
        profile: TaskProfile,
    ) -> ResourceAssignment | None:
        reasons: list[str] = []
        score = 0.0

        missing = [
            capability
            for capability in profile.required
            if not resource.has_capability(capability)
        ]

        if missing:
            return None

        if profile.estimated_tokens and resource.metadata.get("context_window"):
            context = int(resource.metadata["context_window"])

            if context < profile.estimated_tokens:
                return None

            if context >= profile.estimated_tokens * 2:
                score += 1.0
                reasons.append("comfortable context headroom")

        if resource.free or resource.local:
            score += 2.0
            reasons.append("free/local")

        if resource.estimated_latency_ms is not None:
            # Reward low latency without letting speed dominate capability fit.
            speed_score = max(
                0.0,
                1.0 - math.log10(max(1, resource.estimated_latency_ms)) / 6.0,
            )
            score += speed_score
            if resource.estimated_latency_ms < 5000:
                reasons.append("low estimated latency")

        if resource.estimated_cost is not None:
            score -= min(2.0, math.log10(1 + max(0.0, resource.estimated_cost)))
            if resource.estimated_cost == 0:
                reasons.append("zero estimated cost")

        role = profile.role.lower().strip()

        if role in {"mathematician", "math", "theorist"}:
            if resource.has_capability("reasoning"):
                score += 2.0
                reasons.append("math role fit")

        elif role in {"programmer", "coder", "developer"}:
            if resource.has_capability("code"):
                score += 2.0
                reasons.append("programming role fit")

        elif role in {
            "researcher",
            "fact_checker",
            "fact-checker",
            "web_researcher",
        }:
            if resource.has_capability("tools"):
                score += 2.0
                reasons.append("research/tool fit")

        elif role in {
            "critic",
            "counterexample_hunter",
            "counterexample-hunter",
            "verifier",
        }:
            if resource.has_capability("reasoning"):
                score += 2.0
                reasons.append("verification role fit")

        elif role in {"planner", "synthesizer"}:
            if resource.has_capability("reasoning"):
                score += 1.5
                reasons.append("planning/synthesis fit")

        return ResourceAssignment(
            resource=resource,
            score=score,
            reasons=reasons,
        )

    def rank(
        self,
        profile: TaskProfile,
        *,
        n: int | None = None,
    ) -> list[ResourceAssignment]:
        assignments: list[ResourceAssignment] = []

        for resource in self.registry.all():
            assignment = self._score(resource, profile)
            if assignment is not None:
                assignments.append(assignment)

        assignments.sort(key=lambda item: item.score, reverse=True)

        return assignments[: n or len(assignments)]

    def select(
        self,
        profile: TaskProfile,
    ) -> ResourceAssignment | None:
        ranked = self.rank(profile, n=1)
        return ranked[0] if ranked else None
