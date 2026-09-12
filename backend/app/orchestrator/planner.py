from __future__ import annotations

from dataclasses import dataclass

from .state import ProblemState, Task


@dataclass
class TaskProposal:
    description: str
    role: str
    depends_on: list[str]
    reason: str = ""
    context: str = ""


class Planner:
    """Deterministic task planner.

    This is intentionally simple for now.
    Later, an LLM can produce TaskProposal objects,
    while the state layer remains responsible for
    validating and applying them.
    """

    def propose_verification(
        self,
        state: ProblemState,
        task_id: str,
    ) -> TaskProposal:
        if task_id not in state.tasks:
            raise ValueError(f"Unknown task: {task_id}")

        task = state.tasks[task_id]

        return TaskProposal(
            description=f"Verify the result of: {task.description}",
            role="verifier",
            depends_on=[task_id],
        )

    def apply(
        self,
        state: ProblemState,
        proposal: TaskProposal,
        prefix: str = "task",
    ) -> Task:
        task_id = state.next_task_id(prefix)

        task = Task(
            id=task_id,
            description=proposal.description,
            role=proposal.role,
            depends_on=list(proposal.depends_on),
            reason=proposal.reason,
            context=proposal.context,
        )

        state.add_task(task)
        return task


import json
from typing import Any

from ..providers.base import ChatMessage
from .router import analyze_task, rank_candidates
from .runner import AgentSpec, ModelRef, run_tool_agent


class LLMPlanner:
    """Use an LLM to propose the next pieces of work.

    The LLM proposes; ProblemState remains the authority that
    validates and applies changes.
    """

    def _state_prompt(self, state: ProblemState) -> str:
        tasks = [
            {
                "id": task.id,
                "description": task.description,
                "role": task.role,
                "depends_on": task.depends_on,
                "reason": task.reason,
                "context": task.context,
                "status": task.status,
            }
            for task in state.tasks.values()
        ]

        contributions = [
            {
                "task_id": c.task_id,
                "role": c.role,
                "content": c.content,
            }
            for c in state.contributions
        ]

        return f"""You are the planning component of a multi-agent problem-solving system.

Your job is NOT to solve the problem directly.
Your job is to decide what work should happen next.

PROBLEM:
{state.problem}

CURRENT TASKS:
{json.dumps(tasks, indent=2)}

CONTRIBUTIONS:
{json.dumps(contributions, indent=2)}

KNOWN FACTS:
{json.dumps(state.facts, indent=2)}

HYPOTHESES:
{json.dumps(state.hypotheses, indent=2)}

CONTRADICTIONS:
{json.dumps(state.contradictions, indent=2)}

VERIFIED CLAIMS:
{json.dumps(state.verified_claims, indent=2)}

FAILED ATTEMPTS:
{json.dumps(state.failed_attempts, indent=2)}

UNRESOLVED QUESTIONS:
{json.dumps(state.unresolved_questions, indent=2)}

Return ONLY valid JSON in this exact shape:

{{
  "done": false,
  "tasks": [
    {{
      "description": "specific piece of work",
      "role": "researcher|mathematician|programmer|verifier|critic|counterexample_hunter|fact_checker|synthesizer",
      "depends_on": ["existing_task_id"],
      "reason": "why this task is the most useful next step",
      "context": "relevant discoveries or evidence the worker needs"
    }}
  ]
}}

Rules:
- Propose only work that is genuinely useful.
- Prefer verification, falsification, testing, or resolving contradictions when appropriate.
- Do not duplicate completed work.
- Prefer complementary specialists over several agents performing the same kind of investigation.
- When the problem benefits from independent perspectives, propose multiple tasks with distinct roles.
- Useful role combinations can include mathematician + programmer, researcher + fact_checker, or researcher + critic.
- Independent tasks should have no dependency on each other so they can run concurrently.
- Give each task a distinct investigative objective.
- Use dependencies when a later specialist genuinely needs an earlier result.
- Every dependency must reference an existing task.
- If enough evidence exists to finish, return "done": true and an empty task list.
- Do not invent task IDs.
"""

    @staticmethod
    def _parse(text: str) -> dict[str, Any]:
        text = text.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        data = json.loads(text)

        if not isinstance(data, dict):
            raise ValueError("Planner output must be a JSON object")

        tasks = data.get("tasks", [])
        if not isinstance(tasks, list):
            raise ValueError("Planner 'tasks' must be a list")

        return data

    async def propose(
        self,
        state: ProblemState,
        *,
        adapters,
        run_id: str = "",
        project_id: str | None = None,
    ) -> tuple[bool, list[TaskProposal]]:
        ranked = await rank_candidates(
            adapters,
            analyze_task(
                "plan and decompose a complex problem; "
                "identify verification and falsification work"
            ),
            n=1,
            diverse=True,
        )

        if not ranked:
            raise RuntimeError("No enabled models available for planner")

        candidate = ranked[0]

        agent = AgentSpec(
            label="planner",
            role="planner",
            ref=ModelRef(
                model=candidate.model.id,
                provider_id=candidate.provider_id,
            ),
            temperature=0.2,
            max_tokens=2000,
            allow_tools=False,
        )

        result = await run_tool_agent(
            agent,
            [ChatMessage(role="user", content=self._state_prompt(state))],
            run_id=run_id,
            project_id=project_id,
        )

        data = self._parse(result.text)

        proposals: list[TaskProposal] = []

        for item in data.get("tasks", []):
            if not isinstance(item, dict):
                continue

            description = item.get("description")
            role = item.get("role")
            depends_on = item.get("depends_on", [])

            if (
                isinstance(description, str)
                and description.strip()
                and isinstance(role, str)
                and role.strip()
                and isinstance(depends_on, list)
                and all(isinstance(dep, str) for dep in depends_on)
            ):
                proposals.append(
                    TaskProposal(
                        description=description.strip(),
                        role=role.strip(),
                        depends_on=depends_on,
                    )
                )

        return bool(data.get("done", False)), proposals
