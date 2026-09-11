"""The multi-model council: independent answers → critique rounds → revisions
→ synthesis. Every stage is parallel where possible and emits trace events so
the UI can show the full deliberation live.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from ..providers.base import ChatMessage, GenerationResult
from .prompts import CRITIQUE_INSTRUCTION, REVISION_INSTRUCTION, SYNTHESIS_INSTRUCTION, render
from .roles import role_prompt
from .runner import AgentSpec, AllCandidatesFailed, ModelRef, run_tool_agent
from .events import bus


@dataclass
class Participant:
    label: str
    ref: dict[str, Any]
    role: str = "researcher"
    system: str | None = None
    allow_tools: bool = False

    @staticmethod
    def from_dict(d: dict[str, Any], idx: int) -> "Participant":
        role = d.get("role") or "researcher"
        return Participant(
            label=d.get("label") or f"Agent {idx + 1} ({role})",
            ref=d.get("ref") or d,
            role=role,
            system=d.get("system"),
            allow_tools=bool(d.get("allow_tools", d.get("tools"))),
        )


@dataclass
class AgentOutput:
    label: str
    role: str
    text: str
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0
    ok: bool = True
    error: str = ""
    stage: str = "independent"


def _format_solutions(outputs: list[AgentOutput], own_label: str | None = None) -> str:
    blocks = []
    for o in outputs:
        if not o.ok or not o.text:
            continue
        tag = " (YOURS)" if own_label and o.label == own_label else ""
        header = f"--- Contributor: {o.label}{tag} | role: {o.role} | {o.provider}/{o.model} ---"
        blocks.append(f"{header}\n{o.text}")
    return "\n\n".join(blocks)


async def _gather_parallel(coros, parallel: bool):
    if parallel:
        return await asyncio.gather(*coros, return_exceptions=False)
    out = []
    for c in coros:
        try:
            out.append(await c)
        except AllCandidatesFailed as e:
            out.append(e)
    return out


def _agent_from_result(label: str, role: str, r: GenerationResult, stage: str) -> AgentOutput:
    return AgentOutput(
        label=label, role=role, text=r.text, provider=r.provider, model=r.model,
        input_tokens=r.usage.input_tokens, output_tokens=r.usage.output_tokens,
        cost=r.usage.cost, latency_ms=r.latency_ms, ok=True, stage=stage,
    )


async def run_council(
    *,
    run_id: str,
    question: str,
    history: list[ChatMessage],
    config: dict[str, Any],
    project_id: str | None = None,
    context_block: str = "",
) -> dict[str, Any]:
    participants = [Participant.from_dict(p, i) for i, p in enumerate(config.get("participants", []))]
    if not participants:
        raise ValueError("Council has no participants")
    parallel = bool(config.get("parallel", True))
    critique_rounds = int(config.get("critique_rounds", 1))
    do_revision = bool(config.get("revision", True))
    temperature = float(config.get("temperature", 0.7))

    ctx = f"\n\n{context_block}" if context_block else ""

    async def independent(p: Participant) -> AgentOutput:
        node = f"independent:{p.label}"
        system = p.system or role_prompt(p.role)
        messages = [ChatMessage(role="system", content=system), *history,
                    ChatMessage(role="user", content=f"{question}{ctx}")]
        spec = AgentSpec(label=p.label, ref=ModelRef.parse(p.ref), role=p.role,
                         temperature=temperature, allow_tools=p.allow_tools,
                         failover=bool(config.get("failover", True)))
        try:
            result = await run_tool_agent(spec, messages, run_id=run_id, node_id=node,
                                          project_id=project_id)
            return _agent_from_result(p.label, p.role, result, "independent")
        except AllCandidatesFailed as exc:
            await bus.publish(run_id, "agent_failed", node_id=node, label=p.label,
                              errors=exc.errors)
            return AgentOutput(p.label, p.role, "", ok=False,
                               error="; ".join(exc.errors)[:500], stage="independent")

    # ------------------------------------------------ ROUND 1: independent
    await bus.publish(run_id, "round_started", round=0, title="Independent answers",
                      agents=[p.label for p in participants])
    coros = [independent(p) for p in participants]
    if parallel:
        answers = await asyncio.gather(*coros)
    else:
        answers = []
        for c in coros:
            answers.append(await c)
    await bus.publish(run_id, "round_finished", round=0,
                      ok_count=sum(1 for a in answers if a.ok))

    healthy = [a for a in answers if a.ok and a.text]
    if not healthy:
        raise AllCandidatesFailed(["Every council participant failed; see trace."])

    # ------------------------------------------------ ROUND 2: critique (N rounds)
    all_critiques: list[list[AgentOutput]] = []
    for cr in range(critique_rounds):
        solutions_block = _format_solutions(healthy)

        async def critique(p: Participant, idx: int) -> AgentOutput:
            node = f"critique-r{cr}:{p.label}"
            own = next((a for a in answers if a.label == p.label), None)
            if own is None or not own.ok:
                return AgentOutput(p.label, p.role, "", ok=False, stage=f"critique-{cr}")
            prompt = render(CRITIQUE_INSTRUCTION, {
                "question": question, "context_block": ctx, "solutions": solutions_block})
            messages = [
                ChatMessage(role="system",
                           content=role_prompt("critic") + f"\nYou are {p.label}, reviewing the panel."),
                *history,
                ChatMessage(role="user", content=prompt),
            ]
            spec = AgentSpec(label=f"{p.label} (critique)", ref=ModelRef.parse(p.ref),
                             role="critic", temperature=max(0.2, temperature - 0.2),
                             failover=bool(config.get("failover", True)))
            try:
                result = await run_tool_agent(spec, messages, run_id=run_id, node_id=node,
                                              project_id=project_id)
                out = _agent_from_result(p.label, "critic", result, f"critique-{cr}")
                return out
            except AllCandidatesFailed as exc:
                await bus.publish(run_id, "agent_failed", node_id=node, label=p.label, errors=exc.errors)
                return AgentOutput(p.label, "critic", "", ok=False,
                                   error="; ".join(exc.errors)[:300], stage=f"critique-{cr}")

        await bus.publish(run_id, "round_started", round=cr + 1,
                          title=f"Critique round {cr + 1}", agents=[p.label for p in participants])
        coros = [critique(p, i) for i, p in enumerate(participants)]
        if parallel:
            critiques = await asyncio.gather(*coros)
        else:
            critiques = [await c for c in coros]
        all_critiques.append(critiques)
        await bus.publish(run_id, "round_finished", round=cr + 1,
                          ok_count=sum(1 for a in critiques if a.ok))

    # ------------------------------------------------ ROUND 3: revisions
    finals = healthy
    if do_revision and all_critiques:
        latest_critiques = [c for c in all_critiques[-1] if c.ok and c.text]
        if latest_critiques:
            critiques_block = "\n\n".join(
                f"--- Critique by {c.label} ---\n{c.text}" for c in latest_critiques)

            async def revise(p: Participant) -> AgentOutput:
                node = f"revision:{p.label}"
                own = next((a for a in answers if a.label == p.label), None)
                if own is None or not own.ok:
                    return own or AgentOutput(p.label, p.role, "", ok=False, stage="revision")
                prompt = render(REVISION_INSTRUCTION, {
                    "question": question, "context_block": ctx,
                    "own_solution": own.text, "critiques": critiques_block})
                messages = [
                    ChatMessage(role="system", content=role_prompt(p.role)),
                    *history,
                    ChatMessage(role="user", content=prompt),
                ]
                spec = AgentSpec(label=f"{p.label} (revised)", ref=ModelRef.parse(p.ref),
                                 role=p.role, temperature=temperature,
                                 failover=bool(config.get("failover", True)))
                try:
                    result = await run_tool_agent(spec, messages, run_id=run_id, node_id=node,
                                                  project_id=project_id)
                    out = _agent_from_result(p.label, p.role, result, "revision")
                    return out
                except AllCandidatesFailed as exc:
                    await bus.publish(run_id, "agent_failed", node_id=node, label=p.label,
                                      errors=exc.errors)
                    return own  # fall back to original answer

            await bus.publish(run_id, "round_started", round=99, title="Revisions",
                              agents=[p.label for p in participants])
            coros = [revise(p) for p in participants]
            if parallel:
                revised = await asyncio.gather(*coros)
            else:
                revised = [await c for c in coros]
            finals = [a for a in revised if a.ok and a.text] or healthy
            await bus.publish(run_id, "round_finished", round=99,
                              ok_count=len(finals))

    # ------------------------------------------------ ROUND 4: synthesis
    solutions_block = _format_solutions(finals)
    critiques_block = ""
    if all_critiques:
        flat = [c for group in all_critiques for c in group if c.ok and c.text]
        if flat:
            critiques_block = "\n\n================ CRITIQUES ================\n" + "\n\n".join(
                f"[{c.label}] {c.text}" for c in flat)

    synth_cfg = config.get("synthesizer")
    final_text = ""
    synth_output: AgentOutput | None = None
    if synth_cfg:
        await bus.publish(run_id, "synthesis_started",
                          provider=(synth_cfg.get("ref") or synth_cfg).get("provider_type", "?"))
        prompt = render(SYNTHESIS_INSTRUCTION, {
            "question": question, "context_block": ctx,
            "solutions": solutions_block, "critiques_block": critiques_block})
        messages = [
            ChatMessage(role="system", content=synth_cfg.get("system") or role_prompt("synthesizer")),
            *history,
            ChatMessage(role="user", content=prompt),
        ]
        spec = AgentSpec(label="Synthesizer",
                         ref=ModelRef.parse(synth_cfg.get("ref") or synth_cfg),
                         role="synthesizer", temperature=0.4,
                         max_tokens=synth_cfg.get("max_tokens"),
                         failover=True)
        try:
            result = await run_tool_agent(spec, messages, run_id=run_id, node_id="synthesis",
                                          project_id=project_id)
            synth_output = _agent_from_result("Synthesizer", "synthesizer", result, "synthesis")
            final_text = result.text
        except AllCandidatesFailed as exc:
            await bus.publish(run_id, "agent_log", node_id="synthesis", level="warn",
                              message=f"Synthesizer and all fallbacks failed: {exc.errors}; "
                                      "using deterministic aggregation instead.")

    if not final_text:
        # Deterministic fallback so the council never collapses.
        final_text = _aggregate(question, finals, all_critiques)
        await bus.publish(run_id, "synthesis_fallback",
                          message="No synthesis model succeeded; answers aggregated verbatim.")

    totals = _totals(answers, all_critiques, finals, synth_output)
    contributors = [
        {"label": a.label, "provider": a.provider, "model": a.model, "role": a.role,
         "output_tokens": a.output_tokens, "cost": a.cost, "stage": a.stage}
        for a in finals
    ]
    return {
        "final": final_text,
        "contributors": contributors,
        "totals": totals,
        "answers": [a.__dict__ for a in answers],
        "critiques": [[a.__dict__ for a in group] for group in all_critiques],
        "finals": [a.__dict__ for a in finals],
        "synthesis": synth_output.__dict__ if synth_output else None,
    }


def _aggregate(question: str, finals: list[AgentOutput], critiques: list) -> str:
    parts = [
        "# Council report (auto-aggregated — no synthesis model available)",
        f"**Question:** {question}\n",
        "The synthesizer and all configured fallback models were unavailable, so the "
        "individual contributors' final answers are presented verbatim.\n",
    ]
    for a in finals:
        parts.append(f"## {a.label} — {a.role} ({a.provider}/{a.model})\n{a.text}\n")
    return "\n".join(parts)


def _totals(answers, critique_groups, finals, synth) -> dict[str, Any]:
    tin = sum(a.input_tokens for a in answers if a.ok)
    tout = sum(a.output_tokens for a in answers if a.ok)
    cost = sum(a.cost for a in answers if a.ok)
    calls = sum(1 for a in answers if a.ok)
    for group in critique_groups:
        tin += sum(a.input_tokens for a in group if a.ok)
        tout += sum(a.output_tokens for a in group if a.ok)
        cost += sum(a.cost for a in group if a.ok)
        calls += sum(1 for a in group if a.ok)
    # revisions are distinct model calls
    revision_outputs = finals if any(a.stage == "revision" for a in finals) else []
    tin += sum(a.input_tokens for a in revision_outputs)
    tout += sum(a.output_tokens for a in revision_outputs)
    cost += sum(a.cost for a in revision_outputs)
    calls += len(revision_outputs)
    if synth:
        tin += synth.input_tokens
        tout += synth.output_tokens
        cost += synth.cost
        calls += 1
    return {"input_tokens": tin, "output_tokens": tout, "cost": round(cost, 8), "calls": calls}
