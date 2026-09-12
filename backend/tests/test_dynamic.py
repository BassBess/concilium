import pytest
import asyncio

from app.orchestrator.dynamic import run_task
from app.orchestrator.state import ProblemState, Task


class FakeResult:
    text = "dynamic result"
    provider = "fake"
    model = "fake-model"
    latency_ms = 10

    class Usage:
        input_tokens = 5
        output_tokens = 7
        cost = 0.0

    usage = Usage()


@pytest.mark.asyncio
async def test_dynamic_task_updates_state(monkeypatch):
    state = ProblemState("Solve the problem")
    state.add_task(Task(
        id="task_1",
        description="Investigate the problem",
        role="researcher",
    ))

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    monkeypatch.setattr(
        "app.orchestrator.planner.rank_candidates",
        fake_rank,
    )

    contribution = await run_task(
        state,
        "task_1",
        adapters=[],
    )

    assert state.tasks["task_1"].status == "completed"
    assert contribution.content == "dynamic result"
    assert len(state.contributions) == 1
    assert state.contributions[0].metadata["model"] == "fake-model"


@pytest.mark.asyncio
async def test_dynamic_execution_can_add_followup_task(monkeypatch):
    from app.orchestrator.planner import Planner

    state = ProblemState("Test dynamic planning")
    state.add_task(Task(
        id="task_1",
        description="Investigate the problem",
        role="researcher",
    ))

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    await run_task(state, "task_1", adapters=[])

    planner = Planner()
    proposal = planner.propose_verification(state, "task_1")
    verification = planner.apply(state, proposal, prefix="verify")

    assert verification.depends_on == ["task_1"]
    assert verification.role == "verifier"
    assert verification.status == "pending"
    assert state.runnable_tasks()[0].id == "verify_1"


@pytest.mark.asyncio
async def test_run_until_complete_executes_all_runnable_tasks(monkeypatch):
    from app.orchestrator.dynamic import run_until_complete

    state = ProblemState("Complete all tasks")
    state.add_task(Task(
        id="task_1",
        description="First task",
        role="researcher",
    ))
    state.add_task(Task(
        id="task_2",
        description="Second task",
        role="verifier",
        depends_on=["task_1"],
    ))

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    await run_until_complete(state, adapters=[])

    assert state.tasks["task_1"].status == "completed"
    assert state.tasks["task_2"].status == "completed"
    assert len(state.contributions) == 2
    assert state.is_complete()


@pytest.mark.asyncio
async def test_run_dynamic_creates_and_executes_verification(monkeypatch):
    from app.orchestrator.dynamic import run_dynamic
    from app.orchestrator.planner import TaskProposal

    class FakePlanner:
        async def propose(self, state, **kwargs):
            if len(state.contributions) == 1:
                return False, [
                    TaskProposal(
                        description="Verify the result",
                        role="verifier",
                        depends_on=["task_1"],
                    )
                ]
            return True, []

    monkeypatch.setattr(
        "app.orchestrator.dynamic.LLMPlanner",
        FakePlanner,
    )

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    state = await run_dynamic(
        "Investigate whether the proposed approach works",
        adapters=[],
    )

    assert "task_1" in state.tasks
    assert "task_2" in state.tasks
    assert state.tasks["task_2"].role == "verifier"
    assert state.tasks["task_2"].depends_on == ["task_1"]
    assert state.tasks["task_1"].status == "completed"
    assert state.tasks["task_2"].status == "completed"
    assert state.is_complete()

async def test_dynamic_loop_uses_planner(monkeypatch):
    from app.orchestrator.dynamic import run_dynamic

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    calls = []

    class FakePlanner:
        async def propose(self, state, **kwargs):
            calls.append(len(state.contributions))

            if len(state.contributions) == 1:
                from app.orchestrator.planner import TaskProposal

                return False, [
                    TaskProposal(
                        description="Try to falsify the result",
                        role="counterexample_hunter",
                        depends_on=["task_1"],
                    )
                ]

            return True, []

    monkeypatch.setattr(
        "app.orchestrator.dynamic.LLMPlanner",
        FakePlanner,
    )

    state = await run_dynamic(
        "Investigate the proposed approach",
        adapters=[],
    )

    assert calls == [1, 2]
    assert "task_1" in state.tasks
    assert "task_2" in state.tasks
    assert state.tasks["task_2"].role == "counterexample_hunter"
    assert state.tasks["task_2"].depends_on == ["task_1"]
    assert state.is_complete()


@pytest.mark.asyncio
async def test_dynamic_worker_receives_task_reason_and_context(monkeypatch):
    from app.orchestrator.dynamic import run_task

    state = ProblemState("Determine whether hypothesis H is valid")
    state.add_task(Task(
        id="task_1",
        description="Test hypothesis H against the discovered counterexample",
        role="counterexample_hunter",
        reason="The researcher found evidence that may contradict H.",
        context="Earlier research identified input X where H may fail.",
    ))

    captured = {}

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(agent, messages, **kwargs):
        captured["prompt"] = messages[-1].content
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    await run_task(state, "task_1", adapters=[])

    prompt = captured["prompt"]

    assert "Determine whether hypothesis H is valid" in prompt
    assert "Test hypothesis H against the discovered counterexample" in prompt
    assert "The researcher found evidence that may contradict H." in prompt
    assert "Earlier research identified input X where H may fail." in prompt
    assert "Do not independently solve the entire original problem" in prompt


@pytest.mark.asyncio
async def test_dynamic_planner_context_is_propagated_into_task(monkeypatch):
    from app.orchestrator.dynamic import run_dynamic
    from app.orchestrator.planner import TaskProposal

    class FakePlanner:
        async def propose(self, state, **kwargs):
            if len(state.contributions) == 1:
                return False, [
                    TaskProposal(
                        description="Attack the discovered claim",
                        role="counterexample_hunter",
                        depends_on=["task_1"],
                        reason="The first researcher made a claim that needs falsification.",
                        context="The claim depends on assumption A.",
                    )
                ]

            return True, []

    monkeypatch.setattr(
        "app.orchestrator.dynamic.LLMPlanner",
        FakePlanner,
    )

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    async def fake_run(*args, **kwargs):
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    state = await run_dynamic(
        "Investigate the claim",
        adapters=[],
    )

    task = state.tasks["task_2"]

    assert task.reason == (
        "The first researcher made a claim that needs falsification."
    )
    assert task.context == "The claim depends on assumption A."


@pytest.mark.asyncio
async def test_dynamic_synthesizer_receives_investigation_state(monkeypatch):
    from app.orchestrator.dynamic import synthesize_dynamic

    state = ProblemState("Determine whether approach X works")

    state.add_task(Task(
        id="task_1",
        description="Investigate approach X",
        role="researcher",
    ))

    state.add_task(Task(
        id="task_2",
        description="Try to falsify the result",
        role="counterexample_hunter",
        depends_on=["task_1"],
        reason="The first investigation produced a claim requiring attack.",
        context="The first result relies on assumption A.",
    ))

    state.add_contribution(
        __import__("app.orchestrator.state", fromlist=["Contribution"]).Contribution(
            id="task_1:result",
            task_id="task_1",
            agent="dynamic:researcher",
            role="researcher",
            content="Approach X appears to work under assumption A.",
        )
    )

    state.add_contribution(
        __import__("app.orchestrator.state", fromlist=["Contribution"]).Contribution(
            id="task_2:result",
            task_id="task_2",
            agent="dynamic:counterexample_hunter",
            role="counterexample_hunter",
            content="Counterexample Y breaks X when assumption A is removed.",
        )
    )

    state.verified_claims.append(
        "Approach X works only when assumption A holds."
    )

    state.contradictions.append(
        "The original broad claim failed outside assumption A."
    )

    state.failed_attempts.append(
        "Attempting to prove X unconditionally failed."
    )

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    captured = {}

    async def fake_run(agent, messages, **kwargs):
        captured["prompt"] = messages[-1].content
        return FakeResult()

    monkeypatch.setattr(
        "app.orchestrator.dynamic.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_tool_agent",
        fake_run,
    )

    result = await synthesize_dynamic(
        state,
        adapters=[],
    )

    prompt = captured["prompt"]

    assert result.text == "dynamic result"

    assert "Determine whether approach X works" in prompt
    assert "Approach X appears to work under assumption A." in prompt
    assert "Counterexample Y breaks X when assumption A is removed." in prompt
    assert "Approach X works only when assumption A holds." in prompt
    assert "The original broad claim failed outside assumption A." in prompt
    assert "Attempting to prove X unconditionally failed." in prompt

    # Most importantly: this is explicitly an investigation, not
    # independent candidate-answer selection.
    assert "NOT choosing the best answer from competing independent answers" in prompt
    assert "Do not describe this as a council vote or majority decision." in prompt


def test_extract_state_update_parses_worker_findings():
    from app.orchestrator.dynamic import extract_state_update

    content = """Investigation complete.

```json
{
  "findings": {
    "facts": ["Fact A"],
    "hypotheses": ["Hypothesis B"],
    "contradictions": ["Contradiction C"],
    "verified_claims": ["Claim D"],
    "failed_attempts": ["Attempt E"],
    "unresolved_questions": ["Question F"]
  }
}
```"""

    update = extract_state_update(content)

    assert update == {
        "facts": ["Fact A"],
        "hypotheses": ["Hypothesis B"],
        "contradictions": ["Contradiction C"],
        "verified_claims": ["Claim D"],
        "failed_attempts": ["Attempt E"],
        "unresolved_questions": ["Question F"],
    }


def test_apply_state_update_merges_and_deduplicates():
    from app.orchestrator.dynamic import apply_state_update

    state = ProblemState("Test state")
    state.facts.append("Existing fact")

    apply_state_update(
        state,
        {
            "facts": ["Existing fact", "New fact"],
            "unresolved_questions": ["What remains unknown?"],
        },
    )

    assert state.facts == ["Existing fact", "New fact"]
    assert state.unresolved_questions == ["What remains unknown?"]


@pytest.mark.asyncio
async def test_planner_receives_unresolved_questions(monkeypatch):
    from app.orchestrator.planner import LLMPlanner

    state = ProblemState("Determine whether approach X works")
    state.unresolved_questions.append(
        "Does approach X still work when input Y is present?"
    )

    planner = LLMPlanner()

    class Candidate:
        provider_id = "fake-provider"

        class Model:
            id = "fake-model"

        model = Model()

    async def fake_rank(*args, **kwargs):
        return [Candidate()]

    captured = {}

    async def fake_run(agent, messages, **kwargs):
        captured["prompt"] = messages[-1].content

        class PlannerResult:
            text = '{"done": true, "tasks": []}'

        return PlannerResult()

    monkeypatch.setattr(
        "app.orchestrator.planner.rank_candidates",
        fake_rank,
    )
    monkeypatch.setattr(
        "app.orchestrator.planner.run_tool_agent",
        fake_run,
    )

    await planner.propose(state, adapters=[])

    assert "Does approach X still work when input Y is present?" in captured["prompt"]


@pytest.mark.asyncio
async def test_dynamic_loop_adapts_to_new_findings(monkeypatch):
    from app.orchestrator.dynamic import run_dynamic
    from app.orchestrator.planner import TaskProposal

    state_steps = []

    async def fake_run_task(state, task_id, **kwargs):
        state.set_task_status(task_id, "completed")
        if task_id == "task_1":
            state.unresolved_questions.append("Does X work on case C?")
        elif task_id == "task_2":
            state.failed_attempts.append("X fails on case C.")
            state.contradictions.append("X was expected to work on case C.")

    class FakePlanner:
        calls = 0

        async def propose(self, state, **kwargs):
            self.calls += 1
            state_steps.append(
                (
                    self.calls,
                    list(state.unresolved_questions),
                    list(state.failed_attempts),
                    list(state.contradictions),
                )
            )

            if self.calls == 1:
                return False, [
                    TaskProposal(
                        description="Test X on case C",
                        role="tester",
                        depends_on=["task_1"],
                    )
                ]

            if self.calls == 2:
                return False, [
                    TaskProposal(
                        description="Investigate why X fails on case C",
                        role="researcher",
                        depends_on=["task_2"],
                    )
                ]

            return True, []

    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_task",
        fake_run_task,
    )
    monkeypatch.setattr(
        "app.orchestrator.dynamic.LLMPlanner",
        FakePlanner,
    )

    state = await run_dynamic(
        "Determine whether approach X works.",
        adapters=[],
        max_steps=5,
    )

    assert "Does X work on case C?" in state.unresolved_questions
    assert "X fails on case C." in state.failed_attempts
    assert "X was expected to work on case C." in state.contradictions

    assert state.tasks["task_2"].description == "Test X on case C"
    assert state.tasks["task_3"].description == (
        "Investigate why X fails on case C"
    )

    assert len(state_steps) >= 2
    assert "Does X work on case C?" in state_steps[0][1]


@pytest.mark.asyncio
async def test_run_until_complete_runs_independent_tasks_in_parallel(monkeypatch):
    from app.orchestrator.dynamic import run_until_complete

    state = ProblemState("Run independent tasks together")

    state.add_task(Task(
        id="task_a",
        description="Independent task A",
        role="researcher",
    ))
    state.add_task(Task(
        id="task_b",
        description="Independent task B",
        role="researcher",
    ))

    started = set()
    both_started = asyncio.Event()

    async def fake_run_task(state, task_id, **kwargs):
        started.add(task_id)

        if len(started) == 2:
            both_started.set()

        await asyncio.wait_for(both_started.wait(), timeout=1)

        state.set_task_status(task_id, "completed")

    monkeypatch.setattr(
        "app.orchestrator.dynamic.run_task",
        fake_run_task,
    )

    await run_until_complete(state, adapters=[])

    assert started == {"task_a", "task_b"}
    assert state.tasks["task_a"].status == "completed"
    assert state.tasks["task_b"].status == "completed"
    assert state.is_complete()
