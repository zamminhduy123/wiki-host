"""
llm/gemini.py — Gemini LLM Provider implementation.
"""

import logging
from typing import TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel

from config import settings
from .base import LLMProvider

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class GeminiProvider(LLMProvider):
    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)
        self._model_name = settings.llm_model or "gemini-1.5-pro-latest"
        logger.info(f"Initialized GeminiProvider using model: {self._model_name}")

    def generate(self, prompt: str, schema: type[T]) -> T:
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,
        )
        
        logger.debug(f"GeminiProvider calling {self._model_name}...")
        response = self._client.models.generate_content(
            model=self._model_name,
            contents=prompt,
            config=config,
        )
        text = response.text
        return schema.model_validate_json(text)
