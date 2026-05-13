"""
main.py — FastAPI application entry point for the Librarian Agent.

Run locally with:
    uvicorn main:app --reload --host 0.0.0.0 --port 8000

Or via the helper script:
    python main.py
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings  # triggers env var validation at import time
from routers import lint, query, webhook, ws_query, ollama_proxy

# ─── Logging Setup ───────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)
from services import telegram_service

# ─── Lifespan ────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    logger.info("Application starting up...")
    await telegram_service.set_webhook()
    yield
    # Shutdown logic
    logger.info("Application shutting down...")

# ─── App Factory ─────────────────────────────────────────────────────────────

app = FastAPI(
    title="Librarian Agent",
    description=(
        "Autonomous LLM-powered knowledge management pipeline. "
        "Receives Telegram messages, routes them through Gemini 1.5 Pro, "
        "and batch-commits structured Markdown updates to GitHub."
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ─── CORS Setup ──────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins for ngrok/local dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(webhook.router, tags=["Telegram Webhook"])
app.include_router(query.router, tags=["Researcher"])
app.include_router(ws_query.router, tags=["Real-time Researcher"])
app.include_router(lint.router, tags=["Maintenance"])
app.include_router(ollama_proxy.router, prefix="/ollama", tags=["Ollama Proxy"])



# ─── Health Check ─────────────────────────────────────────────────────────────


@app.get("/health", tags=["Meta"])
async def health_check() -> JSONResponse:
    """
    Liveness probe endpoint.

    Returns basic service info so you can verify the server is up and
    environment variables loaded correctly — without exposing secrets.
    """
    return JSONResponse(
        content={
            "status": "ok",
            "service": "librarian-agent",
            "github_repo": settings.github_repo,
            "github_branch": settings.github_branch,
            "wiki_dir": settings.wiki_dir,
            "raw_sources_dir": settings.raw_sources_dir,
            "secret_validation": settings.telegram_secret_token is not None,
        }
    )


# ─── Dev Server Entry Point ───────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    logger.info("Starting Librarian Agent on %s:%d", host, port)
    uvicorn.run("main:app", host=host, port=port, reload=False)
