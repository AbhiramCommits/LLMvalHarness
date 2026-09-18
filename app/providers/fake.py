import hashlib

from app.providers.base import BaseProvider, CompletionResult


class FakeProvider(BaseProvider):
    """Deterministic provider for tests and local dev; requires no API key.

    Outputs (text, token counts, latency) are derived from a hash of
    ``model_id + prompt`` so repeated runs of the same item are stable.
    """

    def __init__(self) -> None:
        self.base_url = None

    async def complete(self, prompt: str, model_id: str) -> CompletionResult:
        digest = hashlib.sha256(f"{model_id}:{prompt}".encode()).digest()
        seed = int.from_bytes(digest[:4], "big")
        return CompletionResult(
            text=f"[fake:{model_id}] {prompt[:100]}",
            prompt_tokens=max(1, len(prompt.split())),
            completion_tokens=10 + seed % 41,
            latency_ms=5 + seed % 16,
        )
