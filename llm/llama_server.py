"""

llm/llama_server.py — LlamaServer LLM Provider implementation.
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
        self._api_key = settings.llama_server_api_key
        self._model_name = settings.llm_model or "Qwen3.6-35B-A3B-UD-Q4_K_M"
        logger.info(f"Initialized LlamaServerProvider using model: {self._model_name} at {self._base_url}")

    def _extract_text(self, data: dict) -> str:
        """Attempt to extract the model's structured JSON output from known response shapes.

        Returns the JSON/string that should be passed to pydantic for validation.
        """
        # Common shapes: {"response": <json|string>}
        text = ""
        if isinstance(data, dict):
            if "response" in data:
                text = data["response"]
            elif "text" in data:
                text = data["text"]
            else:
                # OpenAI-like: {"choices": [{"text": "..."}]}
                choices = data.get("choices")
                if isinstance(choices, list) and choices:
                    first = choices[0]
                    if isinstance(first, dict):
                        if "text" in first:
                            text = first["text"]
                        elif "message" in first and isinstance(first["message"], dict):
                            text = first["message"].get("content", "")

            if not text:
                # Some servers return {'completion': '...'} or {'output_text': '...'}
                for key in ("completion", "output_text", "result"):
                    if key in data:
                        text = data[key]
                        break

        # Fallback: if data is already a string or we couldn't find a key
        if not text:
            text = data if isinstance(data, str) else json.dumps(data)

        # Handle Markdown code blocks (e.g. ```json ... ```)
        if isinstance(text, str):
            text = text.strip()
            if "```" in text:
                # Try to extract content between first ``` and last ```
                lines = text.splitlines()
                start = -1
                for i, line in enumerate(lines):
                    if line.strip().startswith("```"):
                        start = i
                        break
                
                end = -1
                for i in range(len(lines) - 1, start, -1):
                    if lines[i].strip().startswith("```"):
                        end = i
                        break
                
                if start != -1 and end != -1:
                    # Join lines between indices, skipping the ``` lines themselves
                    text = "\n".join(lines[start+1:end]).strip()

            if text:
                try:
                    json.loads(text)
                except json.JSONDecodeError:
                    text = self._extract_json_from_text(text)

        return text

    def _extract_json_from_text(self, text: str) -> str:
        """Try to find and extract a valid JSON object from plain text.
        If multiple JSON objects are found, returns the last one, as the first
        is often a hallucinated schema template like "string".
        """
        # Look for JSON objects (starting with {)
        brace_count = 0
        start_idx = -1
        last_valid_json = None
        
        # Keep scanning the text to find all valid JSON objects, keep the last one.
        for i, char in enumerate(text):
            if char == "{":
                if brace_count == 0:
                    start_idx = i
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0 and start_idx != -1:
                    # Found a complete JSON object candidate
                    candidate = text[start_idx:i+1].strip()
                    try:
                        json.loads(candidate)
                        last_valid_json = candidate
                    except json.JSONDecodeError:
                        pass
                    start_idx = -1
        
        if last_valid_json:
            logger.debug(f"Extracted JSON object from text: {last_valid_json[:100]}...")
            return last_valid_json
            
        # If no valid JSON found, return the original text
        logger.warning(f"Could not extract valid JSON from text: {text[:200]}...")
        return text

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

        logger.info(f"LlamaServerProvider POSTing to {url} with schema: {json.dumps(schema_json)[:200]}...")
        logger.debug(f"FULL PROMPT:\n{prompt}")

        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        with httpx.Client(timeout=600.0) as client:
            response = client.post(url, json=payload, headers=headers)
            try:
                response.raise_for_status()
            except Exception:
                logger.exception("Llama server request failed: %s", response.text)
                raise

            try:
                data = response.json()
                logger.info(f"Raw response from Llama server: {json.dumps(data)[:300]}...")
            except Exception:
                # Not JSON — treat as raw text
                raw = response.text
                logger.warning(f"Llama server returned non-JSON response: {raw[:300]}")
                return schema.model_validate_json(raw)

            extracted = self._extract_text(data)
            logger.info(f"Extracted JSON string before validation: {extracted[:300]}...")

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

