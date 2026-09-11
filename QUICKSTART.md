# QUICKSTART

Get a working council in under 5 minutes, with or without API keys.

## A. Fully offline (zero keys)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --port 8000
# in a second terminal:
cd frontend && npm install && npm run build
```

Open <http://localhost:8000> → the setup wizard runs with the built-in
deterministic **mock models** (they make no network calls). Click through to
**Test** and watch a 3-model council answer, critique, revise and synthesize.

For live UI development use `npm run dev` (http://localhost:5173; API proxied).

## B. Add your first free remote model

The fastest genuinely-free real models:

1. **Google Gemini** — <https://aistudio.google.com/app/apikey> → create API key (free tier).
2. **Groq** — <https://console.groq.com/keys> → create key (free developer tier, very fast).
3. In Concilium: **Providers → Google Gemini → paste key → Save → 🔄 Discover models**
   (same for Groq).
4. Toggle the provider **on**. Models now show a green **ready** dot in **Models**.

## C. Add a local model

```bash
# Ollama (https://ollama.com)
ollama pull llama3.1
ollama serve
# Providers → Ollama (local) → enable → 🔄 Discover models
```

LM Studio: start its Local Server (default :1234), then enable the LM Studio card.

## D. Ask the council

1. **New conversation** → choose **👥 Council**.
2. **⚙ Configure** → **✨ Auto-build council** (diverse models, preferably free).
3. Ask e.g. *"Should a 4-person startup build a microservices architecture?"*
4. Watch the right panel: parallel answers → critiques → revisions → synthesis.
5. Re-open past runs anytime via **🔬 Inspect trace** on a final answer.

## E. Try the other modes

- **🎯 Auto-route**: type a coding question — the router selects a code-specialized
  model; a simple greeting goes to a fast/free model. Click **🧮 Preview routing**.
- **🔀 Workflow**: pick **★ Coding gauntlet** (2 programmers → critic → judge → final).
- **🔎 Deep research**: enable the chip, configure a free Tavily key under **Tools**,
  and web findings are injected before the council starts.

## F. Docker

```bash
docker compose up --build                      # http://localhost:8000
docker compose --profile local up --build      # includes Ollama
```

## Run the tests

```bash
cd backend && pytest        # 60+ tests, fully offline (mock providers + failure injection)
```

## Where things live

- Backend code: `backend/app/` · tests: `backend/tests/`
- Frontend: `frontend/src/` · pages in `frontend/src/pages/`
- SQLite DB, uploads and secret key: `backend/data/` (created automatically)

See [README.md](README.md) for the full guide and [ARCHITECTURE.md](ARCHITECTURE.md)
for internals.
