"""Tests for core/decision_store.py — pure helpers and Chroma integration."""

import json

import pytest

import decision_store as ds


# ----- pure helpers (no Chroma) ----- #

def test_flatten_metadata_core_fields():
    flat = ds._flatten_metadata("PR #1", "sum", "reason", "approved_and_merged", "2026-01-01", None)
    assert flat == {
        "ref": "PR #1",
        "summary": "sum",
        "reasoning": "reason",
        "outcome": "approved_and_merged",
        "date": "2026-01-01",
    }


def test_flatten_metadata_inlines_scalars_and_jsons_rest():
    flat = ds._flatten_metadata(
        "r", "s", "x", "o", "d",
        {"repo": "org/a", "author": "dev", "nested": {"k": "v"}},
    )
    assert flat["repo"] == "org/a"
    assert flat["author"] == "dev"
    assert json.loads(flat["_extra_json"]) == {"nested": {"k": "v"}}


def test_record_from_metadata_roundtrip():
    flat = ds._flatten_metadata("r", "s", "x", "o", "d", {"repo": "org/a", "nested": {"k": 1}})
    rec = ds._record_from_metadata("doc1", flat, score=0.9)
    assert rec["doc_id"] == "doc1"
    assert rec["ref"] == "r"
    assert rec["repo"] == "org/a"
    assert rec["metadata"] == {"nested": {"k": 1}}
    assert rec["score"] == 0.9


@pytest.mark.parametrize("repo,include_global,expected", [
    (None, False, None),
    (None, True, None),
    ("org/a", False, {"repo": "org/a"}),
    ("org/a", True, {"repo": {"$in": ["org/a", "*"]}}),
    ("*", True, {"repo": "*"}),     # global-only, no double include
    ("*", False, {"repo": "*"}),
])
def test_chroma_where(repo, include_global, expected):
    assert ds._chroma_where(repo, include_global) == expected


# ----- Chroma integration ----- #

def _seed(store):
    store.upsert(doc_id="a1", ref="PR #1", summary="auth in api", reasoning="jwt",
                 outcome="approved_and_merged", date="2026-01-01", metadata={"repo": "org/a"})
    store.upsert(doc_id="b1", ref="PR #2", summary="styling on web", reasoning="css",
                 outcome="changes_requested", date="2026-01-02", metadata={"repo": "org/b"})
    store.upsert(doc_id="g1", ref="ADR-1", summary="org-wide policy", reasoning="standard",
                 outcome="approved_and_merged", date="2026-01-03", metadata={"repo": ds.GLOBAL_REPO})


def test_empty_store_returns_empty(store):
    assert store.retrieve("anything", k=5) == []


def test_upsert_and_retrieve(store):
    _seed(store)
    res = store.retrieve("authentication token", k=3)
    assert len(res) == 3
    assert {r["ref"] for r in res} == {"PR #1", "PR #2", "ADR-1"}


def test_score_is_normalized(store):
    _seed(store)
    res = store.retrieve("authentication", k=3)
    for r in res:
        assert 0.0 <= r["score"] <= 1.0


def test_retrieve_filtered_by_repo(store):
    _seed(store)
    res = store.retrieve("anything", k=10, repo="org/a")
    assert [r["ref"] for r in res] == ["PR #1"]


def test_retrieve_global_only(store):
    _seed(store)
    res = store.retrieve("anything", k=10, repo=ds.GLOBAL_REPO)
    assert [r["ref"] for r in res] == ["ADR-1"]


def test_retrieve_repo_plus_global(store):
    _seed(store)
    res = store.retrieve("anything", k=10, repo="org/a", include_global=True)
    assert {r["ref"] for r in res} == {"PR #1", "ADR-1"}


def test_delete(store):
    _seed(store)
    store.delete("a1")
    refs = {r["ref"] for r in store.retrieve("anything", k=10)}
    assert "PR #1" not in refs
    assert len(refs) == 2


def test_upsert_overwrites_same_id(store):
    store.upsert(doc_id="x", ref="PR #9", summary="first", reasoning="", outcome="o", date="d")
    store.upsert(doc_id="x", ref="PR #9", summary="second", reasoning="", outcome="o", date="d")
    res = store.retrieve("first second", k=5)
    assert len(res) == 1
    assert res[0]["summary"] == "second"


def test_existing_ids(store):
    _seed(store)  # ids a1, b1, g1
    assert store.existing_ids(["a1", "missing", "g1"]) == {"a1", "g1"}
    assert store.existing_ids([]) == set()
    assert store.existing_ids(["nope"]) == set()


def test_create_store_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("DECISION_STORE_BACKEND", "bogus")
    with pytest.raises(ValueError):
        ds.create_store()
