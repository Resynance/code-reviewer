"""Tests for GitHub content fetching in assessment_engine.py."""

import pytest

import assessment_engine


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


def test_fetch_repo_content_rejects_truncated_recursive_tree(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, url, params=None):
            calls.append((url, params))
            if url.endswith("/repos/acme/app"):
                return FakeResponse({"default_branch": "main"})
            if url.endswith("/repos/acme/app/git/trees/main"):
                return FakeResponse({
                    "truncated": True,
                    "tree": [
                        {"type": "blob", "path": "README.md", "size": 20},
                    ],
                })
            raise AssertionError(f"unexpected content fetch after truncated tree: {url}")

    monkeypatch.setattr("httpx.Client", FakeClient)

    engine = assessment_engine.AssessmentEngine()
    with pytest.raises(RuntimeError, match="truncated repository tree"):
        engine._fetch_repo_content("acme/app", "ghp_token")

    assert [url for url, _ in calls] == [
        f"{assessment_engine.GITHUB_API_BASE}/repos/acme/app",
        f"{assessment_engine.GITHUB_API_BASE}/repos/acme/app/git/trees/main",
    ]
