import time
from typing import Any

import httpx

from app.providers.base import (
    BaseProvider,
    CompletionResult,
    NonRetryableProviderError,
    RetryableProviderError,
)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class AnthropicProvider(BaseProvider):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anthropic.com",
        timeout_seconds: float = 60.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds

    async def complete(self, prompt: str, model_id: str) -> CompletionResult:
        if not self.api_key:
            raise NonRetryableProviderError("ANTHROPIC_API_KEY is not set")
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model_id,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise RetryableProviderError(f"anthropic request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RetryableProviderError(f"anthropic transport error: {exc}") from exc
        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.status_code != 200:
            message = f"anthropic HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code in _RETRYABLE_STATUS:
                raise RetryableProviderError(message)
            raise NonRetryableProviderError(message)
        body = response.json()
        try:
            content = body["content"][0]["text"]
            usage = body["usage"]
            prompt_tokens = int(usage["input_tokens"])
            completion_tokens = int(usage["output_tokens"])
        except (KeyError, IndexError, TypeError) as exc:
            raise RetryableProviderError(f"unexpected anthropic response shape: {exc}") from exc
        return CompletionResult(
            text=str(content),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
        )
