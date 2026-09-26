"""Tests that manual model configuration exposes no live model catalog route."""

from app.routers.llm import router


class TestModelListingRemoved:
    def test_router_has_no_live_model_catalog(self):
        assert all(route.path != "/api/llm/models" for route in router.routes)
