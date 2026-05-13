"""
llm/factory.py — Instantiates the correct LLMProvider.
"""

from config import settings
from .base import LLMProvider
from .gemini import GeminiProvider
from .ollama import OllamaProvider
from .llama_server import LlamaServerProvider


def get_provider() -> LLMProvider:
    if settings.llm_provider == "ollama":
        return OllamaProvider()
    elif settings.llm_provider == "llama":
        return LlamaServerProvider()
    return GeminiProvider()
