"""Resource scheduling for heterogeneous Concilium computation."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

from .resources import ResourceAssignment, ResourceRegistry, ResourceSpec
from .router import TaskProfile


class Scheduler:
    """Choose the computational resource for a task.

    The scheduler decides WHICH RESOURCE CLASS / PROVIDER should perform
    the task.

    The existing router remains responsible for deciding WHICH MODEL
    inside an LLM provider should perform it.

    This separation is important for Concilium's eventual large provider
    ecosystem.
    """

    def __init__(
        self,
        registry: ResourceRegistry | None = None,
        health_provider: Callable[[ResourceSpec], dict[str, Any]] | None = None,
    ) -> None:
        self.registry = registry or ResourceRegistry()
        self.health_provider = health_provider

    def _score(
        self,
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

        if resource.free:
            score += 1.5
            reasons.append("free")

        if resource.local:
            score += 1.5
            reasons.append("local")

        if resource.estimated_latency_ms is not None:
            latency = max(1, resource.estimated_latency_ms)
            speed_score = max(0.0, 1.0 - math.log10(latency) / 6.0)
            score += speed_score

            if latency < 5000:
                reasons.append("low estimated latency")

        if resource.estimated_cost is not None:
            cost = max(0.0, resource.estimated_cost)
            score -= min(2.0, math.log10(1.0 + cost))

            if cost == 0:
                reasons.append("zero estimated cost")

        if self.health_provider is not None:
            health = self.health_provider(resource)

            if health.get("in_cooldown"):
                return None

            failures = int(health.get("consecutive_failures", 0) or 0)
            rate_limits = int(health.get("rate_limits", 0) or 0)
            retries = int(health.get("retries", 0) or 0)

            if failures:
                penalty = min(3.0, failures * 0.75)
                score -= penalty
                reasons.append(f"{failures} consecutive failure(s)")

            if rate_limits:
                penalty = min(2.0, rate_limits * 0.25)
                score -= penalty
                reasons.append(f"{rate_limits} rate limit(s)")

            if retries:
                penalty = min(1.0, retries * 0.1)
                score -= penalty

            avg_latency = health.get("avg_latency_ms")
            if avg_latency is not None:
                latency = max(1.0, float(avg_latency))
                if latency < 1000:
                    score += 0.75
                    reasons.append("healthy recent latency")
                elif latency > 10000:
                    score -= 0.75
                    reasons.append("high recent latency")

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

        assignments.sort(
            key=lambda assignment: assignment.score,
            reverse=True,
        )

        if n is None:
            return assignments

        return assignments[:n]

    def select(
        self,
        profile: TaskProfile,
    ) -> ResourceAssignment | None:
        ranked = self.rank(profile, n=1)
        return ranked[0] if ranked else None
