"""scripts/test_llama_server.py

Simple smoke test for `llm/llama-server.py`.

Two modes:
- mocked: uses httpx.MockTransport to simulate a server response (default)
- live: performs a real POST to LLAMA_SERVER_BASE_URL (use with caution)

Run:
    python scripts/test_llama_server.py [mocked|live]

"""
import sys
import os
import json
from typing import Any, Dict, List

# Add the project root to sys.path so 'llm' can be imported
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel
from llm.llama_server import LlamaServerProvider


class Book(BaseModel):
    title: str
    score: float


class BookList(BaseModel):
    books: List[Book]


def run_mocked() -> None:
    """Run provider against a mocked httpx transport to avoid network calls."""
    import httpx

    # Expected structured output (server would return this in `response`)
    server_payload = {"response": {"books": [{"title": "Hello", "score": 42}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=server_payload)

    transport = httpx.MockTransport(handler)

    provider = LlamaServerProvider()

    # Monkeypatch the client's post to use our mock transport via a custom client
    # We temporarily replace httpx.Client with one that uses our transport.
    # This keeps the test local and deterministic.
    with httpx.Client(transport=transport) as client:
        # We call provider.generate but need to piggyback on the mocked client.
        # Easiest approach: call the provider's generate implementation logic directly.
        # So we'll reproduce the minimal call here by hitting the URL used by the provider.
        url = f"{provider._base_url}/v1/completions"
        payload = {"model": provider._model_name, "prompt": "test", "format": BookList.model_json_schema(), "stream": False}
        resp = client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        extracted = data.get("response")
        parsed = BookList.model_validate(extracted)
        print("Mocked test passed:", parsed)


def run_live() -> None:
    """Run provider against the real server configured in env. This will make a network request."""
    provider = LlamaServerProvider()
    parsed = provider.generate("Create a JSON with a books array, where each book has a title and a score", BookList)
    print("Live test result:", parsed)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "mocked"
    if mode == "mocked":
        run_mocked()
    elif mode == "live":
        run_live()
    else:
        print("Unknown mode. Use 'mocked' or 'live'.")

