"""Abstract base class for AI model providers."""

from abc import ABC, abstractmethod
from typing import Any, Dict


class ModelProviderError(Exception):
    """Base exception for AI model provider failures."""
    pass


class ModelTimeoutError(ModelProviderError):
    """Raised when an AI model call exceeds the bounded timeout."""
    pass


class ModelUnavailableError(ModelProviderError):
    """Raised when an AI provider returns 500/503 or network connectivity fails."""
    pass


class ModelProvider(ABC):
    """Provider interface decoupling the orchestrator from concrete LLM SDKs."""

    @abstractmethod
    async def generate_recommendation(
        self,
        prompt: str,
        system_prompt: str,
        context: Dict[str, Any],
    ) -> str:
        """Generate raw JSON recommendation string from the model."""
        pass
