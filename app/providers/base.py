import copy
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CompletionResult:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class ProviderError(Exception):
    """Base class for provider failures."""


class RetryableProviderError(ProviderError):
    """Transient failure (429/5xx/timeout); retried with exponential backoff."""


class NonRetryableProviderError(ProviderError):
    """Permanent failure (4xx/auth/config); retrying is pointless."""


class Provider(Protocol):
    async def complete(self, prompt: str, model_id: str) -> CompletionResult: ...


class BaseProvider(Provider, ABC):
    base_url: str | None = None

    @abstractmethod
    async def complete(self, prompt: str, model_id: str) -> CompletionResult: ...

    def with_base_url(self, base_url: str) -> "BaseProvider":
        """Return a copy of this provider pointed at a specific base URL."""
        clone = copy.copy(self)
        clone.base_url = base_url
        return clone
