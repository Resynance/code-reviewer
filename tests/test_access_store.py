"""Tests for core/access_store.py — file backend + cache + postgres dispatch."""

import pytest

import access_store


@pytest.fixture
def acc(tmp_path, monkeypatch):
    monkeypatch.setattr(access_store, "_FILE_PATH", tmp_path / "access.json")
    monkeypatch.delenv("CONFIG_STORE_BACKEND", raising=False)
    access_store._CACHE["emails"] = None
    return access_store


def test_add_list_remove(acc):
    assert acc.list_emails() == []
    assert acc.add_email("A@b.com") == ["a@b.com"]      # normalized lowercase
    acc.add_email("c@b.com")
    assert set(acc.list_emails()) == {"a@b.com", "c@b.com"}
    acc.add_email("a@b.com")  # dedupe
    assert len(acc.list_emails()) == 2
    assert acc.remove_email("a@b.com") == ["c@b.com"]


def test_blank_email_ignored(acc):
    assert acc.add_email("  ") == []


def test_cache_and_invalidation(acc):
    acc.add_email("x@b.com")
    assert acc.allowed_emails_cached() == {"x@b.com"}
    acc.add_email("y@b.com")  # mutation invalidates the cache
    assert acc.allowed_emails_cached() == {"x@b.com", "y@b.com"}


def test_postgres_dispatch(acc, monkeypatch):
    monkeypatch.setenv("CONFIG_STORE_BACKEND", "postgres")
    store = {"emails": []}
    monkeypatch.setattr(access_store, "_pg_list", lambda: list(store["emails"]))
    monkeypatch.setattr(access_store, "_pg_add", lambda e: store["emails"].append(e))
    monkeypatch.setattr(access_store, "_pg_remove", lambda e: store["emails"].remove(e) if e in store["emails"] else None)
    acc.add_email("z@b.com")
    assert acc.list_emails() == ["z@b.com"]
    acc.remove_email("z@b.com")
    assert acc.list_emails() == []
