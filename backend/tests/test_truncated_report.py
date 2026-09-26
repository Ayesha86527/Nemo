"""Regression: truncated market-intel JSON must render as a dashboard, not a JSON wall."""

from app.market.engine import parse_report

TRUNCATED = '''{ "match_score": 68, "summary": "Ayesha shows strong LLM pipeline experience but lacks governance artifacts.", "skill_gaps": [ { "skill": "Model monitoring & governance", "status": "missing", "note": "No evidence in CV" }, { "skill": "Kubernetes", "status": "missing", "note": "Docker present, no orchestration" }, { "skill": "MLOps versioning", "status": "partial", "note": "CI/CD shown, no MLflow" } ], "market_signals": [ "Model monitoring required in most postings", "Governance expertise emphasized" ], "recommendations": [ "Publish model cards for a deployed LLM", "Learn Kubernetes orchestration", "Add MLflow versioning to pipelines"'''


class TestTruncatedReport:
    def test_truncated_json_is_repaired_not_dumped(self):
        report = parse_report(TRUNCATED)
        assert report["match_score"] == 68
        assert len(report["skill_gaps"]) == 3
        assert {g["status"] for g in report["skill_gaps"]} == {"missing", "partial"}
        assert len(report["market_signals"]) == 2
        assert len(report["recommendations"]) == 3
        assert not report["summary"].startswith("{")

    def test_cut_mid_string_still_recovers_most(self):
        report = parse_report(TRUNCATED[: len(TRUNCATED) - 40])
        assert report["match_score"] == 68
        assert report["skill_gaps"]
        assert not report["summary"].startswith("{")

    def test_total_garbage_gives_friendly_summary(self):
        report = parse_report("no json here at all")
        assert "could not be structured" in report["summary"]
        assert not report["summary"].startswith("{")
