"""Tests for core/review_store.py — file backend + postgres dispatch."""

import pytest

import review_store


@pytest.fixture
def rs(tmp_path, monkeypatch):
    monkeypatch.setattr(review_store, "_FILE_PATH", tmp_path / "reviews.json")
    monkeypatch.delenv("REVIEW_STORE_BACKEND", raising=False)
    monkeypatch.delenv("CONFIG_STORE_BACKEND", raising=False)
    return review_store


def _rec(repo="org/a", pr=1, **kw):
    base = dict(repo=repo, pr_number=pr, title="t", author="dev", approved=True,
                confidence=0.8, summary="s", issues=[], suggestions=[],
                past_decisions=[], source="api")
    base.update(kw)
    return base


def test_save_and_list_newest_first(rs):
    rs.save_review(_rec(pr=1))
    rs.save_review(_rec(pr=2))
    out = rs.list_reviews()
    assert [r["pr_number"] for r in out] == [2, 1]
    assert out[0]["id"] == 2 and "created_at" in out[0]


def test_filter_by_repo_and_pr(rs):
    rs.save_review(_rec(repo="org/a", pr=1))
    rs.save_review(_rec(repo="org/b", pr=2))
    rs.save_review(_rec(repo="org/a", pr=1))
    assert {r["repo"] for r in rs.list_reviews(repo="org/a")} == {"org/a"}
    assert len(rs.list_reviews(repo="org/a", pr_number=1)) == 2


def test_limit(rs):
    for i in range(5):
        rs.save_review(_rec(pr=i))
    assert len(rs.list_reviews(limit=3)) == 3


def test_list_fields_default_to_empty(rs):
    rs.save_review({"repo": "org/a", "pr_number": 9})  # missing list fields
    r = rs.list_reviews()[0]
    assert r["issues"] == [] and r["suggestions"] == [] and r["past_decisions"] == []


def test_history_is_append_only(rs):
    rs.save_review(_rec(pr=1, approved=True))
    rs.save_review(_rec(pr=1, approved=False))
    out = rs.list_reviews(pr_number=1)
    assert len(out) == 2  # both runs kept, not overwritten


def test_postgres_backend_dispatch(rs, monkeypatch):
    monkeypatch.setenv("REVIEW_STORE_BACKEND", "postgres")
    monkeypatch.setattr(review_store, "_pg_save", lambda rec: {**rec, "id": 1})
    monkeypatch.setattr(review_store, "_pg_list", lambda repo, pr, limit: [{"id": 1, "repo": repo}])
    assert rs.save_review(_rec())["id"] == 1
    assert rs.list_reviews(repo="org/a")[0]["repo"] == "org/a"
