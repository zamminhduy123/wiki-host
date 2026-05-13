"""
routers/query.py — POST /query

The Researcher endpoint: answers questions from the wiki synchronously,
and files high/medium confidence answers back into wiki/analyses/.

Flow:
  1. Receive {"question": "..."}
  2. Fetch wiki/index.md from GitHub
  3. LLM Call 1 (Navigator): Which wiki files are relevant?
  4. Fetch those files from GitHub
  5. LLM Call 2 (Researcher): Synthesise a grounded answer with citations
  6. If confidence >= medium → file answer to wiki/analyses/ (background)
  7. Return the structured JSON answer
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from agents import researcher
from config import settings
from llm import factory as llm_factory
from models import QueryRequest, ResearcherAnswer
from services import github_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _file_answer_back(question: str, result: ResearcherAnswer) -> None:
    """
    Background task: file a good answer into wiki/analyses/ and update the index.

    Only runs for high/medium confidence answers — low confidence answers
    are ephemeral and not worth filing.
    """
    try:
        path, content = researcher.build_analysis_page(question, result)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Fetch current index to append the new analysis entry
        index = github_service.get_file(settings.index_path)
        index_content = index.content if index else ""

        # Append new row to the index table
        new_row = f"| {path} | Analysis: {question[:60]}... |"
        if "| File | Summary |" in index_content:
            updated_index = index_content.rstrip() + f"\n{new_row}\n"
        else:
            updated_index = index_content + f"\n| File | Summary |\n|---|---|\n{new_row}\n"

        # Log entry
        slug = path.split("/")[-1].replace(".md", "")
        log_entry = (
            f"## [{today}] query | {question[:60]}\n"
            f"Answer filed to {path} (confidence: {result.confidence})."
        )

        # Commit analysis page + updated index in one shot
        github_service.batch_commit(
            {path: content, settings.index_path: updated_index},
            f"Librarian: Filed analysis — {question[:60]}",
        )
        logger.info("[Query] ✅ Analysis filed to %s", path)

        # Append to log (separate lightweight commit)
        github_service.append_to_log(log_entry)

    except Exception as exc:
        logger.warning("[Query] Could not file answer back: %s", exc)


@router.post(
    "/query",
    response_model=ResearcherAnswer,
    summary="Ask a question answered from your wiki",
    description=(
        "Sends a natural-language question through a two-stage LLM pipeline "
        "that navigates the wiki index, fetches relevant files, and returns a "
        "grounded answer with source citations. High/medium confidence answers "
        "are automatically filed back into wiki/analyses/."
    ),
    tags=["Researcher"],
)
async def query_wiki(body: QueryRequest, background_tasks: BackgroundTasks) -> JSONResponse:
    """
    POST /query

    Body:  {"question": "What projects am I currently working on?"}
    """
    question = body.question.strip()
    logger.info("[Query] Received question: %.120s", question)

    # ── Step 1: Fetch the wiki index ──────────────────────────────────────
    logger.info("[Query] Fetching wiki/index.md…")
    index_content = github_service.ensure_index_exists()

    # ── Step 2: Initialize LLM Provider & Route ──────────────────────────
    provider = llm_factory.get_provider()

    logger.info("[Query] Researcher Call 1: routing…")
    routing = await researcher.route_query(
        question=question,
        index_content=index_content,
        provider=provider,
    )

    # ── Step 3: Fetch the relevant files (with fallback) ──────────────────
    files_to_read = routing.files_to_read

    if not files_to_read:
        logger.warning("[Query] Routing returned 0 files — falling back to all wiki files.")
        files_to_read = re.findall(r'wiki/[\w\-/]+\.md', index_content)
        files_to_read = [f for f in files_to_read if f not in {settings.index_path, settings.schema_path}]
        logger.info("[Query] Fallback: will fetch %s", files_to_read)

    fetched_map = github_service.fetch_files(files_to_read)
    fetched_contents: dict = {
        path: repo_file.content for path, repo_file in fetched_map.items()
    }
    logger.info("[Query] Fetched %d file(s).", len(fetched_contents))

    # ── Step 4: Researcher Call 2 — Answer synthesis ─────────────────────
    logger.info("[Query] Researcher Call 2: synthesising answer…")
    result = await researcher.answer_query(
        question=question,
        fetched_files=fetched_contents,
        provider=provider,
    )

    logger.info(
        "[Query] ✅ Answer ready (confidence=%s, sources=%d).",
        result.confidence,
        len(result.sources),
    )

    # ── Step 5: File answer back to wiki/analyses/ (if worth keeping) ─────
    if result.confidence in ("high", "medium"):
        logger.info("[Query] Confidence %s — filing answer back to wiki/analyses/.", result.confidence)
        background_tasks.add_task(_file_answer_back, question, result)

    return JSONResponse(content=result.model_dump())
