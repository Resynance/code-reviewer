"""Tests for core/hipaa.py."""

import hipaa


def test_review_findings_detect_phi_logging_and_vendor_policy():
    policy = hipaa._normalize_policy({
        "approved_vendors": ["aws"],
        "disallowed_vendors": ["segment"],
        "phi_field_patterns": ["patient_id"],
    })
    result = hipaa.review_findings(
        '+ logger.info("patient_id=%s", patient_id)\n+ requests.post("https://api.segment.io/v1/track", json={"patient_id": 1})',
        ["api/patient.py"],
        policy,
    )
    titles = {f["title"] for f in result["hipaa_findings"]}
    assert "Potential PHI in logs or debug output" in titles
    assert "Disallowed third-party vendor appears in HIPAA review scope" in titles
    assert result["requires_manual_compliance_review"] is True


def test_normalize_result_merges_deterministic_and_llm_findings():
    deterministic = {
        "hipaa_relevant": True,
        "requires_manual_compliance_review": False,
        "hipaa_findings": [{
            "category": "phi_logging",
            "severity": "critical",
            "title": "Potential PHI in logs or debug output",
            "evidence": "logger.info(patient_ssn)",
            "recommendation": "Remove PHI from logs.",
            "file": "api/patient.py",
        }],
        "phi_exposure_risk": [{"summary": "PHI in logs", "details": "logger.info(patient_ssn)", "file": "api/patient.py"}],
    }
    llm = {
        "hipaa_relevant": True,
        "summary": "HIPAA issues found",
        "hipaa_findings": [{
            "category": "manual_review",
            "severity": "medium",
            "title": "Vendor review required",
            "evidence": "External processor used.",
            "recommendation": "Confirm BAA.",
            "manual_review": True,
        }],
    }
    merged = hipaa.normalize_result(llm, deterministic, enabled=True)
    assert merged["enabled"] is True
    assert merged["hipaa_relevant"] is True
    assert merged["requires_manual_compliance_review"] is True
    assert len(merged["hipaa_findings"]) == 2
    assert merged["phi_exposure_risk"][0]["summary"] == "PHI in logs"
