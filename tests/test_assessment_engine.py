"""Tests for core/assessment_engine.py."""

import base64

import pytest

from assessment_engine import AssessmentEngine


class _FakeResponse:
    def __init__(self, payload=None, *, status_error=None):
        self._payload = payload or {}
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        key = (url, tuple(sorted((params or {}).items())))
        response = self._responses.get(key) or self._responses.get((url, ()))
        if response is None:
            raise AssertionError(f"unexpected request: {url} {params}")
        return response


def _encoded(content: str) -> dict:
    return {"content": base64.b64encode(content.encode()).decode()}


def test_fetch_repo_content_fails_when_selected_file_cannot_be_fetched(monkeypatch):
    import httpx

    repo = "org/app"
    base = "https://api.github.com"
    responses = {
        (f"{base}/repos/{repo}", ()): _FakeResponse({"default_branch": "main"}),
        (
            f"{base}/repos/{repo}/git/trees/main",
            (("recursive", "1"),),
        ): _FakeResponse({
            "tree": [
                {"type": "blob", "path": "README.md", "size": 12},
                {"type": "blob", "path": "app.py", "size": 30},
            ]
        }),
        (f"{base}/repos/{repo}/contents/README.md", ()): _FakeResponse(_encoded("# App")),
        (f"{base}/repos/{repo}/contents/app.py", ()): _FakeResponse(
            status_error=httpx.HTTPStatusError(
                "403 rate limited",
                request=httpx.Request("GET", f"{base}/repos/{repo}/contents/app.py"),
                response=httpx.Response(403),
            )
        ),
    }

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: _FakeClient(responses))

    with pytest.raises(RuntimeError, match="Failed to fetch selected repository files: app.py"):
        AssessmentEngine()._fetch_repo_content(repo, "token")
