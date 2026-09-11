"""Custom workflow engine.

A workflow is a small DAG of nodes::

    {
      "nodes": [
        {"id": "research", "role": "researcher", "count": 3, "tools": true},
        {"id": "critique", "role": "critic", "count": 2, "depends_on": ["research"]},
        {"id": "judge", "role": "judge", "count": 1, "depends_on": ["critique"]},
        {"id": "write", "role": "synthesizer", "count": 1,
         "depends_on": ["judge", "research"], "terminal": true}
      ]
    }

* Nodes without ``ref`` auto-pick models via the router (diverse providers).
* ``count`` fans out N parallel agents with the same role.
* Independent nodes run concurrently; each node receives the outputs of its
  dependencies; terminal/synthesizer nodes receive everything upstream.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from ..providers.base import ChatMessage
from .council import AgentOutput, _agent_from_result, _format_solutions
from .events import bus
from .roles import role_prompt
from .router import analyze_task, rank_candidates
from .runner import AgentSpec, AllCandidatesFailed, ModelRef, run_tool_agent
from .state import ProblemState, Task, Contribution


@dataclass
class NodeResult:
    node_id: str
    outputs: list[AgentOutput]


def _topo_levels(nodes: list[dict]) -> list[list[dict]]:
    by_id = {n["id"]: n for n in nodes}
    done: set[str] = set()
    levels: list[list[dict]] = []
    remaining = list(nodes)
    while remaining:
        level = [n for n in remaining if not (set(n.get("depends_on", [])) - done)]
        if not level:
            raise ValueError("workflow contains a cycle or unknown dependency")
        for n in level:
            done.add(n["id"])
        remaining = [n for n in remaining if n["id"] not in done]
        levels.append(level)
    return levels


async def _auto_refs(count: int, question: str, adapters) -> list[dict[str, str]]:
    ranked = await rank_candidates(adapters, analyze_task(question), n=max(count, 1), diverse=True)
    refs = [{"provider_id": c.provider_id, "model": c.model.id} for c in ranked[:count]]
    if not refs:
        raise AllCandidatesFailed(["No enabled models available for workflow node"])
    while len(refs) < count:  # repeat best models if fewer than count
        refs.append(refs[len(refs) % len(refs)])
    return refs


def _inputs_for(node: dict, results: dict[str, NodeResult], question: str,
                all_outputs: list[AgentOutput], context_block: str) -> str:
    deps = node.get("depends_on", [])
    blocks = [f"QUESTION:\n{question}{context_block}"]
    role = node.get("role", "")
    gather_all = node.get("terminal") or role in ("synthesizer", "judge", "summarizer")
    pool = all_outputs if gather_all else []
    if not gather_all:
        for dep in deps:
            nr = results.get(dep)
            if nr:
                pool.extend(nr.outputs)
    if pool:
        blocks.append("UPSTREAM CONTRIBUTIONS:\n" + _format_solutions(pool))
    instruction = node.get("instruction")
    if instruction:
        blocks.append(f"ADDITIONAL INSTRUCTION:\n{instruction}")
    return "\n\n".join(blocks)


async def run_workflow(
    *,
    run_id: str,
    question: str,
    history: list[ChatMessage],
    definition: dict[str, Any],
    adapters,
    project_id: str | None = None,
    context_block: str = "",
) -> dict[str, Any]:
    nodes = definition.get("nodes", [])
    if not nodes:
        raise ValueError("workflow has no nodes")
    levels = _topo_levels(nodes)
    results: dict[str, NodeResult] = {}
    all_outputs: list[AgentOutput] = []
    total_calls = 0

    # Shared evolving state. Execution remains DAG-based for now;
    # this state becomes the foundation for dynamic planning later.
    state = ProblemState(problem=question)

    for node in nodes:
        state.add_task(
            Task(
                id=node["id"],
                description=node.get("instruction") or node.get("role", "workflow task"),
                role=node.get("role", ""),
                depends_on=list(node.get("depends_on", [])),
            )
        )

    await bus.publish(run_id, "workflow_started", levels=[[n["id"] for n in lvl] for lvl in levels])

    for level_idx, level in enumerate(levels):
        await bus.publish(run_id, "round_started", round=level_idx,
                          title=f"Stage {level_idx + 1}: {', '.join(n['id'] for n in level)}")

        async def run_node(node: dict) -> NodeResult:
            nonlocal total_calls
            count = int(node.get("count", 1))  # noqa: F841 (redeclared for clarity)

            refs: list[dict[str, Any]]
            if node.get("ref"):
                refs = [node["ref"]] * count
            else:
                refs = await _auto_refs(count, question, adapters)
            role = node.get("role", "researcher")
            system = node.get("system") or role_prompt(role)
            user_block = _inputs_for(node, results, question, all_outputs, context_block)

            async def one(i: int) -> AgentOutput:
                nonlocal total_calls
                label = f"{node['id']}.{i + 1}" if count > 1 else node["id"]
                node_id = f"{node['id']}:{i}"
                messages = [ChatMessage(role="system", content=system), *history,
                            ChatMessage(role="user", content=user_block)]
                ref = refs[min(i, len(refs) - 1)]
                provider_type = ref.get("provider_type", "?")
                await bus.publish(run_id, "agent_started", node_id=node_id, label=label,
                                  role=role, provider=provider_type, model=ref.get("model"),
                                  attempt=0, workflow_node=node["id"])
                spec = AgentSpec(label=label, ref=ModelRef.parse(ref), role=role,
                                 temperature=float(node.get("temperature", 0.7)),
                                 allow_tools=bool(node.get("tools", False)), failover=True)
                try:
                    result = await run_tool_agent(spec, messages, run_id=run_id,
                                                  node_id=node_id, project_id=project_id)
                    total_calls += 1
                    return _agent_from_result(label, role, result, node["id"])
                except AllCandidatesFailed as exc:
                    await bus.publish(run_id, "agent_failed", node_id=node_id, label=label,
                                      errors=exc.errors)
                    return AgentOutput(label, role, "", ok=False,
                                       error="; ".join(exc.errors)[:400], stage=node["id"])

            outs = await asyncio.gather(*[one(i) for i in range(count)])
            outs = [o for o in outs]
            nr = NodeResult(node["id"], outs)
            results[node["id"]] = nr
            all_outputs.extend(o for o in outs if o.ok)

            state.set_task_status(node["id"], "completed")

            for o in outs:
                if o.ok and o.text:
                    state.add_contribution(
                        Contribution(
                            id=o.label,
                            task_id=node["id"],
                            agent=o.provider or o.model or o.label,
                            role=o.role,
                            content=o.text,
                            metadata={
                                "provider": o.provider,
                                "model": o.model,
                                "stage": o.stage,
                            },
                        )
                    )

            return nr

        level_results = await asyncio.gather(*[run_node(n) for n in level])
        ok = sum(1 for nr in level_results for o in nr.outputs if o.ok)
        await bus.publish(run_id, "round_finished", round=level_idx, ok_count=ok)

    # Determine final output
    terminal_nodes = [n for n in nodes if n.get("terminal")] or [
        n for n in nodes if not any(n["id"] in m.get("depends_on", []) for m in nodes)]
    terminal_outputs: list[AgentOutput] = []
    for n in terminal_nodes:
        terminal_outputs.extend(o for o in results[n["id"]].outputs if o.ok and o.text)
    if not terminal_outputs:
        terminal_outputs = [o for o in all_outputs if o.ok]
    if not terminal_outputs:
        raise AllCandidatesFailed(["Every workflow node failed"])

    if len(terminal_outputs) == 1:
        final = terminal_outputs[0].text
    else:
        # Deterministic join (a terminal synthesizer node normally collapses to 1)
        final = "# Workflow output\n\n" + "\n\n".join(
            f"## {o.label} ({o.role}, {o.provider}/{o.model})\n{o.text}" for o in terminal_outputs)

    totals = {
        "input_tokens": sum(o.input_tokens for o in all_outputs),
        "output_tokens": sum(o.output_tokens for o in all_outputs),
        "cost": round(sum(o.cost for o in all_outputs), 8),
        "calls": total_calls,
    }
    return {
        "final": final,
        "totals": totals,
        "nodes": {nid: [o.__dict__ for o in nr.outputs] for nid, nr in results.items()},
        "state": {
            "problem": state.problem,
            "tasks": {
                task_id: {
                    "description": task.description,
                    "role": task.role,
                    "depends_on": task.depends_on,
                    "status": task.status,
                }
                for task_id, task in state.tasks.items()
            },
            "contributions": [
                {
                    "id": c.id,
                    "task_id": c.task_id,
                    "agent": c.agent,
                    "role": c.role,
                    "content": c.content,
                    "confidence": c.confidence,
                    "metadata": c.metadata,
                }
                for c in state.contributions
            ],
        },
        "contributors": [
            {"label": o.label, "provider": o.provider, "model": o.model, "role": o.role,
             "output_tokens": o.output_tokens, "cost": o.cost, "stage": o.stage}
            for o in all_outputs],
    }
