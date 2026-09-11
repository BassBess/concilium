# ARCHITECTURE

## High-level flow

```
Browser (React/TS SPA, SSE client)
  │  REST /api/*  +  GET /api/runs/{id}/events (SSE)
  ▼
FastAPI app (app/main.py)
  ├── middleware: rate limit, security headers, CORS, error containment
  ├── lifespan: init DB → seed → provider registry reload → tool configs load
  ▼
API layer (app/api/)
  chat · providers · library(workflows/prompts) · tools · usage · system/wizard · auth
  │  POST /conversations/{id}/runs  ── creates User msg + Run row, schedules engine task
  ▼
Orchestration (app/orchestrator/)
  engine.py   mode dispatch + context/research + persistence + buffered final streaming
  council.py  independent → critique rounds → revisions → synthesis
  workflow.py DAG executor (topological levels, fan-out count, fan-in terminal nodes)
  router.py   task analyzer + candidate ranker
  runner.py   resilient_call / run_tool_agent (semaphores, retries, cooldowns, failover)
  quotas.py   RPM/TPM windows, daily/monthly caps, cooldowns, counters, semaphores
  events.py   per-run pub/sub bus (history replay → SSE)
  roles.py    built-in agent personas
  prompts.py  stage prompts + {{variable}} renderer
  presets.py  auto-council builder
  ▼
Providers (app/providers/)
  registry.py    @register_provider auto-discovery, instance lifecycle
  base.py        ProviderAdapter ABC, ModelInfo/Capabilities/Usage/errors
  catalog.py     static model metadata (context, caps, list-price estimates)
  openai_compatible.py → openai, groq, together, openrouter, xai, mistral, lmstudio, custom
  anthropic.py · google.py · cohere.py · ollama.py · mock.py
  ▼
Tools (app/tools/) — base registry + SSRF guard, calculator, datetime, websearch,
                     knowledge (document retrieval), python_sandbox
  ▼
Persistence (app/models.py, SQLAlchemy 2 async)
  projects · conversations · messages · runs(traces JSON) · provider_configs
  workflows · prompt_templates · tool_configs · usage_records · documents · settings
  → SQLite via aiosqlite (or Postgres via asyncpg)
```

## Request lifecycle (council)

1. `POST /conversations/{id}/runs` validates input (participant/round caps), writes
   a `user` Message and a `Run(status=running, trace={config})`, schedules
   `engine.execute_run` as a background asyncio task, returns `run_id`.
2. Browser opens `GET /api/runs/{id}/events` (SSE). The bus replays any missed
   history and streams new events; on (re)connect after completion, events come
   from the persisted trace.
3. Engine loads prior turns, optionally runs **deep research** (tool results
   labeled untrusted), then runs `run_council`:
   - **R0** — N participants called concurrently through `resilient_call`
     (per-provider semaphores bound concurrency).
   - **R1..k** — each model critiques all answers.
   - **Revisions** — each model revises using the critique set.
   - **Synthesis** — one synthesizer gets everything; on total failure, a
     deterministic aggregation is emitted instead (`synthesis_fallback`).
4. Every attempt publishes events (`agent_started`, `agent_log`, `cooldown_*`,
   `tool_*`, `agent_finished`, …). Agent outputs persist as `agent` Messages;
   the final text persists as an `assistant/final` Message; it is then streamed
   to the browser in buffered `final_chunk` events (uniform across providers,
   while preserving real usage accounting).
5. The terminal event is appended before `Run.trace` is persisted, so historical
   traces are complete.

## Resilience model (runner + quotas)

```
candidate chain: [primary] + failover ranked list
                 (different providers first → same provider's other models)
for each candidate:
    quota.check()            # cooldown? rpm window? daily cap? → skip
    semaphore(provider)      # concurrency limit
    for attempt in 0..max_retries:
        call adapter.generate()
          200 + content     → mark_success, persist UsageRecord, emit finished
          429 / quota text  → mark_rate_limited(retry-after|exp backoff), break
          401/403           → record error, break (failover continues)
          5xx/malformed/net → backoff 0.4·2^n (capped), retry
          empty body        → malformed → retry
all candidates failed       → AllCandidatesFailed
```

- Cooldown = wall-clock deadline per `provider/model`; router heavily demotes
  cooling candidates; they automatically rejoin when the deadline passes.
- Counters: sliding 60 s window, UTC-day and UTC-month buckets, rolling latency
  average, successes, consecutive failures, rate-limit count.
- Each call writes a `usage_records` row (success / ratelimit / retry / error) —
  telemetry failures are swallowed so accounting never blocks an answer.
- Mock provider implements `fail_once`, `fail_every`, `rate_limit_every` and
  model-scoped forced limits — the test suite and wizard use these to prove the
  behavior without faking any real provider.

## Provider abstraction

```python
class ProviderAdapter:
    type_key, display_name, kind (local|remote), secret_fields, setting_fields
    is_configured() -> bool
    async list_models() -> list[ModelInfo]      # static catalog + live merge
    async get_models(force_refresh)             # cached live listing
    async generate(messages, model, options)    # → GenerationResult(text, usage, tool_calls)
    async stream(messages, model, options)      # async chunks; default = buffered generate
    async health_check() -> {status, models, detail}
    get_capabilities(model) / get_limits(model)
```

- Provider **instances** are DB rows (`ProviderConfig`), so custom OpenAI-
  compatible endpoints can be added at runtime, including multiple instances.
- Secrets in settings are Fernet-encrypted (`enc::…`) before storage; the
  registry decrypts only when constructing in-memory adapters.
- Adapters map HTTP errors to typed exceptions in `_util.raise_for_http_status`;
  engines only ever catch those types.
- `OpenAICompatibleAdapter` implements payload/tool/stream mapping once; Groq,
  Together, OpenRouter, xAI, Mistral and LM Studio differ only by base URL and
  headers. Live `/models` listings are merged with the static catalog, with
  capability heuristics for unknown ids.

## Tools & security

- Tools declare a JSON-schema for parameters; only enabled+available tools run;
  invocation and permissions live in `tools/base.py::ToolRegistry`.
- Native function calling is used for tool-capable models; a bounded tool loop
  (default 4 rounds) appends assistant tool-calls and `tool` result messages.
- `assert_public_url` resolves hosts and refuses private/link-local/metadata
  ranges (SSRF). Search only talks to Tavily or an operator-configured SearXNG.
- The Python sandbox runs `python -I -B` in a temp cwd with a stripped
  environment and POSIX rlimits (CPU, address space, file size), a wall-clock
  kill, and is gated behind both a server env flag and the Tools enable switch.
- All tool results and research material are wrapped as **untrusted data**
  before being added to model context (prompt-injection mitigation).

## Data model

| Table | Purpose |
|---|---|
| `projects` | Workspace boundary (conversations, documents) |
| `conversations` | Mode + config, title |
| `messages` | `user`, `assistant/final`, `agent/*stage*` outputs with provider/model/tokens/cost |
| `runs` | Mode, question, status, final/error, full event trace (JSON) |
| `provider_configs` | Instances with encrypted secret fields + local limit overrides |
| `workflows` | Saved DAGs or council presets (built-ins flagged) |
| `prompt_templates` | system / role / workflow / snippet texts with `{{variables}}` |
| `tool_configs` | Enable flags, (encrypted) settings, permissions |
| `usage_records` | Per-call accounting stream for the dashboard |
| `documents` | Uploaded UTF-8 text knowledge base per project |
| `settings` | App settings + `setup_completed` |

## Frontend

- React 18 + TypeScript + Vite + zustand; HashRouter; SSE via `EventSource`.
- One global store (`store.ts`) owns data and the **live run** reducer; the
  `CouncilPanel` renders a pure function of the event list (same component is
  reused for historical traces inside a modal).
- The composer switches modes (single/router/council/workflow) with inline
  configuration; model pickers consume `/models/live` (live discovery across
  enabled instances) and fall back to the static catalog.
- No external CSS/font/CDN dependencies — fully self-contained for local/preview.

## Extending

| Want to… | Change |
|---|---|
| Add a provider | one file in `app/providers/` (+ optional catalog rows) |
| Add a tool | subclass `Tool`, register, add seed row |
| Change routing | scoring in `orchestrator/router.py` |
| New persona | prompt template kind `role`, or `roles.py` |
| Swap DB | set `CONCILIUM_DATABASE_URL` (async SQLAlchemy) |
| Move to workers | `engine.execute_run` is already a standalone async task — wrap in arq/Celery |
| Multi-user | replace single-user token middleware; tables already carry project scoping |
| Token-level streaming | wire adapter `stream()` into runner + events |

## Design constraints & honesty notes

- Prices are **estimates** from catalog metadata; the UI labels them as such and
  relies on providers' live usage numbers where returned.
- Free-tier limits differ by account/region; defaults in `DEFAULT_LIMITS` are
  conservative hints, fully editable per provider.
- OpenRouter `:free` model ids rotate; live discovery is the source of truth.
- The sandbox is a hardened subprocess, not a VM; use the provided Docker image
  for hostile code.
