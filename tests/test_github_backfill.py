"""Tests for core/github_backfill.py — error handling and import logic, httpx mocked."""

import httpx
import pytest

import github_backfill as gb


class FakeResp:
    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        return self._json


class FakeClient:
    """Stand-in for httpx.Client returning canned responses keyed by page."""

    def __init__(self, pages, **kwargs):
        self._pages = pages

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url, params=None):
        page = (params or {}).get("page", 1)
        return self._pages.get(page, FakeResp(200, []))


def _patch_client(monkeypatch, pages):
    monkeypatch.setattr(httpx, "Client", lambda **kw: FakeClient(pages, **kw))


def _pr(number, merged=True, closed=True):
    return {
        "number": number,
        "title": f"PR {number}",
        "body": "body",
        "merged_at": "2026-01-01T00:00:00Z" if merged else None,
        "closed_at": "2026-01-02T00:00:00Z" if closed else None,
        "state": "closed",
        "user": {"login": "dev"},
        "html_url": f"https://github.com/org/repo/pull/{number}",
    }


def test_missing_token_raises(store):
    with pytest.raises(ValueError, match="token"):
        gb.backfill("org/repo", 1, token="", store=store)


def test_bad_repo_raises(store):
    with pytest.raises(ValueError, match="owner/repo"):
        gb.backfill("noslash", 1, token="t", store=store)


def test_404_message_is_actionable(store, monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(404, {"message": "Not Found"}, '{"message":"Not Found"}')})
    with pytest.raises(RuntimeError, match="not found"):
        gb.backfill("org/repo", 1, token="t", store=store)


def test_auth_error_message(store, monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(401, {}, "unauthorized")})
    with pytest.raises(RuntimeError, match="authentication"):
        gb.backfill("org/repo", 1, token="t", store=store)


def test_other_error_surfaces_status(store, monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(500, {}, "boom")})
    with pytest.raises(RuntimeError, match="500"):
        gb.backfill("org/repo", 1, token="t", store=store)


def test_successful_import_counts_and_stores(store, monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(200, [_pr(1), _pr(2)]), 2: FakeResp(200, [])})
    imported = gb.backfill("org/repo", 3, token="t", store=store)
    assert imported == 2
    refs = {r["ref"] for r in store.retrieve("PR", k=10)}
    assert refs == {"PR #1", "PR #2"}


def test_stops_on_empty_page(store, monkeypatch):
    # page 2 empty -> page 3 must never be requested
    seen = []

    class TrackingClient(FakeClient):
        def get(self, url, params=None):
            seen.append(params["page"])
            return super().get(url, params)

    pages = {1: FakeResp(200, [_pr(1)]), 2: FakeResp(200, [])}
    monkeypatch.setattr(httpx, "Client", lambda **kw: TrackingClient(pages, **kw))
    gb.backfill("org/repo", 5, token="t", store=store)
    assert seen == [1, 2]


def test_outcome_mapping():
    assert gb._outcome_for(_pr(1, merged=True)) == "approved_and_merged"
    assert gb._outcome_for(_pr(1, merged=False, closed=True)) == "closed_without_merge"
    assert gb._outcome_for({"state": "open"}) == "changes_requested"


def test_on_page_callback_invoked(store, monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(200, [_pr(1)]), 2: FakeResp(200, [])})
    calls = []
    gb.backfill("org/repo", 2, token="t", store=store, on_page=lambda p, n: calls.append((p, n)))
    assert calls == [(1, 1)]


# ----- pr_doc_id + list_open_prs ----- #

def _open_pr(number, draft=False):
    return {
        "number": number,
        "title": f"Open {number}",
        "state": "open",
        "user": {"login": "dev"},
        "html_url": f"https://github.com/org/repo/pull/{number}",
        "created_at": "2026-06-01T00:00:00Z",
        "draft": draft,
    }


def test_pr_doc_id():
    assert gb.pr_doc_id("org/repo", 5) == "org-repo-pr-5"


def test_list_open_prs_returns_summaries(monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(200, [_open_pr(1), _open_pr(2, draft=True)]), 2: FakeResp(200, [])})
    prs = gb.list_open_prs("org/repo", token="t")
    assert [p["number"] for p in prs] == [1, 2]
    assert prs[0]["author"] == "dev"
    assert prs[0]["draft"] is False
    assert prs[1]["draft"] is True


def test_list_open_prs_missing_token_raises():
    with pytest.raises(ValueError, match="token"):
        gb.list_open_prs("org/repo", token="")


def test_list_open_prs_404_message(monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(404, {}, "nf")})
    with pytest.raises(RuntimeError, match="not found"):
        gb.list_open_prs("org/repo", token="t")


def test_list_prs_includes_state(monkeypatch):
    _patch_client(monkeypatch, {1: FakeResp(200, [_open_pr(1), _pr(2)]), 2: FakeResp(200, [])})
    prs = gb.list_prs("org/repo", token="t")
    assert {p["number"]: p["state"] for p in prs} == {1: "open", 2: "closed"}


class RouteClient:
    """Fake httpx.Client that dispatches by URL/headers (call order independent)."""

    def __init__(self, meta, diff_resp, files_pages):
        self.meta = meta
        self.diff_resp = diff_resp
        self.files_pages = files_pages

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url, params=None, headers=None):
        if url.endswith("/files"):
            return self.files_pages.get((params or {}).get("page", 1), FakeResp(200, []))
        if headers and "diff" in headers.get("Accept", ""):
            return self.diff_resp
        return self.meta


def test_fetch_pr_shapes_form_data(monkeypatch):
    meta = FakeResp(200, {"number": 196, "title": "T", "body": "B",
                          "user": {"login": "dev"}, "base": {"ref": "develop"}})
    diff = FakeResp(200, None, "DIFF-TEXT")
    files = {1: FakeResp(200, [{"filename": "a.py"}, {"filename": "b.py"}]), 2: FakeResp(200, [])}
    monkeypatch.setattr(httpx, "Client", lambda **kw: RouteClient(meta, diff, files))
    data = gb.fetch_pr("org/repo", 196, token="t")
    assert data["pr_number"] == 196
    assert data["title"] == "T" and data["description"] == "B"
    assert data["author"] == "dev" and data["base_branch"] == "develop"
    assert data["diff"] == "DIFF-TEXT"
    assert data["files_changed"] == ["a.py", "b.py"]


def test_fetch_pr_builds_diff_from_files_on_406(monkeypatch):
    # GitHub 406s the .diff media type on PRs with >300 files.
    meta = FakeResp(200, {"number": 1, "title": "big", "body": "",
                          "user": {"login": "d"}, "base": {"ref": "main"}})
    diff406 = FakeResp(406, {"message": "Sorry, the diff exceeded the maximum number of files (300)."}, "")
    files = {
        1: FakeResp(200, [
            {"filename": "a.py", "status": "modified", "patch": "@@ -1 +1 @@\n-x\n+y"},
            {"filename": "big.bin", "status": "added"},  # no patch
        ]),
        2: FakeResp(200, []),
    }
    monkeypatch.setattr(httpx, "Client", lambda **kw: RouteClient(meta, diff406, files))
    data = gb.fetch_pr("org/repo", 1, token="t")
    assert "diff --git a/a.py b/a.py" in data["diff"]
    assert "+y" in data["diff"]
    assert "no textual diff available" in data["diff"]  # binary file
    assert data["files_changed"] == ["a.py", "big.bin"]


def test_fetch_pr_missing_token():
    with pytest.raises(ValueError, match="token"):
        gb.fetch_pr("org/repo", 1, token="")


# ----- owner / repo discovery ----- #

def _client_from(handler, monkeypatch):
    class C:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None):
            return handler(url, params or {})

    monkeypatch.setattr(httpx, "Client", lambda **k: C())


def test_list_owners(monkeypatch):
    def handler(url, params):
        if url.endswith("/user"):
            return FakeResp(200, {"login": "me"})
        if url.endswith("/user/orgs"):
            return FakeResp(200, [{"login": "acme"}, {"login": "globex"}] if params.get("page", 1) == 1 else [])
        return FakeResp(404, {}, "nf")

    _client_from(handler, monkeypatch)
    owners = gb.list_owners("t")
    assert owners[0] == {"login": "me", "type": "user"}
    assert {o["login"] for o in owners} == {"me", "acme", "globex"}
    assert all(o["type"] == "org" for o in owners[1:])


def test_list_owner_repos_org(monkeypatch):
    def handler(url, params):
        if "/orgs/acme/repos" in url:
            return FakeResp(200, [{"full_name": "acme/a", "name": "a", "private": True, "description": "d"}]
                            if params.get("page", 1) == 1 else [])
        return FakeResp(404, {}, "nf")

    _client_from(handler, monkeypatch)
    repos = gb.list_owner_repos("acme", "t", owner_type="org")
    assert repos == [{"full_name": "acme/a", "name": "a", "private": True, "description": "d"}]


def test_list_owner_repos_user_uses_affiliation(monkeypatch):
    seen = {}

    def handler(url, params):
        if url.endswith("/user/repos"):
            seen.update(params)
            return FakeResp(200, [{"full_name": "me/x", "name": "x", "private": False}]
                            if params.get("page", 1) == 1 else [])
        return FakeResp(404, {}, "nf")

    _client_from(handler, monkeypatch)
    repos = gb.list_owner_repos("me", "t", owner_type="user")
    assert repos[0]["full_name"] == "me/x"
    assert seen.get("affiliation") == "owner"


def test_list_owners_missing_token():
    with pytest.raises(ValueError, match="token"):
        gb.list_owners("")


def test_list_owners_auth_error(monkeypatch):
    _client_from(lambda url, params: FakeResp(403, {}, "forbidden"), monkeypatch)
    with pytest.raises(RuntimeError, match="authentication failed"):
        gb.list_owners("t")
