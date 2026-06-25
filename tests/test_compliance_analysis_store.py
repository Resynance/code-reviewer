"""Tests for core/compliance_analysis_store.py."""

import pytest

import compliance_analysis_store


@pytest.fixture
def temp_store(tmp_path, monkeypatch, clean_env):
    monkeypatch.setattr(compliance_analysis_store, "_FILE_PATH", tmp_path / "compliance_analysis.json")
    return compliance_analysis_store


def test_save_and_list(temp_store):
    saved = temp_store.save_analysis({
        "repo": "acme/app",
        "health": {"score": 90},
        "coverage": {"coverage_score": 75},
        "suggestions": [{"type": "enable_compliance"}],
    })
    assert saved["id"] == 1
    assert saved["repo"] == "acme/app"
    assert "created_at" in saved

    listed = temp_store.list_analyses()
    assert len(listed) == 1
    assert listed[0]["health"]["score"] == 90


def test_list_filters_by_repo(temp_store):
    temp_store.save_analysis({"repo": "acme/app", "health": {}, "coverage": {}, "suggestions": []})
    temp_store.save_analysis({"repo": "acme/other", "health": {}, "coverage": {}, "suggestions": []})

    listed = temp_store.list_analyses(repo="acme/app")
    assert len(listed) == 1
    assert listed[0]["repo"] == "acme/app"


def test_get_by_id(temp_store):
    saved = temp_store.save_analysis({
        "repo": "acme/app",
        "health": {"score": 88},
        "coverage": {},
        "suggestions": [],
    })
    fetched = temp_store.get_analysis(saved["id"])
    assert fetched["repo"] == "acme/app"
    assert fetched["health"]["score"] == 88


def test_get_missing_returns_none(temp_store):
    assert temp_store.get_analysis(9999) is None


def test_save_normalizes_empty_fields(temp_store):
    saved = temp_store.save_analysis({"repo": "acme/app"})
    assert saved["health"] == {}
    assert saved["coverage"] == {}
    assert saved["suggestions"] == []
