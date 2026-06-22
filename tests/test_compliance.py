"""Tests for core/compliance.py."""

import compliance


def test_review_findings_detect_phi_logging_and_vendor_policy():
    policy = compliance._normalize_policy({
        "approved_vendors": ["aws"],
        "disallowed_vendors": ["segment"],
        "phi_field_patterns": ["patient_id"],
    })
    result = compliance.review_findings(
        '+ logger.info("patient_id=%s", patient_id)\n+ requests.post("https://api.segment.io/v1/track", json={"patient_id": 1})',
        ["api/patient.py"],
        policy,
    )
    titles = {f["title"] for f in result["hipaa_findings"]}
    assert "Potential PHI in logs or debug output" in titles
    assert "Disallowed third-party vendor appears in HIPAA review scope" in titles
    assert result["requires_manual_compliance_review"] is True


def test_review_findings_detect_hl7_logging_transport_and_validation_gaps():
    policy = compliance._normalize_policy({
        "approved_hl7_versions": ["2.5.1"],
        "required_hl7_validation_signals": ["ack", "message_control_id"],
        "required_hl7_transport_signals": ["mllps", "tls"],
    })
    result = compliance.review_findings(
        '+ logger.info("MSH|^~\\\\&|ADT|PID|patient_id")\n+ start_hl7_listener("tcp://feed:2575")\n+ msg = "MSH|^~\\\\&|ADT|PID|PV1|"',
        ["integrations/hl7_listener.py"],
        policy,
    )
    titles = {f["title"] for f in result["hl7_findings"]}
    assert "Raw HL7 payload appears in logs or debug output" in titles
    assert "HL7 transport lacks obvious secure channel signal" in titles
    assert "HL7 message handling lacks obvious validation or ACK controls" in titles
    assert result["hl7_relevant"] is True
    assert result["requires_manual_compliance_review"] is False


def test_normalize_result_merges_deterministic_and_llm_findings():
    deterministic = {
        "hipaa_relevant": True,
        "hl7_relevant": True,
        "requires_manual_compliance_review": False,
        "hipaa_findings": [{
            "category": "phi_logging",
            "severity": "critical",
            "title": "Potential PHI in logs or debug output",
            "evidence": "logger.info(patient_ssn)",
            "recommendation": "Remove PHI from logs.",
            "file": "api/patient.py",
        }],
        "hl7_findings": [{
            "category": "hl7_validation",
            "severity": "medium",
            "title": "HL7 message handling lacks obvious validation or ACK controls",
            "evidence": 'msg = "MSH|^~\\\\&|ADT"',
            "recommendation": "Validate and ACK/NACK messages.",
            "file": "integrations/hl7.py",
        }],
        "phi_exposure_risk": [{"summary": "PHI in logs", "details": "logger.info(patient_ssn)", "file": "api/patient.py"}],
        "hl7_message_integrity_gaps": [{"summary": "No ACK", "details": "Missing ACK handling", "file": "integrations/hl7.py"}],
    }
    llm = {
        "hipaa_relevant": True,
        "hl7_relevant": True,
        "summary": "HIPAA issues found",
        "hipaa_findings": [{
            "category": "manual_review",
            "severity": "medium",
            "title": "Vendor review required",
            "evidence": "External processor used.",
            "recommendation": "Confirm BAA.",
            "manual_review": True,
        }],
        "hl7_findings": [{
            "category": "hl7_manual_review",
            "severity": "medium",
            "title": "Partner contract review required",
            "evidence": "External interface assumptions are not fully visible in code.",
            "recommendation": "Confirm partner ACK, retry, and profile expectations.",
            "manual_review": True,
        }],
    }
    merged = compliance.normalize_result(llm, deterministic, enabled=True)
    assert merged["enabled"] is True
    assert merged["hipaa_relevant"] is True
    assert merged["hl7_relevant"] is True
    assert merged["requires_manual_compliance_review"] is True
    assert len(merged["hipaa_findings"]) == 2
    assert len(merged["hl7_findings"]) == 2
    assert merged["phi_exposure_risk"][0]["summary"] == "PHI in logs"
    assert merged["hl7_message_integrity_gaps"][0]["summary"] == "No ACK"
