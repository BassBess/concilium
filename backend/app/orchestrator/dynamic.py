from __future__ import annotations

import json

from typing import Any

from ..providers.base import ChatMessage, GenerationResult
from .planner import LLMPlanner, Planner
from .router import analyze_task, rank_candidates
from .runner import AgentSpec, ModelRef, run_tool_agent
from .state import Contribution, ProblemState, Task


async def run_task(
    state: ProblemState,
    task_id: str,
    *,
    run_id: str = "",
    adapters,
    project_id: str | None = None,
    allow_tools: bool = True,
) -> Contribution:
    if task_id not in state.tasks:
        raise ValueError(f"Unknown task: {task_id}")

    task = state.tasks[task_id]

    if task.status != "pending":
        raise ValueError(
            f"Task {task_id!r} is not pending (status={task.status!r})"
        )

    if not all(
        state.tasks[dep].status == "completed"
        for dep in task.depends_on
    ):
        raise ValueError(f"Task {task_id!r} has incomplete dependencies")

    state.set_task_status(task_id, "running")

    upstream = []
    for dep in task.depends_on:
        upstream.extend(state.task_contributions(dep))

    prompt_parts = [
        f"PROBLEM:\n{state.problem}",
        f"TASK:\n{task.description}",
    ]

    if upstream:
        prompt_parts.append(
            "UPSTREAM CONTRIBUTIONS:\n"
            + "\n\n".join(
                f"[{c.role}] {c.content}" for c in upstream
            )
        )

    messages = [
        ChatMessage(
            role="user",
            content="\n\n".join(prompt_parts),
        )
    ]

    ranked = await rank_candidates(
        adapters,
        analyze_task(task.description),
        n=1,
        diverse=True,
    )

    if not ranked:
        state.set_task_status(task_id, "failed")
        raise RuntimeError("No enabled models available for dynamic task")

    candidate = ranked[0]

    agent = AgentSpec(
        label=f"dynamic:{task.role or 'agent'}",
        role=task.role,
        ref=ModelRef(
            model=candidate.model.id,
            provider_id=candidate.provider_id,
        ),
        allow_tools=allow_tools,
        metadata={"task_id": task_id},
    )

    try:
        result = await run_tool_agent(
            agent,
            messages,
            run_id=run_id,
            node_id=task_id,
            project_id=project_id,
        )
    except Exception:
        state.set_task_status(task_id, "failed")
        raise

    contribution = Contribution(
        id=f"{task_id}:result",
        task_id=task_id,
        agent=agent.label,
        role=task.role,
        content=result.text,
        metadata={
            "provider": result.provider,
            "model": result.model,
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "cost": result.usage.cost,
            "latency_ms": result.latency_ms,
        },
    )

    state.add_contribution(contribution)
    state.set_task_status(task_id, "completed")

    return contribution


async def run_until_complete(
    state: ProblemState,
    *,
    run_id: str = "",
    adapters,
    project_id: str | None = None,
    max_steps: int = 20,
) -> ProblemState:
    """Execute currently runnable tasks until the state has no more work."""

    for _ in range(max_steps):
        runnable = state.runnable_tasks()

        if not runnable:
            break

        for task in runnable:
            await run_task(
                state,
                task.id,
                run_id=run_id,
                adapters=adapters,
                project_id=project_id,
            )

    return state


async def run_dynamic(
    problem: str,
    *,
    run_id: str = "",
    adapters,
    project_id: str | None = None,
    max_steps: int = 10,
) -> ProblemState:
    """Run an adaptive planning loop over ProblemState."""

    state = ProblemState(problem=problem)

    initial = Task(
        id="task_1",
        description=problem,
        role="researcher",
    )
    state.add_task(initial)

    planner = LLMPlanner()

    for _ in range(max_steps):
        runnable = state.runnable_tasks()

        if not runnable:
            break

        for task in runnable:
            await run_task(
                state,
                task.id,
                run_id=run_id,
                adapters=adapters,
                project_id=project_id,
            )

            done, proposals = await planner.propose(
                state,
                adapters=adapters,
                run_id=run_id,
                project_id=project_id,
            )

            if done:
                return state

            for proposal in proposals:
                task_id = state.next_task_id("task")

                new_task = Task(
                    id=task_id,
                    description=proposal.description,
                    role=proposal.role,
                    depends_on=list(proposal.depends_on),
                )

                state.add_task(new_task)

    return state


class DynamicSynthesizer:
    """Turn the accumulated adaptive investigation into one final answer.

    This is deliberately different from council synthesis:
    the inputs are not independent candidate answers. They are the
    evidence and work products accumulated by a dependency-driven
    investigation.
    """

    @staticmethod
    def _state_prompt(state: ProblemState) -> str:
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
                "agent": c.agent,
                "role": c.role,
                "content": c.content,
                "confidence": c.confidence,
            }
            for c in state.contributions
        ]

        return f"""You are the final synthesis component of an adaptive
multi-agent investigation.

You are NOT choosing the best answer from competing independent answers.

The agents below performed dependent investigative tasks. Later tasks
were created because of discoveries made by earlier tasks.

Your job is to reconstruct what the investigation actually established
and produce one rigorous answer to the original problem.

================ ORIGINAL PROBLEM ================
{state.problem}

================ TASK GRAPH ================
{json.dumps(tasks, indent=2)}

================ INVESTIGATION CONTRIBUTIONS ================
{json.dumps(contributions, indent=2)}

================ VERIFIED CLAIMS ================
{json.dumps(state.verified_claims, indent=2)}

================ FACTS ================
{json.dumps(state.facts, indent=2)}

================ HYPOTHESES ================
{json.dumps(state.hypotheses, indent=2)}

================ CONTRADICTIONS ================
{json.dumps(state.contradictions, indent=2)}

================ FAILED ATTEMPTS ================
{json.dumps(state.failed_attempts, indent=2)}

================ SYNTHESIS RULES ================
1. Do not assume a claim is true merely because an agent stated it.
2. Give priority to claims supported by verification or multiple
   independent investigative steps.
3. Explicitly resolve important contradictions when the evidence
   permits resolution.
4. Preserve uncertainty where the investigation did not establish
   something.
5. Do not resurrect failed approaches without explaining why.
6. Do not describe this as a council vote or majority decision.
7. Do not merely concatenate the contributions.
8. Answer the ORIGINAL PROBLEM directly.
9. If the investigation is incomplete, say exactly what remains
   unresolved.

Produce a coherent final answer with reasoning and an appropriate
level of detail.
"""


async def synthesize_dynamic(
    state: ProblemState,
    *,
    adapters,
    run_id: str = "",
    project_id: str | None = None,
    temperature: float = 0.3,
) -> GenerationResult:
    """Synthesize the completed adaptive investigation."""

    ranked = await rank_candidates(
        adapters,
        analyze_task(
            "synthesize a rigorous investigation from verified evidence, "
            "contradictions, hypotheses, and failed attempts"
        ),
        n=1,
        diverse=True,
    )

    if not ranked:
        raise RuntimeError("No enabled models available for dynamic synthesis")

    candidate = ranked[0]

    agent = AgentSpec(
        label="dynamic:synthesizer",
        role="synthesizer",
        ref=ModelRef(
            model=candidate.model.id,
            provider_id=candidate.provider_id,
        ),
        temperature=temperature,
        max_tokens=4000,
        allow_tools=False,
    )

    return await run_tool_agent(
        agent,
        [ChatMessage(
            role="user",
            content=DynamicSynthesizer._state_prompt(state),
        )],
        run_id=run_id,
        node_id="dynamic:synthesis",
        project_id=project_id,
    )
