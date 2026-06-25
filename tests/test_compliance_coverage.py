"""Tests for core/compliance_coverage.py."""

import compliance_coverage as coverage


def _review(compliance_review, created_at="2024-01-01T00:00:00Z"):
    return {"created_at": created_at, "compliance_review": compliance_review}


def test_detects_blind_spot_when_llm_finds_what_deterministic_misses():
    reviews = [
        _review({
            "hipaa_findings": [
                {"category": "access_control", "title": "Missing auth", "evidence": "x", "source": "llm"},
            ],
        }),
        _review({
            "hipaa_findings": [
                {"category": "access_control", "title": "Missing auth 2", "evidence": "y", "source": "llm"},
            ],
        }),
    ]
    result = coverage.analyze_coverage("acme/app", reviews, [])

    assert result["llm_count"] == 2
    assert result["deterministic_count"] == 0
    assert len(result["blind_spots"]) == 1
    assert result["blind_spots"][0]["category"] == "access_control"
    assert result["coverage_score"] < 100


def test_counts_deterministic_and_llm_findings_by_category():
    reviews = [
        _review({
            "hipaa_findings": [
                {"category": "phi_logging", "title": "PHI log", "evidence": "x", "source": "deterministic"},
                {"category": "phi_logging", "title": "PHI log LLM", "evidence": "y", "source": "llm"},
            ],
        }),
    ]
    result = coverage.analyze_coverage("acme/app", reviews, [])

    cats = result["categories"]
    assert cats["phi_logging"]["deterministic"] == 1
    assert cats["phi_logging"]["llm"] == 1
    assert cats["phi_logging"]["total"] == 2
    assert not result["blind_spots"]


def test_includes_assessments_in_analysis():
    assessments = [
        _review({
            "hl7_findings": [
                {"category": "hl7_transport", "title": "No TLS", "evidence": "x", "source": "deterministic"},
            ],
        }),
    ]
    result = coverage.analyze_coverage("acme/app", [], assessments)

    assert result["assessment_count"] == 1
    assert result["categories"]["hl7_transport"]["total"] == 1


def test_returns_zero_score_when_no_history():
    result = coverage.analyze_coverage("acme/app", [], [])
    assert result["coverage_score"] == 0
    assert "No compliance findings" in result["summary"]


def test_blind_spot_threshold_ignores_single_llm_finding():
    reviews = [
        _review({
            "hipaa_findings": [
                {"category": "audit_trail", "title": "Audit gap", "evidence": "x", "source": "llm"},
            ],
        }),
    ]
    result = coverage.analyze_coverage("acme/app", reviews, [])
    # Single LLM-only finding is a blind spot with medium severity, still reported.
    assert len(result["blind_spots"]) == 1
    assert result["blind_spots"][0]["severity"] == "medium"
