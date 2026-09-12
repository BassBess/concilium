from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Task:
    id: str
    description: str
    role: str = ""
    depends_on: list[str] = field(default_factory=list)
    reason: str = ""
    context: str = ""
    status: str = "pending"
    assigned_to: str | None = None


@dataclass
class Contribution:
    id: str
    task_id: str
    agent: str
    role: str
    content: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProblemState:
    problem: str

    tasks: dict[str, Task] = field(default_factory=dict)
    contributions: list[Contribution] = field(default_factory=list)

    facts: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)
    verified_claims: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    unresolved_questions: list[str] = field(default_factory=list)

    def add_task(self, task: Task) -> None:
        if task.id in self.tasks:
            raise ValueError(f"Task already exists: {task.id}")

        for dependency in task.depends_on:
            if dependency not in self.tasks:
                raise ValueError(
                    f"Task {task.id!r} depends on unknown task {dependency!r}"
                )

        self.tasks[task.id] = task

    def next_task_id(self, prefix: str = "task") -> str:
        index = 1
        while f"{prefix}_{index}" in self.tasks:
            index += 1
        return f"{prefix}_{index}"

    def add_contribution(self, contribution: Contribution) -> None:
        if contribution.task_id not in self.tasks:
            raise KeyError(
                f"Contribution references unknown task: {contribution.task_id}"
            )

        self.contributions.append(contribution)

    def task_contributions(self, task_id: str) -> list[Contribution]:
        return [
            contribution
            for contribution in self.contributions
            if contribution.task_id == task_id
        ]

    def set_task_status(self, task_id: str, status: str) -> None:
        if task_id not in self.tasks:
            raise KeyError(f"Unknown task: {task_id}")

        allowed = {"pending", "running", "completed", "failed"}

        if status not in allowed:
            raise ValueError(
                f"Invalid task status: {status}. "
                f"Expected one of: {sorted(allowed)}"
            )

        self.tasks[task_id].status = status

    def pending_tasks(self) -> list[Task]:
        return [task for task in self.tasks.values() if task.status == "pending"]

    def runnable_tasks(self) -> list[Task]:
        completed = {
            task.id
            for task in self.tasks.values()
            if task.status == "completed"
        }

        return [
            task
            for task in self.tasks.values()
            if task.status == "pending"
            and all(dep in completed for dep in task.depends_on)
        ]

    def is_complete(self) -> bool:
        return bool(self.tasks) and all(
            task.status == "completed"
            for task in self.tasks.values()
        )
