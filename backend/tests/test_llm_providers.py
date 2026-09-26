"""Tests for the OpenAI-compatible LLM provider and error taxonomy."""

import httpx
import pytest

from app.db.models import Settings
from app.llm.errors import (
    AuthenticationError,
    InferenceTimeoutError,
    LLMError,
    ModelNotLoadedError,
    ProviderConfigError,
    ProviderUnavailableError,
    ResourceExhaustedError,
)
from app.llm.providers import CustomProvider
from app.llm.router import build_provider, generate


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _provider(handler=None, **kwargs) -> CustomProvider:
    defaults = {"api_key": "k", "model": "some-model", "base_url": "https://example.com/v1"}
    defaults.update(kwargs)
    if handler is not None:
        defaults["http_client"] = _mock_client(handler)
    return CustomProvider(**defaults)


class TestGeneration:
    async def test_successful_generation(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "hello"}}]}
            )

        result = await _provider(handler).generate("say hi")
        assert result.text == "hello"
        assert result.provider == "custom"
        assert result.model == "some-model"
        assert result.error is None

    async def test_request_shape(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            seen["body"] = request.content
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        await _provider(handler, base_url="https://api.deepseek.com/v1/").generate("say hi")
        assert seen["url"] == "https://api.deepseek.com/v1/chat/completions"  # trailing slash normalized
        assert seen["auth"] == "Bearer k"

    async def test_pasted_completions_url_is_normalized(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "ok"}}]}
            )

        provider = _provider(handler, base_url="https://openrouter.ai/api/v1/chat/completions")
        assert provider.base_url == "https://openrouter.ai/api/v1"
        await provider.generate("hi")
        assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"


class TestErrorTaxonomy:
    async def test_401_maps_to_auth_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "bad key"}})

        with pytest.raises(AuthenticationError):
            await _provider(handler).generate("say hi")

    async def test_404_maps_to_model_not_loaded(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"error": "model not found"})

        with pytest.raises(ModelNotLoadedError) as exc_info:
            await _provider(handler).generate("say hi")
        assert "Settings" in exc_info.value.hint

    async def test_429_maps_to_resource_exhausted(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limited"})

        with pytest.raises(ResourceExhaustedError):
            await _provider(handler).generate("say hi")

    async def test_timeout_maps_to_inference_timeout_with_hint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        with pytest.raises(InferenceTimeoutError) as exc_info:
            await _provider(handler).generate("say hi")
        assert exc_info.value.hint

    async def test_connection_failure_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(ProviderUnavailableError) as exc_info:
            await _provider(handler).generate("say hi")
        assert "base URL" in exc_info.value.hint

    async def test_server_error_maps_to_unavailable(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="internal error")

        with pytest.raises(LLMError):
            await _provider(handler).generate("say hi")


class TestValidation:
    def test_missing_base_url_rejected(self):
        with pytest.raises(ProviderConfigError) as exc_info:
            CustomProvider(api_key="k", model="m").validate()
        assert "base URL" in str(exc_info.value)

    def test_missing_model_rejected(self):
        with pytest.raises(ProviderConfigError) as exc_info:
            CustomProvider(api_key="k", base_url="http://local.host/v1").validate()
        assert "model name" in str(exc_info.value)

    def test_missing_key_rejected(self):
        with pytest.raises(ProviderConfigError):
            CustomProvider(model="m", base_url="http://local.host/v1").validate()


class TestRouter:
    def test_build_provider_from_settings(self):
        settings = Settings(
            custom_api_key="k",
            custom_base_url="https://api.deepseek.com/v1",
            cloud_model="deepseek-chat",
        )
        provider = build_provider(settings)
        assert isinstance(provider, CustomProvider)
        assert provider.base_url == "https://api.deepseek.com/v1"
        assert provider.model == "deepseek-chat"
        assert provider.api_key == "k"

    async def test_generate_returns_graceful_error_on_failure(self):
        settings = Settings(custom_api_key="k", custom_base_url="http://127.0.0.1:1/v1", cloud_model="m")

        async def fake_get_settings() -> Settings:
            return settings

        import app.llm.router as router_mod

        original = router_mod.get_settings
        router_mod.get_settings = fake_get_settings
        try:
            result = await generate("say hi")
        finally:
            router_mod.get_settings = original

        assert result.error is not None
        assert result.provider == "custom_error"
        assert result.hint
