"""Tests for core/compliance_analysis.py orchestration."""

import compliance_analysis


def test_compliance_analysis_uses_compliance_fetcher(cfg):
    cfg.save_config({"github_token": "ghp_test"})
    analysis = compliance_analysis.ComplianceAnalysis("acme/app")
    calls = []

    class FakeEngine:
        def _fetch_repo_content_for_compliance(self, repo, token):
            calls.append((repo, token))
            return ["src/main.py"], {"src/main.py": "pass"}

    analysis._engine = FakeEngine()

    tree, files = analysis._fetch_repo_content()

    assert calls == [("acme/app", "ghp_test")]
    assert tree == ["src/main.py"]
    assert files == {"src/main.py": "pass"}
