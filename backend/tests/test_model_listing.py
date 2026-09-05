"""Tests for live model listing on the OpenAI-compatible provider."""

import httpx
import pytest

from app.llm.errors import ProviderConfigError
from app.llm.providers import CustomProvider


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestModelListing:
    async def test_lists_models_from_the_catalog(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert str(request.url).endswith("/models")
            return httpx.Response(
                200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-reasoner"}]}
            )

        provider = CustomProvider(api_key="k", base_url="http://local.host/v1", http_client=_mock_client(handler))
        models = await provider.list_models()
        assert [m.id for m in models] == ["deepseek-chat", "deepseek-reasoner"]

    async def test_filters_non_chat_models(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "openai/gpt-oss-120b"},
                        {"id": "qwen/qwen3.8-27b"},
                        {"id": "whisper-large-v3"},
                        {"id": "meta-llama/llama-prompt-guard-2-86m"},
                    ]
                },
            )

        provider = CustomProvider(api_key="k", base_url="http://local.host/v1", http_client=_mock_client(handler))
        models = await provider.list_models()
        ids = [m.id for m in models]
        assert "openai/gpt-oss-120b" in ids
        assert "qwen/qwen3.8-27b" in ids
        assert "whisper-large-v3" not in ids
        assert "meta-llama/llama-prompt-guard-2-86m" not in ids

    async def test_listing_without_key_raises_config_error(self):
        provider = CustomProvider(api_key="", base_url="http://local.host/v1")
        with pytest.raises(ProviderConfigError):
            await provider.list_models()

    async def test_listing_without_base_url_raises_config_error(self):
        provider = CustomProvider(api_key="k")
        with pytest.raises(ProviderConfigError):
            await provider.list_models()
