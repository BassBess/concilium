"""Concilium API application entrypoint."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api.deps import load_tool_configs
from .api.routes_auth import router as auth_router
from .api.routes_chat import router as chat_router
from .api.routes_library import router as library_router
from .api.routes_providers import router as providers_router
from .api.routes_system import router as system_router
from .api.routes_tools import router as tools_router
from .api.routes_usage import router as usage_router
from .config import get_settings
from .db import init_db
from .providers.registry import registry
from .seed import seed

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed()
    await registry.reload()
    await load_tool_configs()
    yield


app = FastAPI(title="Concilium", version=__version__,
              description="Universal multi-model AI orchestration platform",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Lightweight in-process rate limiting + basic security headers
# (single-user local deployments; a reverse proxy handles this at scale)
# ---------------------------------------------------------------------------

_RATE_BUCKETS: dict[str, deque] = defaultdict(deque)
_RATE_LIMIT = 240  # requests / minute / ip


@app.middleware("http")
async def guard_middleware(request: Request, call_next):
    # rate limit API mutating calls (SSE streams are long-lived; exempt events)
    if request.url.path.startswith("/api/") and not request.url.path.endswith("/events"):
        ip = request.client.host if request.client else "local"
        window = _RATE_BUCKETS[ip]
        now = time.time()
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= _RATE_LIMIT:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)
        window.append(now)
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 — never leak tracebacks to the client
        import traceback as _tb
        _tb.print_exc()
        return JSONResponse({"detail": f"internal error: {type(exc).__name__}: {exc}"}, status_code=500)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__, "name": "Concilium"}


for r in (auth_router, chat_router, providers_router, system_router,
          library_router, tools_router, usage_router):
    app.include_router(r)


# ---------------------------------------------------------------------------
# Serve the built React frontend (production). In dev use Vite (port 5173).
# ---------------------------------------------------------------------------

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str, request: Request):
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = FRONTEND_DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
else:
    @app.get("/")
    async def root_notice() -> dict:
        return {"name": "Concilium", "status": "running",
                "ui": "frontend not built — run `npm install && npm run build` in frontend/, "
                      "or use the Vite dev server on port 5173",
                "docs": "/docs"}


def run() -> None:  # pragma: no cover
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":  # pragma: no cover
    run()
