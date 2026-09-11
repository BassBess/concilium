"""Test bootstrap: isolated temp database, seeded registry, API client."""
from __future__ import annotations

import os
import pathlib
import tempfile
import uuid

import pytest

# Configure an isolated environment BEFORE app modules are imported.
_TEST_DIR = pathlib.Path(tempfile.mkdtemp(prefix="concilium_test_"))
os.environ["CONCILIUM_DATA_DIR"] = str(_TEST_DIR)
os.environ["CONCILIUM_DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DIR / 'test.db'}"
os.environ.pop("CONCILIUM_PASSWORD", None)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GOOGLE_API_KEY", None)

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.api.deps import load_tool_configs  # noqa: E402
from app.db import init_db  # noqa: E402
from app.orchestrator.quotas import quota_manager  # noqa: E402
from app.providers.registry import registry  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="session", autouse=True)
async def _bootstrap():
    await init_db()
    await seed()
    await registry.reload()
    await load_tool_configs()
    yield


@pytest.fixture(autouse=True)
def _reset_runtime():
    quota_manager.reset()
    for a in registry.all():
        a.models_cache = None
        if hasattr(a, "_call_count"):
            a._call_count.clear()
            a.force_rate_limit = False
            a.force_error = False
            a.rate_limited_models = set()
            a._fail_once_done = set()
        # restore clean mock settings
        if a.type_key == "mock":
            a.settings = {"mock_delay_ms": 0}
    yield


@pytest.fixture
async def client():
    from app.main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def unique():
    return uuid.uuid4().hex[:8]
