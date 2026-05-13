"""
llm/__init__.py — LLM Provider abstraction layer
"""

from .base import LLMProvider
from .factory import get_provider

__all__ = ["LLMProvider", "get_provider"]
