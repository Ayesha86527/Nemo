"""Static regression checks for the generated-output UI contract.

The renderer has no browser-test harness yet, so these checks keep its critical
manual-model and output-hierarchy requirements visible in the backend suite.
"""

from pathlib import Path


FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "renderer"


class TestFrontendOutputContract:
    def test_settings_uses_manual_model_entry_without_a_catalog(self):
        source = (FRONTEND / "views" / "Settings.tsx").read_text(encoding="utf-8")
        client = (FRONTEND / "api" / "client.ts").read_text(encoding="utf-8")

        assert "Refresh model list" not in source
        assert "model-catalog" not in source
        assert "listModels" not in source
        assert "listModels" not in client
        assert "Enter the exact model name" in source

    def test_roadmap_exposes_focus_and_preferences_to_personalize_the_request(self):
        roadmap = (FRONTEND / "views" / "Roadmap.tsx").read_text(encoding="utf-8")
        client = (FRONTEND / "api" / "client.ts").read_text(encoding="utf-8")

        # Focus and preferences flow through the agent conversation, so the
        # client still sends them with the request and the view surfaces what
        # was applied instead of offering manual inputs.
        assert "focus?: string" in client and "preferences?: string" in client
        assert "roadmap.focus" in roadmap and "roadmap.preferences" in roadmap

    def test_generated_outputs_use_labeled_summary_evidence_and_action_sections(self):
        roadmap = (FRONTEND / "views" / "Roadmap.tsx").read_text(encoding="utf-8")
        market = (FRONTEND / "views" / "Market.tsx").read_text(encoding="utf-8")
        dashboard = (FRONTEND / "views" / "Dashboard.tsx").read_text(encoding="utf-8")

        assert "Outcome & context" in roadmap
        assert "Context & summary" in market
        assert "Evidence" in market and "Next actions" in market
        assert "Candidate match" in dashboard and "Company and role research" in dashboard
        assert all("output-section" in source for source in (roadmap, market, dashboard))
