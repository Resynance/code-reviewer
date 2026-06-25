"""API tests for the new /api/compliance/* endpoints."""

import pytest


@pytest.fixture
def configured_repo(cfg, client, monkeypatch):
    test_client, main = client
    cfg.save_config({
        "repos": ["acme/app"],
        "github_token": "ghp_test_token",
    })
    return test_client, main, monkeypatch


def test_compliance_dashboard(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis, "get_dashboard",
        lambda repo, history_limit=50: {
            "repo": repo,
            "health": {"score": 90},
            "coverage": {"coverage_score": 75},
            "suggestions": [],
        },
    )

    resp = test_client.get("/api/compliance/dashboard?repo=acme/app")
    assert resp.status_code == 200
    data = resp.json()
    assert data["repo"] == "acme/app"
    assert data["health"]["score"] == 90


def test_compliance_health(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(main.compliance_analysis, "get_health", lambda repo: {"score": 88})

    resp = test_client.get("/api/compliance/health?repo=acme/app")
    assert resp.status_code == 200
    assert resp.json()["score"] == 88


def test_compliance_coverage(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(main.compliance_analysis, "get_coverage", lambda repo, limit=50: {"coverage_score": 60})

    resp = test_client.get("/api/compliance/coverage?repo=acme/app")
    assert resp.status_code == 200
    assert resp.json()["coverage_score"] == 60


def test_compliance_suggestions(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis, "get_suggestions",
        lambda repo: [{"id": "s1", "type": "enable_compliance"}],
    )

    resp = test_client.get("/api/compliance/suggestions?repo=acme/app")
    assert resp.status_code == 200
    assert resp.json()["suggestions"][0]["id"] == "s1"


def test_apply_compliance_suggestion(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis, "apply_suggestion",
        lambda repo, suggestion: {"default": {}, "repos": {repo: {"enabled": True}}},
    )

    resp = test_client.post(
        "/api/compliance/suggestions/apply",
        json={"repo": "acme/app", "suggestion": {"type": "enable_compliance"}},
    )
    assert resp.status_code == 200
    assert "compliance_policies" in resp.json()


def test_compliance_endpoints_require_repo(configured_repo):
    test_client, _, _ = configured_repo
    resp = test_client.get("/api/compliance/dashboard")
    assert resp.status_code == 422


def test_analyze_compliance_persists_result(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis, "get_dashboard",
        lambda repo, history_limit=50: {
            "repo": repo,
            "health": {"score": 92},
            "coverage": {"coverage_score": 80},
            "suggestions": [],
        },
    )
    monkeypatch.setattr(
        main.compliance_analysis_store, "save_analysis",
        lambda record: {**record, "id": 1, "created_at": "2024-01-01T00:00:00Z"},
    )

    resp = test_client.post("/api/compliance/analyze", json={"repo": "acme/app"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 1
    assert data["health"]["score"] == 92


def test_list_compliance_analyses(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis_store, "list_analyses",
        lambda repo=None, limit=20: [
            {"id": 1, "repo": "acme/app", "health": {}, "coverage": {}, "suggestions": [], "created_at": "2024-01-01T00:00:00Z"},
        ],
    )

    resp = test_client.get("/api/compliance/analyses?repo=acme/app")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert data["analyses"][0]["id"] == 1


def test_get_compliance_analysis(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85},
            "coverage": {},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )

    resp = test_client.get("/api/compliance/analyses/42")
    assert resp.status_code == 200
    assert resp.json()["health"]["score"] == 85


def test_get_missing_compliance_analysis_returns_404(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(main.compliance_analysis_store, "get_analysis", lambda analysis_id: None)

    resp = test_client.get("/api/compliance/analyses/999")
    assert resp.status_code == 404


def test_reanalyze_compliance(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {},
            "coverage": {},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        main.compliance_analysis, "get_dashboard",
        lambda repo, history_limit=50: {
            "repo": repo,
            "health": {"score": 95},
            "coverage": {},
            "suggestions": [],
        },
    )
    monkeypatch.setattr(
        main.compliance_analysis_store, "save_analysis",
        lambda record: {**record, "id": 2, "created_at": "2024-01-02T00:00:00Z"},
    )

    resp = test_client.post("/api/compliance/analyses/1/reanalyze")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == 2
    assert data["health"]["score"] == 95


def test_create_compliance_issue(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85, "findings": [{"severity": "high", "title": "Missing encryption", "recommendation": "Add TLS"}]},
            "coverage": {"coverage_score": 70, "blind_spots": [{"severity": "medium", "category": "audit_trail", "suggestion": "Add audit logging"}]},
            "suggestions": [{"severity": "low", "reason": "Add vendor to approved list"}],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    seen = {}

    def fake_create_issue(repo, title, body, token):
        seen["repo"] = repo
        seen["title"] = title
        seen["body"] = body
        return "https://github.com/acme/app/issues/12"

    monkeypatch.setattr(main, "create_issue", fake_create_issue)

    resp = test_client.post("/api/compliance/analyses/12/issue", json={})
    assert resp.status_code == 200
    assert resp.json()["html_url"].endswith("/issues/12")
    assert seen["repo"] == "acme/app"
    assert "Compliance follow-up for acme/app" in seen["title"]
    assert "Policy Health Findings" in seen["body"]


def test_create_compliance_issue_enqueues_local_followup(configured_repo):
    test_client, main, monkeypatch = configured_repo
    test_client.put("/api/settings", json={
        "llm_execution_mode": "local_queue",
        "llm_worker_secret": "worker-secret",
        "local_agentic_targets": [{"id": "codex", "label": "Codex", "enabled": True, "command": ["codex", "exec", "-"]}],
    })
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85},
            "coverage": {"coverage_score": 70},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(main, "create_issue", lambda repo, title, body, token: "https://github.com/acme/app/issues/12")
    monkeypatch.setattr(
        main.review_jobs, "create_job",
        lambda request, job_type="review", executor="inline": {"id": "job-1", "status": "queued", "job_type": job_type, "request": request, "executor": executor},
    )

    resp = test_client.post("/api/compliance/analyses/12/issue", json={"agentic_target": "codex"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == "job-1"
    assert body["job_status"] == "queued"


def test_create_compliance_issue_missing_analysis_returns_404(configured_repo):
    test_client, main, monkeypatch = configured_repo
    monkeypatch.setattr(main.compliance_analysis_store, "get_analysis", lambda analysis_id: None)

    resp = test_client.post("/api/compliance/analyses/99/issue", json={})
    assert resp.status_code == 404


def test_create_compliance_issue_rejects_agentic_target_in_inline_mode(configured_repo):
    test_client, main, monkeypatch = configured_repo
    test_client.put("/api/settings", json={"llm_execution_mode": "inline"})
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85},
            "coverage": {"coverage_score": 70},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )

    resp = test_client.post("/api/compliance/analyses/12/issue", json={"agentic_target": "codex"})
    assert resp.status_code == 400
    assert "local_queue" in resp.json()["detail"]


def test_create_compliance_issue_rejects_disabled_agentic_target(configured_repo):
    test_client, main, monkeypatch = configured_repo
    test_client.put("/api/settings", json={
        "llm_execution_mode": "local_queue",
        "llm_worker_secret": "worker-secret",
        "local_agentic_targets": [{"id": "codex", "label": "Codex", "enabled": False, "command": ["codex"]}],
    })
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85},
            "coverage": {"coverage_score": 70},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )

    resp = test_client.post("/api/compliance/analyses/12/issue", json={"agentic_target": "codex"})
    assert resp.status_code == 400
    assert "not enabled" in resp.json()["detail"]


def test_create_compliance_issue_rejects_agentic_followup_without_worker_secret(configured_repo):
    test_client, main, monkeypatch = configured_repo
    test_client.put("/api/settings", json={
        "llm_execution_mode": "local_queue",
        "local_agentic_targets": [{"id": "codex", "label": "Codex", "enabled": True, "command": ["codex", "exec", "-"]}],
    })
    monkeypatch.setattr(
        main.compliance_analysis_store, "get_analysis",
        lambda analysis_id: {
            "id": analysis_id,
            "repo": "acme/app",
            "health": {"score": 85},
            "coverage": {"coverage_score": 70},
            "suggestions": [],
            "created_at": "2024-01-01T00:00:00Z",
        },
    )
    monkeypatch.setattr(
        main, "create_issue",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("issue should not be created")),
    )

    resp = test_client.post("/api/compliance/analyses/12/issue", json={"agentic_target": "codex"})
    assert resp.status_code == 400
    assert "worker secret" in resp.json()["detail"].lower()
