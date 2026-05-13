"""
llm/ollama.py — Ollama LLM Provider implementation.
"""

import json
import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel

from config import settings
from .base import LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LlamaServerProvider(LLMProvider):
    def __init__(self) -> None:
        # Use the dedicated llama server base URL (falls back to ollama URL by default)
        self._base_url = settings.llama_server_base_url
        self._model_name = settings.llm_model or "Qwen3-Coder-Next-MXFP4_MOE"
        logger.info(f"Initialized LlamaServerProvider using model: {self._model_name} at {self._base_url}")

    def _extract_text(self, data: dict) -> str:
        """Attempt to extract the model's structured JSON output from known response shapes.

        Returns the JSON/string that should be passed to pydantic for validation.
        """
        # Common shapes: {"response": <json|string>}
        if isinstance(data, dict):
            if "response" in data:
                return data["response"]
            if "text" in data:
                return data["text"]
            # OpenAI-like: {"choices": [{"text": "..."}]}
            choices = data.get("choices")
            if isinstance(choices, list) and choices:
                first = choices[0]
                if isinstance(first, dict) and "text" in first:
                    return first["text"]
            # Some servers return {'completion': '...'} or {'output_text': '...'}
            for key in ("completion", "output_text", "result"):
                if key in data:
                    return data[key]

        # Fallback: return entire payload as JSON string
        return json.dumps(data)

    def generate(self, prompt: str, schema: type[T]) -> T:
        url = f"{self._base_url}/v1/completions"
        schema_json = schema.model_json_schema()

        payload = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "format": schema_json,
            "options": {"temperature": 0.2},
        }

        logger.debug(f"LlamaServerProvider POSTing to {url}...")

        with httpx.Client(timeout=600.0) as client:
            response = client.post(url, json=payload)
            try:
                response.raise_for_status()
            except Exception:
                logger.exception("Llama server request failed: %s", response.text)
                raise

            try:
                data = response.json()
            except Exception:
                # Not JSON — treat as raw text
                raw = response.text
                logger.debug("Llama server returned non-JSON response; trying to validate raw text")
                return schema.model_validate_json(raw)

            extracted = self._extract_text(data)

            # If extracted is a dict, validate directly; if it's a string, assume JSON string
            try:
                if isinstance(extracted, dict):
                    return schema.model_validate(extracted)
                if isinstance(extracted, str):
                    return schema.model_validate_json(extracted)
                # Otherwise, fallback to converting to JSON string
                return schema.model_validate_json(json.dumps(extracted))
            except Exception:
                logger.exception("Failed to validate Llama server response against schema. Full response: %s", data)
                raise

