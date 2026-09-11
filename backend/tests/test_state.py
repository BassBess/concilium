from app.orchestrator.state import ProblemState, Task, Contribution


def test_problem_state():
    state = ProblemState("test problem")

    task = Task(
        id="t1",
        description="investigate",
        role="researcher",
    )

    state.add_task(task)

    assert "t1" in state.tasks
    assert state.tasks["t1"].status == "pending"
    assert state.pending_tasks() == [task]


def test_contribution():
    state = ProblemState("test problem")

    state.add_task(
        Task(
            id="t1",
            description="investigate",
            role="researcher",
        )
    )

    state.add_contribution(
        Contribution(
            id="c1",
            task_id="t1",
            agent="agent1",
            role="researcher",
            content="found X",
            confidence=0.9,
        )
    )

    assert len(state.contributions) == 1
    assert state.contributions[0].content == "found X"
    assert state.contributions[0].confidence == 0.9


def test_runnable_tasks_respect_dependencies():
    state = ProblemState("dependency test")

    state.add_task(Task(
        id="research",
        description="Research",
    ))

    state.add_task(Task(
        id="verify",
        description="Verify",
        depends_on=["research"],
    ))

    state.add_task(Task(
        id="write",
        description="Write",
        depends_on=["verify"],
    ))

    assert [t.id for t in state.runnable_tasks()] == ["research"]

    state.set_task_status("research", "completed")

    assert [t.id for t in state.runnable_tasks()] == ["verify"]

    state.set_task_status("verify", "completed")

    assert [t.id for t in state.runnable_tasks()] == ["write"]


def test_is_complete():
    state = ProblemState("completion test")

    state.add_task(Task(
        id="t1",
        description="First task",
    ))

    assert state.is_complete() is False

    state.set_task_status("t1", "completed")

    assert state.is_complete() is True


def test_dynamic_task_creation():
    state = ProblemState("dynamic planning test")

    state.add_task(Task(
        id="research",
        description="Research the problem",
    ))

    state.set_task_status("research", "completed")

    state.add_task(Task(
        id="verify_research",
        description="Verify the research findings",
        role="verifier",
        depends_on=["research"],
    ))

    assert [t.id for t in state.runnable_tasks()] == ["verify_research"]
    assert state.is_complete() is False


def test_planner_can_create_followup_task():
    from app.orchestrator.planner import Planner

    state = ProblemState("planner test")

    state.add_task(Task(
        id="research",
        description="Research the problem",
        role="researcher",
    ))

    state.set_task_status("research", "completed")

    planner = Planner()

    proposal = planner.propose_verification(state, "research")
    task = planner.apply(state, proposal, prefix="verify")

    assert task.id == "verify_1"
    assert task.role == "verifier"
    assert task.depends_on == ["research"]
    assert [t.id for t in state.runnable_tasks()] == ["verify_1"]


def test_planner_can_create_followup_task():
    from app.orchestrator.planner import Planner

    state = ProblemState("planner test")

    state.add_task(Task(
        id="research",
        description="Research the problem",
        role="researcher",
    ))

    state.set_task_status("research", "completed")

    planner = Planner()

    proposal = planner.propose_verification(state, "research")
    task = planner.apply(state, proposal, prefix="verify")

    assert task.id == "verify_1"
    assert task.role == "verifier"
    assert task.depends_on == ["research"]
    assert [t.id for t in state.runnable_tasks()] == ["verify_1"]


def test_planner_can_create_followup_task():
    from app.orchestrator.planner import Planner

    state = ProblemState("planner test")

    state.add_task(Task(
        id="research",
        description="Research the problem",
        role="researcher",
    ))

    state.set_task_status("research", "completed")

    planner = Planner()

    proposal = planner.propose_verification(state, "research")
    task = planner.apply(state, proposal, prefix="verify")

    assert task.id == "verify_1"
    assert task.role == "verifier"
    assert task.depends_on == ["research"]
    assert [t.id for t in state.runnable_tasks()] == ["verify_1"]


def test_planner_rejects_unknown_task():
    from app.orchestrator.planner import Planner

    state = ProblemState("planner test")
    planner = Planner()

    try:
        planner.propose_verification(state, "does_not_exist")
    except ValueError as exc:
        assert "Unknown task" in str(exc)
    else:
        raise AssertionError("Planner accepted an unknown task")
