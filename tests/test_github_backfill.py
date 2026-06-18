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
