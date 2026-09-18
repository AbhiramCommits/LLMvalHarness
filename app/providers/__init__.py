from functools import lru_cache

from app.config import get_settings
from app.providers.anthropic import AnthropicProvider
from app.providers.base import (
    BaseProvider,
    CompletionResult,
    NonRetryableProviderError,
    Provider,
    ProviderError,
    RetryableProviderError,
)
from app.providers.fake import FakeProvider
from app.providers.openai import OpenAIProvider

__all__ = [
    "AnthropicProvider",
    "BaseProvider",
    "CompletionResult",
    "FakeProvider",
    "NonRetryableProviderError",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "RetryableProviderError",
    "get_provider",
]


@lru_cache
def get_provider() -> BaseProvider:
    """Build the provider selected by the ``EVAL_PROVIDER`` env var."""
    settings = get_settings()
    if settings.eval_provider == "fake":
        return FakeProvider()
    if settings.eval_provider == "openai":
        return OpenAIProvider(
            api_key=settings.openai_api_key or "",
            timeout_seconds=settings.provider_request_timeout_ms / 1000,
        )
    return AnthropicProvider(
        api_key=settings.anthropic_api_key or "",
        timeout_seconds=settings.provider_request_timeout_ms / 1000,
    )
