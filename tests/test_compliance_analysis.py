"""Tests for core/compliance_analysis.py."""

import compliance_analysis


def test_health_counts_assessments_without_loading_history(monkeypatch):
    monkeypatch.setattr(
        compliance_analysis.ComplianceAnalysis,
        "_fetch_repo_content",
        lambda self: ([], {"src/api.py": "require_user audit_log https"}),
    )
    monkeypatch.setattr(
        compliance_analysis.config_store,
        "get_compliance_policy",
        lambda repo: {"enabled": True},
    )

    def list_assessments(repo=None, limit=20):
        assert repo == "acme/app"
        assert limit == 1
        return [{"created_at": "2026-10-14T00:00:00Z"}]

    monkeypatch.setattr(compliance_analysis.assessment_store, "list_assessments", list_assessments)
    monkeypatch.setattr(compliance_analysis.assessment_store, "count_assessments", lambda repo=None: 12345)

    result = compliance_analysis.get_health("acme/app")

    assert result["freshness"]["latest_assessment_at"] == "2026-10-14T00:00:00Z"
    assert result["freshness"]["assessment_count"] == 12345
