"""
config.py — Centralised settings loaded from environment variables.

All services import from here so credentials are never scattered across files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """Return the value of an env var or raise a descriptive error at startup."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Copy .env.example → .env and fill in your credentials."
        )
    return value


@dataclass(frozen=True)
class Settings:
    # ── Telegram ──────────────────────────────────────────────────────────────
    telegram_bot_token: str
    # Optional header secret set in Telegram's setWebhook call.
    # If present, every incoming request is validated against this value.
    telegram_secret_token: Optional[str]
    telegram_webhook_url: Optional[str]


    # ── GitHub ────────────────────────────────────────────────────────────────
    github_token: str
    github_repo: str          # "owner/repo"
    github_branch: str        # branch to read from / commit to

    # ── LLM Provider ──────────────────────────────────────────────────────────
    gemini_api_key: str
    llm_provider: str         # "gemini" or "ollama"
    llm_model: Optional[str]  # e.g. "gemini-1.5-pro-latest" or "llama3"
    ollama_base_url: str      # e.g. "http://localhost:11434"
    # Optional base URL for a standalone Llama server (if different from Ollama)
    llama_server_base_url: str  # e.g. "http://localhost:8080"
    llama_server_api_key: Optional[str]  # API Key for llama-server or proxy
    llama_server_api_key: Optional[str]  # e.g. "your-llama-api-key"

    # ── Repo path prefixes ────────────────────────────────────────────────────
    wiki_dir: str             # e.g. "wiki"
    raw_sources_dir: str      # e.g. "raw_sources"

    # ── Derived helpers ───────────────────────────────────────────────────────
    @property
    def index_path(self) -> str:
        return f"{self.wiki_dir}/index.md"

    @property
    def schema_path(self) -> str:
        return f"{self.wiki_dir}/SCHEMA.md"

    @property
    def log_path(self) -> str:
        return f"{self.wiki_dir}/log.md"

    @property
    def overview_path(self) -> str:
        return f"{self.wiki_dir}/overview.md"

    @property
    def sources_dir(self) -> str:
        return f"{self.wiki_dir}/sources"

    @property
    def entities_dir(self) -> str:
        return f"{self.wiki_dir}/entities"

    @property
    def concepts_dir(self) -> str:
        return f"{self.wiki_dir}/concepts"

    @property
    def analyses_dir(self) -> str:
        return f"{self.wiki_dir}/analyses"


def _load() -> Settings:
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_secret_token=os.getenv("TELEGRAM_SECRET_TOKEN"),
        telegram_webhook_url=os.getenv("TELEGRAM_WEBHOOK_URL"),
        github_token=_require("GITHUB_TOKEN"),
        github_repo=_require("GITHUB_REPO"),
        github_branch=os.getenv("GITHUB_BRANCH", "main"),
        gemini_api_key=_require("GEMINI_API_KEY"),
        llm_provider=os.getenv("LLM_PROVIDER", "gemini").lower(),
        llm_model=os.getenv("LLM_MODEL"),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        llama_server_base_url=os.getenv("LLAMA_SERVER_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")),
        llama_server_api_key=os.getenv("LLAMA_SERVER_API_KEY"),
        wiki_dir=os.getenv("WIKI_DIR", "wiki"),
        raw_sources_dir=os.getenv("RAW_SOURCES_DIR", "raw_sources"),
    )


# Module-level singleton — imported by all services
settings: Settings = _load()
