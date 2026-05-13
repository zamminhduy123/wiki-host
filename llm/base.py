"""
llm/base.py — Abstract LLM Provider interface.
"""

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

class LLMProvider(Protocol):
    """Protocol for LLM providers."""
    
    def generate(self, prompt: str, schema: type[T]) -> T:
        """
        Send a prompt to the LLM and return a parsed Pydantic object
        matching the provided schema.
        """
        ...
