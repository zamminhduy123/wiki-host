"""
routers/webhook.py — POST /webhook/telegram

Architecture notes:
- The route handler returns 200 OK *immediately* after basic validation.
  Heavy work (GitHub I/O, two Gemini calls) runs inside a BackgroundTask.
  This prevents Telegram from timing out and retrying the webhook, which
  would cause duplicate processing.

- Optional webhook secret validation: if TELEGRAM_SECRET_TOKEN is set in
  the environment, every request must carry an X-Telegram-Bot-Api-Secret-Token
  header matching that value.  Mismatches are rejected with 403.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from agents import librarian
from config import settings
from llm import factory as llm_factory
from models import TelegramUpdate
from services import document_service, github_service, telegram_service

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Background pipeline ──────────────────────────────────────────────────────


async def _run_pipeline(raw_text: str, chat_id: int) -> None:
    """
    Full ingest pipeline executed as a background task.

    Wraps everything in try/except so that any failure sends a Telegram
    error notification instead of silently disappearing.
    """
    try:
        # ── Step 1: GitHub Read Phase ──────────────────────────────────
        logger.info("[Pipeline] Fetching wiki/index.md & SCHEMA.md…")
        index_content = github_service.ensure_index_exists()
        schema_file = github_service.get_file(settings.schema_path)
        schema_content = schema_file.content if schema_file else ""

        # ── Step 2: Initialize LLM Provider & Condense (if needed) ────────
        provider = llm_factory.get_provider()

        # If the input is massive, condense it into high-density facts first.
        # This protects the Librarian's context window.
        if len(raw_text) > 40000:
            logger.info("[Pipeline] Document is large (%.1fk chars). Condensing...", len(raw_text)/1000)
            condensed_text = await document_service.condense_document(
                raw_text, provider
            )
            context_text = f"ANALYSIS OF LARGE SOURCE:\n\n{condensed_text}"
        else:
            context_text = raw_text
        
        logger.info("[Pipeline] Librarian Call 1: routing & file selection…")
        routing = librarian.route_and_select(context_text, index_content, schema_content, provider)

        # ── Step 3: File Fetching Phase ───────────────────────────────────
        # ... (rest stays the same, but we use context_text for librarian)
        paths_to_fetch: list[str] = [
            p for p in routing.files_to_fetch
            if p != settings.index_path  # index is already loaded
        ]
        logger.info("[Pipeline] Fetching %d file(s): %s", len(paths_to_fetch), paths_to_fetch)
        fetched_map = github_service.fetch_files(paths_to_fetch)

        # Build the dict the Librarian will receive: path → content
        fetched_contents: dict[str, str] = {
            path: repo_file.content for path, repo_file in fetched_map.items()
        }

        # ── Step 4: Librarian Call 2 — Compilation ───────────────────────
        logger.info("[Pipeline] Librarian Call 2: compilation…")
        librarian_output = librarian.compile_updates(
            raw_text=context_text,
            index_content=index_content,
            schema_content=schema_content,
            fetched_files=fetched_contents,
            provider=provider,
        )

        # ── Step 5: Build Batch Commit Payload ────────────────────────────
        # Start with all files the Librarian rewrote.
        commit_payload: dict[str, str] = {
            f.filename: f.new_content for f in librarian_output.updated_files
        }

        # Add the immutable raw source dump.
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        raw_source_path = f"{settings.raw_sources_dir}/{timestamp}.txt"
        commit_payload[raw_source_path] = raw_text
        logger.info("[Pipeline] Raw source will be saved to '%s'.", raw_source_path)

        # ── Step 6: GitHub Write Phase (single batch commit) ──────────────
        logger.info(
            "[Pipeline] Batch-committing %d file(s) to GitHub…",
            len(commit_payload),
        )
        commit_sha = github_service.batch_commit(
            file_updates=commit_payload,
            commit_message="Librarian: Automated wiki update via Telegram",
        )

        # ── Step 7: Append to wiki/log.md (lightweight second commit) ───
        if librarian_output.log_entry:
            try:
                github_service.append_to_log(librarian_output.log_entry)
                logger.info("[Pipeline] Log entry written to wiki/log.md.")
            except Exception as log_exc:
                # Non-fatal: log failures should never break ingest
                logger.warning("[Pipeline] Could not append to log: %s", log_exc)

        # ── Step 8: Telegram Success Notification ───────────────────────
        await telegram_service.notify_success(
            chat_id=chat_id,
            summary=librarian_output.summary,
            commit_sha=commit_sha,
        )
        logger.info("[Pipeline] ✅ Pipeline completed. Commit: %s", commit_sha)

    except Exception as exc:  # noqa: BLE001
        logger.error("[Pipeline] ❌ Unhandled error:\n%s", traceback.format_exc())
        short_error = f"{type(exc).__name__}: {exc}"
        await telegram_service.notify_failure(chat_id=chat_id, error_detail=short_error)


# ─── Webhook endpoint ─────────────────────────────────────────────────────────


@router.post("/webhook/telegram", status_code=200)
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
) -> JSONResponse:
    """
    Telegram webhook receiver.

    Returns 200 OK immediately. The actual ingest pipeline runs as a
    background task so Telegram never waits and never retries.
    """
    # ── Optional secret token validation ──────────────────────────────────
    if settings.telegram_secret_token:
        if x_telegram_bot_api_secret_token != settings.telegram_secret_token:
            logger.warning("Webhook rejected: invalid or missing secret token.")
            raise HTTPException(status_code=403, detail="Invalid secret token.")

    # ── Parse and validate Telegram payload ───────────────────────────────
    try:
        body = await request.json()
        update = TelegramUpdate.model_validate(body)
    except Exception as exc:
        logger.warning("Failed to parse Telegram update: %s", exc)
        # Still return 200 so Telegram stops retrying a malformed payload.
        return JSONResponse(content={"ok": True, "detail": "Parse error — ignored."})

    # ── Filter: check for text or document ────────────────────────────────
    if update.message is None:
        return JSONResponse(content={"ok": True, "detail": "Empty message — ignored."})
        
    chat_id: int = update.message.chat.id
    
    # Check if this is a document upload
    if update.message.document:
        doc = update.message.document
        file_id = doc.file_id
        file_name = doc.file_name or "document"
        mime_type = doc.mime_type or ""
        caption = update.message.caption or ""
        
        logger.info("Received document %s (mime: %s) from chat_id=%d.", file_name, mime_type, chat_id)
        
        # In a generic background task so we don't hold up Telegram
        async def process_document_background():
            try:
                # 1. Download file
                file_bytes = await telegram_service.download_file(file_id)
                
                # 2. Extract text (supports PDF, TXT, MD)
                extracted_text = document_service.extract_text(file_bytes, file_name, mime_type)
                
                # 3. Combine with caption if the user sent instructions
                final_text = f"Attached document ({file_name}):\n\n```\n{extracted_text}\n```\n"
                if caption:
                    final_text += f"\nUser instructions: {caption}"
                    
                # 4. Give the raw text to the normal pipeline
                await _run_pipeline(raw_text=final_text, chat_id=chat_id)
                
            except ValueError as e:
                logger.warning("Document processing failed: %s", e)
                await telegram_service.notify_failure(chat_id, f"Could not process document: {e}")
            except Exception as e:
                logger.error("Error processing document: %s", e)
                await telegram_service.notify_failure(chat_id, "An unexpected error occurred while processing the document.")
                
        background_tasks.add_task(process_document_background)
        return JSONResponse(content={"ok": True, "detail": "Document processing started."})
        
    # Not a document, check if it's text
    if not update.message.text:
        logger.info("Ignoring non-text/non-document update (update_id=%d).", update.update_id)
        return JSONResponse(content={"ok": True, "detail": "Non-text/document message — ignored."})

    raw_text: str = update.message.text.strip()

    logger.info(
        "Received message from chat_id=%d (update_id=%d): %.80s…",
        chat_id,
        update.update_id,
        raw_text,
    )

    # ── Dispatch pipeline to background ───────────────────────────────────
    background_tasks.add_task(_run_pipeline, raw_text=raw_text, chat_id=chat_id)

    return JSONResponse(content={"ok": True, "detail": "Processing started."})
