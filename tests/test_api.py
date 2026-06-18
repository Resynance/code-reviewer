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


def test_pr_comment_requires_token(client):
    tc, _ = client
    assert tc.post("/api/pr-comment", json={"repo": "org/a", "pr_number": 5, "body": "hi"}).status_code == 400


def test_pr_comment_posts(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    monkeypatch.setattr(main, "post_pr_comment",
                        lambda repo, pr_number, body, token: "https://github.com/org/a/pull/5#c1")
    res = tc.post("/api/pr-comment", json={"repo": "org/a", "pr_number": 5, "body": "hi"}).json()
    assert res["html_url"].endswith("#c1")


def test_review_is_saved_to_history(client, monkeypatch):
    tc, main = client
    monkeypatch.setenv("OPENROUTER_API_KEY", "key")

    class FakeEngine:
        def review(self, req):
            return ReviewResult(pr_number=req.pr_number, summary="ok", approved=True,
                                confidence=0.9, issues=[], suggestions=[], past_decisions_applied=[])

    monkeypatch.setattr(main, "get_engine", lambda: FakeEngine())
    assert tc.get("/api/reviews").json()["count"] == 0
    tc.post("/api/review", json={"pr_number": 7, "repo": "org/a", "title": "t", "diff": "+x"})
    hist = tc.get("/api/reviews").json()
    assert hist["count"] == 1
    r = hist["reviews"][0]
    assert r["pr_number"] == 7 and r["repo"] == "org/a" and r["source"] == "api"


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


# ----- open PRs ----- #

def test_github_owners_requires_token(client):
    tc, _ = client
    assert tc.get("/api/github/owners").status_code == 400


def test_github_owners_and_repos(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    monkeypatch.setattr(main, "list_owners", lambda token: [
        {"login": "me", "type": "user"}, {"login": "acme", "type": "org"}])
    monkeypatch.setattr(main, "list_owner_repos",
                        lambda owner, token, owner_type="org": [{"full_name": f"{owner}/r", "private": False}])

    owners = tc.get("/api/github/owners").json()["owners"]
    assert [o["login"] for o in owners] == ["me", "acme"]
    repos = tc.get("/api/github/repos", params={"owner": "acme", "type": "org"}).json()
    assert repos["owner"] == "acme" and repos["repos"][0]["full_name"] == "acme/r"


def test_open_prs_requires_token(client):
    tc, _ = client
    assert tc.get("/api/repos/open-prs", params={"repo": "org/a"}).status_code == 400


def test_open_prs_excludes_already_in_store(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    fake_prs = [
        {"number": 1, "title": "a", "author": "x", "url": "u1", "created_at": None, "draft": False},
        {"number": 2, "title": "b", "author": "y", "url": "u2", "created_at": None, "draft": True},
    ]
    monkeypatch.setattr(main, "list_open_prs", lambda repo, token: fake_prs)
    # Seed the store so PR #1 is "already backfilled".
    store = main.get_store()
    store.upsert(doc_id=main.pr_doc_id("org/a", 1), ref="PR #1", summary="x",
                 reasoning="", outcome="approved_and_merged", date="")
    res = tc.get("/api/repos/open-prs", params={"repo": "org/a"}).json()
    assert res["open_pr_count"] == 2
    assert [p["number"] for p in res["new_prs"]] == [2]


# ----- PR picker ----- #

def test_repo_prs_requires_token(client):
    tc, _ = client
    assert tc.get("/api/repos/prs", params={"repo": "org/a"}).status_code == 400


def test_repo_prs_orders_open_first_then_recent(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    prs = [
        {"number": 1, "state": "closed", "updated_at": "2026-06-10", "title": "c1"},
        {"number": 2, "state": "open", "updated_at": "2026-06-01", "title": "o1"},
        {"number": 3, "state": "open", "updated_at": "2026-06-05", "title": "o2"},
    ]
    monkeypatch.setattr(main, "list_prs", lambda repo, token: prs)
    res = tc.get("/api/repos/prs", params={"repo": "org/a"}).json()
    # open PRs first (newest-updated first), then closed
    assert [p["number"] for p in res["prs"]] == [3, 2, 1]


def test_repo_pr_requires_token(client):
    tc, _ = client
    assert tc.get("/api/repos/pr", params={"repo": "org/a", "number": 1}).status_code == 400


def test_access_allowlist_crud(client):
    tc, _ = client
    assert tc.get("/api/access").json()["emails"] == []
    assert tc.post("/api/access", json={"email": "User@Co.com"}).json()["emails"] == ["user@co.com"]
    assert tc.post("/api/access", json={"email": "nope"}).status_code == 400  # invalid email
    tc.post("/api/access", json={"email": "two@co.com"})
    assert set(tc.get("/api/access").json()["emails"]) == {"user@co.com", "two@co.com"}
    tc.delete("/api/access", params={"email": "user@co.com"})
    assert tc.get("/api/access").json()["emails"] == ["two@co.com"]


def test_repo_pr_returns_form_data(client, monkeypatch):
    tc, main = client
    config_store.save_config({"github_token": "t"})
    monkeypatch.setattr(main, "fetch_pr", lambda repo, number, token: {
        "pr_number": number, "repo": repo, "title": "T", "description": "D",
        "author": "dev", "base_branch": "main", "diff": "d", "files_changed": ["a.py"],
    })
    res = tc.get("/api/repos/pr", params={"repo": "org/a", "number": 196}).json()
    assert res["pr_number"] == 196 and res["diff"] == "d" and res["files_changed"] == ["a.py"]


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
