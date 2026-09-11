"""Idempotent first-run seeding: provider rows, tools, workflows, templates."""
from __future__ import annotations

from sqlalchemy import select

from .db import SessionLocal
from .models import (
    Conversation,
    Project,
    PromptTemplate,
    ProviderConfig,
    Setting,
    ToolConfig,
    Workflow,
)
from .providers.registry import ADAPTER_TYPES

# Provider types offered as built-in (but disabled until configured).
BUILTIN_PROVIDER_TYPES = [
    "openai", "anthropic", "google", "groq", "mistral", "together",
    "openrouter", "xai", "cohere", "ollama", "lmstudio", "mock",
]

BUILTIN_TOOLS = [
    ("calculator", True, {}, {}),
    ("datetime", True, {}, {}),
    ("knowledge_search", True, {}, {}),
    ("web_search", False, {}, {"auto_approve": True}),
    ("python_sandbox", False, {}, {"auto_approve": False}),
]

BUILTIN_TEMPLATES = [
    ("Helpful assistant", "system", "You are a helpful, rigorous assistant. Answer the question: {{question}}"),
    ("Code reviewer", "role",
     "You are a senior code reviewer for:\n{{previous_answer}}\n\nProject context: {{project_context}}\n"
     "List correctness issues, security concerns, and concrete fixes."),
    ("Research brief", "system",
     "Using these findings:\n{{research}}\n\nWrite a structured brief answering: {{question}}. "
     "Cite sources inline and mark uncertain claims."),
    ("Council default persona", "system",
     "You are an independent expert contributing to a multi-model council on: {{question}}. "
     "Think independently; do not defer to other models."),
]


async def seed() -> None:
    async with SessionLocal() as session:
        # --- providers ------------------------------------------------------
        for ptype in BUILTIN_PROVIDER_TYPES:
            cls = ADAPTER_TYPES.get(ptype)
            if cls is None:
                continue
            existing = (await session.execute(
                select(ProviderConfig).where(ProviderConfig.type == ptype)
            )).scalars().first()
            if existing:
                continue
            is_mock = ptype == "mock"
            local = cls.kind == "local"
            session.add(ProviderConfig(
                type=ptype,
                label=cls.display_name,
                enabled=is_mock,  # mock works offline; everything else needs a key/local app
                builtin=True,
                settings={"mock_delay_ms": 20} if is_mock else {},
            ))
        await session.flush()

        mock_row = (await session.execute(
            select(ProviderConfig).where(ProviderConfig.type == "mock")
        )).scalars().first()
        mock_id = mock_row.id if mock_row else None

        # --- tools ----------------------------------------------------------
        for key, enabled, settings, perms in BUILTIN_TOOLS:
            if not (await session.execute(select(ToolConfig).where(ToolConfig.key == key))).scalars().first():
                session.add(ToolConfig(key=key, enabled=enabled, settings=settings, permissions=perms))

        # --- default project + conversation --------------------------------
        project = (await session.execute(
            select(Project).where(Project.name == "Default project")
        )).scalars().first()
        if not project:
            project = Project(name="Default project",
                              description="Your first project. Projects keep separate conversations, documents and memory.")
            session.add(project)
            await session.flush()
            session.add(Conversation(
                project_id=project.id, title="New conversation", mode="council",
                config={"note": "seeded"}))

        # --- settings -------------------------------------------------------
        if not await session.get(Setting, "setup_completed"):
            session.add(Setting(key="setup_completed", value={"done": False}))
        if not await session.get(Setting, "app"):
            session.add(Setting(key="app", value={
                "default_concurrency": 4, "prefer_free": True, "failover": True,
                "default_mode": "council", "default_council_size": 3,
            }))

        # --- built-in workflows --------------------------------------------
        if mock_id and not (await session.execute(
            select(Workflow).where(Workflow.name == "Classic council")
        )).scalars().first():
            mock_ref = lambda model: {"provider_id": mock_id, "model": model}  # noqa: E731
            session.add(Workflow(
                name="Classic council", builtin=True,
                description="Independent answers, cross-critique, revisions, then synthesis.",
                definition={
                    "mode": "council",
                    "participants": [
                        {"label": "Researcher", "role": "researcher", "ref": mock_ref("mock-general")},
                        {"label": "Reasoner", "role": "mathematician", "ref": mock_ref("mock-reasoner")},
                        {"label": "Programmer", "role": "programmer", "ref": mock_ref("mock-coder")},
                    ],
                    "parallel": True, "critique_rounds": 1, "revision": True,
                    "synthesizer": {"role": "synthesizer", "ref": mock_ref("mock-general")},
                },
            ))
            session.add(Workflow(
                name="Research pipeline", builtin=True,
                description="Researcher → fact checker → critic → synthesizer.",
                definition={
                    "nodes": [
                        {"id": "research", "role": "researcher", "count": 2, "tools": True},
                        {"id": "factcheck", "role": "fact_checker", "count": 1,
                         "depends_on": ["research"]},
                        {"id": "critic", "role": "critic", "count": 1,
                         "depends_on": ["research", "factcheck"]},
                        {"id": "write", "role": "synthesizer", "count": 1,
                         "depends_on": ["critic", "research"], "terminal": True},
                    ]
                },
            ))
            session.add(Workflow(
                name="Coding gauntlet", builtin=True,
                description="Two programmers → code review → judge → final implementation.",
                definition={
                    "nodes": [
                        {"id": "code", "role": "programmer", "count": 2},
                        {"id": "review", "role": "critic", "count": 1, "depends_on": ["code"]},
                        {"id": "judge", "role": "judge", "count": 1,
                         "depends_on": ["code", "review"]},
                        {"id": "final", "role": "programmer", "count": 1,
                         "depends_on": ["judge", "review"], "terminal": True,
                         "instruction": "Produce the final merged implementation incorporating the review."},
                    ]
                },
            ))

        # --- prompt templates ----------------------------------------------
        for name, kind, content in BUILTIN_TEMPLATES:
            if not (await session.execute(
                select(PromptTemplate).where(PromptTemplate.name == name)
            )).scalars().first():
                session.add(PromptTemplate(name=name, kind=kind, content=content))

        await session.commit()
