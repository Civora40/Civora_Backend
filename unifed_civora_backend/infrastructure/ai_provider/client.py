"""Concrete AI provider client placeholder (configured in Phase 9)."""
from typing import Any, Dict
from .base import BaseAIProvider


class DefaultAIProvider(BaseAIProvider):
    def generate_structured_output(self, prompt: str, schema: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError("AI provider integration is scheduled for Phase 9.")

