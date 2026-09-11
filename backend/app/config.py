"""Application configuration, loaded from environment / .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CONCILIUM_", env_file=".env", extra="ignore")

    secret_key: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/concilium.db"
    data_dir: str = "./data"

    # Auth
    password: str = ""  # if set, the UI requires a login

    # Network
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:8000,http://127.0.0.1:5173"

    # Tools / sandbox
    enable_python_sandbox: bool = False
    sandbox_timeout: int = 8
    sandbox_memory_mb: int = 256

    # Orchestration defaults
    default_concurrency: int = 4
    default_max_retries: int = 2
    default_cooldown_seconds: int = 30

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Ensure data dir exists at import time
    settings.data_path.mkdir(parents=True, exist_ok=True)
    return settings


# Environment variables that are NOT prefixed (provider keys) are read directly.
PROVIDER_ENV_KEYS = {
    "openai": {"api_key": "OPENAI_API_KEY", "base_url": "OPENAI_BASE_URL"},
    "anthropic": {"api_key": "ANTHROPIC_API_KEY"},
    "google": {"api_key": "GOOGLE_API_KEY"},
    "groq": {"api_key": "GROQ_API_KEY"},
    "mistral": {"api_key": "MISTRAL_API_KEY"},
    "together": {"api_key": "TOGETHER_API_KEY"},
    "openrouter": {"api_key": "OPENROADER_API_KEY"},
    "xai": {"api_key": "XAI_API_KEY"},
    "cohere": {"api_key": "COHERE_API_KEY"},
    "ollama": {"base_url": "OLLAMA_BASE_URL"},
    "lmstudio": {"base_url": "LMSTUDIO_BASE_URL"},
}


def env_for_provider(provider_type: str) -> dict[str, str]:
    """Return credentials for a provider that were supplied via environment."""
    out: dict[str, str] = {}
    for field, env_name in PROVIDER_ENV_KEYS.get(provider_type, {}).items():
        val = os.environ.get(env_name, "")
        if val:
            out[field] = val
    return out
