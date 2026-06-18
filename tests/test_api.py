"""Integration tests for backend/main.py via FastAPI TestClient."""

import hmac
import hashlib

import config_store
from review_engine import ReviewResult


def test_health(client):
    tc, _ = client
    assert tc.get("/api/health").json()["status"] == "ok"


# ----- settings ----- #

def test_settings_default(client):
    tc, _ = client
    body = tc.get("/api/settings").json()
    assert body["repos"] == []
    assert body["github_token_set"] is False
    assert body["webhook_secret_set"] is False
    assert body["openrouter_model"] == config_store.DEFAULT_MODEL


def test_settings_put_updates_and_hides_secrets(client):
    tc, _ = client
    resp = tc.put("/api/settings", json={
        "github_token": "ghp_secret", "webhook_secret": "whs",
        "repos": ["org/a"], "openrouter_model": "openai/gpt-4o",
        "openrouter_provider": "Azure",
    })
    body = resp.json()
    assert body["github_token_set"] is True
    assert body["openrouter_model"] == "openai/gpt-4o"
    assert body["openrouter_provider"] == "Azure"
    # secrets must never be echoed back, in any field
    raw = tc.get("/api/settings").text
    assert "ghp_secret" not in raw and "whs" not in raw


def test_settings_put_partial_keeps_other_fields(client):
    tc, _ = client
    tc.put("/api/settings", json={"github_token": "t", "webhook_secret": "s"})
    tc.put("/api/settings", json={"openrouter_model": "m/x"})
    body = tc.get("/api/settings").json()
    assert body["github_token_set"] is True
    assert body["webhook_secret_set"] is True
    assert body["openrouter_model"] == "m/x"


# ----- repos ----- #

def test_repo_add_remove(client):
    tc, _ = client
    assert tc.post("/api/repos", json={"repo": "org/a"}).json()["repos"] == ["org/a"]
    assert tc.post("/api/repos", json={"repo": "org/b"}).json()["repos"] == ["org/a", "org/b"]
    assert tc.delete("/api/repos", params={"repo": "org/a"}).json()["repos"] == ["org/b"]
    assert tc.get("/api/repos").json()["repos"] == ["org/b"]


def test_repo_add_validates_form(client):
    tc, _ = client
    assert tc.post("/api/repos", json={"repo": "noslash"}).status_code == 400


# ----- decisions + scoping ----- #

def _add_decision(tc, ref, summary, repo):
    return tc.post("/api/decisions", json={
        "ref": ref, "summary": summary, "reasoning": "r",
        "outcome": "approved_and_merged", "metadata": {"repo": repo},
    })


def test_decisions_add_list_delete(client):
    tc, _ = client
    r = _add_decision(tc, "PR #1", "auth", "org/a").json()
    doc_id = r["doc_id"]
    assert tc.get("/api/decisions").json()["count"] == 1
    tc.delete(f"/api/decisions/{doc_id}")
    assert tc.get("/api/decisions").json()["count"] == 0


def test_decisions_repo_and_global_filter(client):
    tc, _ = client
    _add_decision(tc, "PR #1", "auth a", "org/a")
    _add_decision(tc, "PR #2", "web b", "org/b")
    _add_decision(tc, "ADR-1", "policy", "*")
    assert tc.get("/api/decisions").json()["count"] == 3
    only_a = tc.get("/api/decisions", params={"repo": "org/a"}).json()
    assert [d["ref"] for d in only_a["decisions"]] == ["PR #1"]
    only_global = tc.get("/api/decisions", params={"repo": "*"}).json()
    assert [d["ref"] for d in only_global["decisions"]] == ["ADR-1"]


def test_decisions_search(client):
    tc, _ = client
    _add_decision(tc, "PR #1", "authentication middleware", "org/a")
    res = tc.post("/api/decisions/search", json={"query": "auth", "k": 5}).json()
    assert res["count"] == 1


# ----- stats & balance ----- #

def test_stats_shape(client):
    tc, _ = client
    s = tc.get("/api/stats").json()
    assert set(s) >= {"decisions_sampled", "backend", "model", "provider",
                      "api_key_configured", "github_token_configured"}


def test_balance_unconfigured(client):
    tc, _ = client  # clean_env clears OPENROUTER_API_KEY
    assert tc.get("/api/balance").json() == {"configured": False}


# ----- review ----- #

def test_review_requires_api_key(client):
    tc, _ = client
    resp = tc.post("/api/review", json={"pr_number": 1, "repo": "org/a", "title": "t", "diff": "+x"})
    assert resp.status_code == 400


def test_review_returns_engine_result(client, monkeypatch):
    tc, main = client
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")

    class FakeEngine:
        def review(self, req):
            return ReviewResult(pr_number=req.pr_number, summary="ok", approved=True,
                                confidence=0.8, issues=[], suggestions=[], past_decisions_applied=[])

    monkeypatch.setattr(main, "get_engine", lambda: FakeEngine())
    resp = tc.post("/api/review", json={"pr_number": 7, "repo": "org/a", "title": "t", "diff": "+x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["pr_number"] == 7 and body["approved"] is True and body["confidence"] == 0.8


# ----- backfill ----- #

def test_backfill_requires_token(client):
    tc, _ = client
    assert tc.post("/api/backfill", json={"repo": "org/a", "pages": 1}).status_code == 400


def test_backfill_invokes_importer(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    monkeypatch.setattr(main, "run_backfill", lambda repo, pages, token, store: 5)
    resp = tc.post("/api/backfill", json={"repo": "org/a", "pages": 2})
    assert resp.json() == {"repo": "org/a", "imported": 5}


# ----- webhook ----- #

def _sign(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


def test_webhook_rejects_bad_signature(client):
    tc, _ = client
    config_store.save_config({"webhook_secret": "s"})
    resp = tc.post("/webhook/github", content=b"{}",
                   headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=bad"})
    assert resp.status_code == 401


def test_webhook_ping_with_valid_signature(client):
    tc, _ = client
    config_store.save_config({"webhook_secret": "s"})
    body = b"{}"
    resp = tc.post("/webhook/github", content=body, headers={
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": _sign(b"s", body),
    })
    assert resp.status_code == 200 and resp.json() == {"status": "pong"}


def test_webhook_rejects_when_no_secret_configured(client):
    tc, _ = client  # no secret set anywhere
    resp = tc.post("/webhook/github", content=b"{}",
                   headers={"X-GitHub-Event": "ping", "X-Hub-Signature-256": "sha256=whatever"})
    assert resp.status_code == 401
