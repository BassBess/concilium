# Contributing to Concilium

Thanks for your interest! Concilium is organized around four extensibility
axes: **providers, tools, roles/prompts, workflows**. Everything else is glue.

## Development setup

```bash
# backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# frontend (another terminal)
cd frontend
npm install
npm run dev          # http://localhost:5173

# tests (offline, no keys needed)
cd backend && pytest
```

## Code layout

```
backend/app/
  main.py                 FastAPI app, middleware, SPA serving
  config.py               env settings + per-provider env keys
  db.py models.py         async SQLAlchemy
  security.py             Fernet credentials, password hashing, signed tokens
  seed.py                 idempotent first-run seed
  providers/              adapter plugin system (auto-discovered)
  orchestrator/           engine, council, workflow, router, runner, quotas
  tools/                  tool plugin system
  api/                    one route module per domain
backend/tests/            pytest + httpx ASGITransport + mock failure injection
frontend/src/
  pages/                  one file per route
  components/             chat, council trace panel, pickers, shared UI
  store.ts                zustand store + SSE run reducer
  api.ts                  typed fetch wrapper
```

## Conventions

- **Providers implement the typed interface and raise typed errors** — never
  fake responses or bypass limits; see [PROVIDERS.md](PROVIDERS.md).
- Secrets only travel through `secret_fields` (encrypted at rest) and are
  never serialized to the browser except as a masked "configured" indicator.
- New behavior in the resilient path needs a test driven by the mock provider
  (`fail_once`, `rate_limited_models`, `rate_limit_every`, `fail_every`).
- Keep the frontend dependency-light; the app must build without network access
  to CDNs at runtime.
- Python: type-annotated public functions; no sync I/O inside async paths.
- Prefer stdlib + existing deps (httpx, SQLAlchemy, cryptography).

## Testing

```bash
pytest                                  # full suite
pytest tests/test_council.py -x         # one file
pytest -k cooldown                      # by keyword
```

Coverage areas: provider adapters, routing, retries/cooldowns, quotas, parallel
execution, council stages, workflow DAGs, tools (incl. SSRF & sandbox gating),
persistence via the API, auth/encryption, and malformed-provider responses.

## Suggested PRs (good first extensions)

- New `OpenAICompatibleAdapter` subclasses for OpenAI-compatible services.
- Native embeddings + vector knowledge search (capability tag exists).
- Image-generation tool/provider path.
- More routing signals and explainable scoring.
- Postgres/asyncpg example compose profile; arq/Celery worker backend.
- Per-conversation custom roles UI (templates of kind `role` already work).
- Token-by-token streaming through `adapter.stream()` into run events.

## Commit / PR checklist

- [ ] `pytest` green; new resilience behavior has a mock-driven test
- [ ] `npm run build` passes (typecheck)
- [ ] No credentials, tokens, or `.env` committed
- [ ] Catalog prices marked as estimates; unavailable models honestly reported
- [ ] README/PROVIDERS updated for user-visible changes

## Security disclosures

Do not open public issues for security vulnerabilities; describe the impact
and reproduction privately to the maintainer. Never include proofs that access
third-party accounts or data.
