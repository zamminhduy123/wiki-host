"""
routers/ws_query.py — WebSocket /ws/query

Streams real-time progress updates during the query pipeline.
"""

from __future__ import annotations

import logging
import json
import re

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agents import researcher
from llm import factory as llm_factory
from services import github_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/query")
async def websocket_query(websocket: WebSocket):
    """
    WebSocket endpoint for real-time query progress.
    
    1. Connects.
    2. Waits for {"question": "..."} JSON.
    3. Sends {"status": "..."} updates.
    4. Sends {"answer": {...}} final result.
    5. Closes or waits for another question.
    """
    await websocket.accept()
    logger.info("WebSocket /ws/query connected.")

    try:
        while True:
            # 1. Wait for message
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                question = msg.get("question", "").strip()
            except json.JSONDecodeError:
                await websocket.send_json({"error": "Invalid JSON mapping. Send {'question': '...'}"})
                continue

            if not question:
                await websocket.send_json({"error": "No question provided."})
                continue

            # Callback helper
            def send_status(text: str):
                import asyncio
                # Helper to send JSON in a sync-like callback context
                asyncio.run_coroutine_threadsafe(
                    websocket.send_json({"status": text}), 
                    asyncio.get_event_loop()
                )

            # ─── EXECUTE PIPELINE ───
            await websocket.send_json({"status": "🔗 Establishing secure connection to Duy's wiki..."})
            
            # Step 1: Index
            index_content = github_service.ensure_index_exists()
            await websocket.send_json({"status": "📂 Scanning wiki for relevant documents..."})
            
            # Step 2: Route
            provider = llm_factory.get_provider()
            
            async def on_status(text: str):
                # Map generic agent status to spicy status
                spicy_map = {
                    "Navigating wiki to find relevant files...": "🧠 Analyzing query to identify knowledge domains...",
                }
                await websocket.send_json({"status": spicy_map.get(text, text)})

            routing = await researcher.route_query(
                question=question,
                index_content=index_content,
                provider=provider,
                on_status=on_status
            )

            # Step 2.5: Share reasoning
            if routing.reasoning:
                await websocket.send_json({"status": f"💡 Decision: {routing.reasoning}"})

            # Step 3: Fetch files (with fallback)
            files_to_read = routing.files_to_read
            
            if not files_to_read:
                await websocket.send_json({"status": "⚠️ Navigator was unsure; retrieving all wiki contents for safety..."})
                files_to_read = re.findall(r'wiki/[\w\-]+\.md', index_content)
                files_to_read = [f for f in files_to_read if f != "wiki/index.md"]

            filenames_str = ", ".join([f"`{f}`" for f in files_to_read])
            await websocket.send_json({"status": f"📑 Retrieving latest content for: {filenames_str}..."})
            
            fetched_map = github_service.fetch_files(files_to_read)
            fetched_contents = {
                path: repo_file.content for path, repo_file in fetched_map.items()
            }

            # Step 4: Answer
            # Custom status for synthesis
            await websocket.send_json({"status": "📖 Synthesizing answer from retrieved documents..."})
            result = await researcher.answer_query(
                question=question,
                fetched_files=fetched_contents,
                provider=provider
            )

            # 5. Send final answer
            await websocket.send_json({
                "status": "✨ Query Complete",
                "answer": result.model_dump()
            })

    except WebSocketDisconnect:
        logger.info("WebSocket /ws/query disconnected.")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.send_json({"error": str(e)})
        except:
            pass
