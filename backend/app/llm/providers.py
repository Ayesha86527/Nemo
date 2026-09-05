"""The LLM provider: a single OpenAI-compatible client.

Nemo talks to any OpenAI-compatible chat-completions endpoint (DeepSeek,
Groq, Mistral, Ollama, vLLM, LM Studio…) using plain HTTP (httpx), with
failures classified into the error taxonomy in `app.llm.errors`. The
provider accepts an injectable `http_client` for testing.
"""

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import httpx

from app.llm.errors import (
    AuthenticationError,
    InferenceTimeoutError,
    LLMError,
    ModelNotLoadedError,
    ProviderConfigError,
    ProviderUnavailableError,
    ResourceExhaustedError,
)


@dataclass
class LLMResponse:
    text: str
    provider: str
    model: str
    error: str | None = None
    hint: str | None = None


@dataclass
class ModelInfo:
    id: str
    name: str = ""


# Catalog entries that are not chat/generation models (audio, guards, embeddings)
_NON_CHAT_MARKERS = ("whisper", "tts", "prompt-guard", "embedding", "guard", "safeguard")


def _is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


class LLMProvider(ABC):
    name: str = "base"
    model: str = ""

    def validate(self) -> None:
        """Raise ProviderConfigError if the provider cannot be used."""

    @abstractmethod
    async def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        """Run inference. Raises LLMError subclasses on failure."""

    @abstractmethod
    async def list_models(self) -> list[ModelInfo]:
        """Fetch the provider's currently available models."""


def _classify_http_error(exc: Exception, provider_name: str) -> LLMError:
    if isinstance(exc, httpx.TimeoutException):
        return InferenceTimeoutError(
            f"{provider_name} inference timed out.",
            hint="Try a smaller model in Settings, or check that the endpoint is healthy.",
        )
    if isinstance(exc, httpx.ConnectError):
        return ProviderUnavailableError(
            f"Could not connect to {provider_name}.",
            hint="Check the base URL in Settings and that the service is running.",
        )
    return ProviderUnavailableError(f"{provider_name} request failed: {exc}")


def _raise_for_status(resp: httpx.Response, provider_name: str) -> None:
    if resp.status_code in (401, 403):
        raise AuthenticationError(
            f"{provider_name} rejected the API key.",
            hint="Check the API key in Settings.",
        )
    if resp.status_code == 404:
        raise ModelNotLoadedError(
            f"{provider_name} returned 404 (model or endpoint not found).",
            hint="Verify the model name and base URL in Settings.",
        )
    if resp.status_code == 429:
        raise ResourceExhaustedError(
            f"{provider_name} rate limit exceeded.",
            hint="Nemo already retried automatically — wait a minute and try again.",
        )
    if resp.status_code >= 500:
        raise ProviderUnavailableError(
            f"{provider_name} returned server error {resp.status_code}.",
            hint="The endpoint is having issues — retry later.",
        )
    if resp.status_code >= 400:
        raise LLMError(f"{provider_name} returned HTTP {resp.status_code}: {resp.text[:200]}")


def normalize_base_url(base_url: str) -> str:
    """Trim slashes and an accidental /chat/completions suffix (paste mistake)."""
    return base_url.strip().rstrip("/").removesuffix("/chat/completions").rstrip("/")


MAX_ATTEMPTS = 3
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY = 30.0

_shared_client: httpx.AsyncClient | None = None


def get_shared_client() -> httpx.AsyncClient:
    """One pooled HTTP client for all provider traffic (connection reuse)."""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(timeout=120.0)
    return _shared_client


def retry_delay(attempt: int, resp: httpx.Response | None) -> float:
    """Seconds to wait before retrying: Retry-After if present, else 1s/2s/4s…"""
    if resp is not None:
        raw = (resp.headers.get("retry-after") or "").strip()
        try:
            return max(0.5, min(float(raw), _MAX_RETRY_DELAY))
        except ValueError:
            pass
    return float(min(2**attempt, 8))


class CustomProvider(LLMProvider):
    """Any OpenAI-compatible endpoint (DeepSeek, Groq, Mistral, Ollama, vLLM, LM Studio…)."""

    name = "custom"

    def __init__(
        self,
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        timeout: float = 120.0,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = normalize_base_url(base_url)
        self.timeout = timeout
        self._client = http_client

    def validate(self) -> None:
        if not self.model:
            raise ProviderConfigError(
                "The LLM endpoint needs a model name.",
                hint="Pick a model in Settings (refresh the model list, or type one in).",
            )
        self._validate_connection()

    def _validate_connection(self) -> None:
        if not self.base_url:
            raise ProviderConfigError(
                "The LLM endpoint needs a base URL.",
                hint="Set the endpoint in Settings, e.g. https://api.deepseek.com/v1",
            )
        if not self.api_key:
            raise ProviderConfigError(
                "The LLM endpoint needs an API key.",
                hint="Add your API key in Settings (any placeholder works for local servers that ignore it).",
            )

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """HTTP request with bounded retries on rate limits, 5xx, and transport failures."""
        client = self._client if self._client is not None else get_shared_client()
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await client.request(method, url, **kwargs)
            except Exception as exc:  # transport: timeout, connect, network resets
                if attempt < MAX_ATTEMPTS - 1:
                    await asyncio.sleep(retry_delay(attempt, None))
                    continue
                raise _classify_http_error(exc, "The LLM endpoint") from exc
            if resp.status_code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                return resp
            await asyncio.sleep(retry_delay(attempt, resp))
        raise AssertionError("unreachable")

    async def generate(self, prompt: str, system: str | None = None) -> LLMResponse:
        self.validate()
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._request(
            "POST",
            f"{self.base_url}/chat/completions",
            json={"model": self.model, "messages": messages},
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        _raise_for_status(resp, "The LLM endpoint")
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        return LLMResponse(text=text, provider=self.name, model=self.model)

    async def list_models(self) -> list[ModelInfo]:
        # The model catalog only needs the endpoint + key, not a model name.
        self._validate_connection()
        resp = await self._request(
            "GET",
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=30,
        )
        _raise_for_status(resp, "The LLM endpoint")
        entries = resp.json().get("data") or []
        return [
            ModelInfo(id=m["id"], name=m.get("name") or m["id"])
            for m in entries
            if m.get("id") and _is_chat_model(m["id"])
        ]
