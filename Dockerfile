# ============================================================================
# Concilium — single-image production build (React build served by FastAPI)
# ============================================================================

# ---- frontend build ----
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# ---- backend runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# The sandbox tool needs python3 available; it runs the same interpreter with
# -I and POSIX rlimits. curl kept for healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
# FastAPI serves the SPA from ../frontend/dist relative to the backend dir
COPY --from=frontend /fe/dist /srv/frontend/dist

RUN mkdir -p /srv/backend/data
VOLUME ["/srv/backend/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
