"""
services/telegram_service.py — Send messages back to the Telegram user.

Uses httpx (async) so it integrates cleanly into the FastAPI event loop
without blocking the worker thread.
"""

from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API_BASE = "https://api.telegram.org"


async def send_message(chat_id: int, text: str) -> None:
    """
    Send a plain-text message to a Telegram chat.

    Errors are logged but never re-raised — notification failure must never
    crash the pipeline or cause Telegram to retry the webhook.

    Parameters
    ----------
    chat_id : int
        The target chat / user ID (extracted from the incoming webhook).
    text : str
        Message content (plain text, up to 4096 chars per Telegram limits).
    """
    url = f"{_TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            logger.info("Telegram notification sent to chat_id=%d.", chat_id)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Telegram API returned %s for chat_id=%d: %s",
            exc.response.status_code,
            chat_id,
            exc.response.text,
        )
    except httpx.RequestError as exc:
        logger.error(
            "Network error sending Telegram message to chat_id=%d: %s",
            chat_id,
            exc,
        )


async def notify_success(chat_id: int, summary: str, commit_sha: str) -> None:
    """Send a success notification with the Librarian's summary and commit SHA."""
    short_sha = commit_sha[:7]
    text = (
        f"✅ *Wiki updated!*\n\n"
        f"{summary}\n\n"
        f"📦 Commit: `{short_sha}`"
    )
    await send_message(chat_id, text)


async def notify_failure(chat_id: int, error_detail: str) -> None:
    """Send a failure notification so the user knows something went wrong."""
    text = (
        "❌ *Wiki update failed*\n\n"
        f"Sorry, I ran into a problem:\n`{error_detail}`\n\n"
        "Your raw message was received but could not be integrated. "
        "Please try again or check the server logs."
    )
    await send_message(chat_id, text)


async def download_file(file_id: str) -> bytes:
    """
    Download a file from Telegram using its file_id.
    
    1. Gets the file_path from telegram.
    2. Downloads the actual bytes.
    """
    # 1. Get file path
    get_file_url = f"{_TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/getFile"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(get_file_url, params={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise ValueError(f"Failed to get file path: {data}")
        file_path = data["result"]["file_path"]
        
    # 2. Download the bytes
    download_url = f"{_TELEGRAM_API_BASE}/file/bot{settings.telegram_bot_token}/{file_path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        file_resp = await client.get(download_url)
        file_resp.raise_for_status()
        return file_resp.content


async def set_webhook() -> bool:
    """
    Register the public URL with Telegram as the webhook endpoint.
    Uses TELEGRAM_WEBHOOK_URL and TELEGRAM_SECRET_TOKEN from settings.
    """
    if not settings.telegram_webhook_url:
        logger.info("TELEGRAM_WEBHOOK_URL not set, skipping automatic webhook registration.")
        return False

    url = f"{_TELEGRAM_API_BASE}/bot{settings.telegram_bot_token}/setWebhook"
    payload = {
        "url": settings.telegram_webhook_url,
    }
    if settings.telegram_secret_token:
        payload["secret_token"] = settings.telegram_secret_token

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            if data.get("ok"):
                logger.info("✅ Telegram webhook successfully set to: %s", settings.telegram_webhook_url)
                return True
            else:
                logger.error("❌ Telegram setWebhook failed: %s", data)
                return False
    except Exception as exc:
        logger.error("❌ Network error while setting Telegram webhook: %s", exc)
        return False
