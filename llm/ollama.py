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


class OllamaProvider(LLMProvider):
    def __init__(self) -> None:
        self._base_url = settings.ollama_base_url
        self._model_name = settings.llm_model or "llama3"
        logger.info(f"Initialized OllamaProvider using model: {self._model_name} at {self._base_url}")

    def generate(self, prompt: str, schema: type[T]) -> T:
        url = f"{self._base_url}/api/generate"
        schema_json = schema.model_json_schema()
        
        payload = {
            "model": self._model_name,
            "prompt": prompt,
            "stream": False,
            "format": schema_json,
            "options": {
                "temperature": 0.2
            }
        }
        
        logger.debug(f"OllamaProvider POSTing to {url}...")
        
        with httpx.Client(timeout=600.0) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            
            # The structured JSON response is in data['response']
            text = data["response"]
            return schema.model_validate_json(text)
