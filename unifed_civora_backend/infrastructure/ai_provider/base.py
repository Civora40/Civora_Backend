"""Base interface for LLM providers (Phase 9)."""
from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAIProvider(ABC):
    @abstractmethod
    def generate_structured_output(self, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Generate validated structured output from an LLM."""
        raise NotImplementedError

