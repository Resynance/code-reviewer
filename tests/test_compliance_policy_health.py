"""Tests for core/compliance_policy_health.py."""

import compliance_policy_health as health


def test_detects_disallowed_and_unlisted_vendors():
    files = {
        "package.json": '{"dependencies": {"segment": "^1.0", "openai": "^4.0"}}',
        "src/api.py": 'requests.post("https://api.segment.io/v1/track", json={"patient_id": 1})',
    }
    policy = {
        "approved_vendors": ["aws"],
        "disallowed_vendors": ["segment"],
    }
    result = health.analyze_policy_health("acme/app", [], files, policy)

    assert result["vendors"]["disallowed_hits"] == ["segment"]
    assert "openai" in result["vendors"]["unlisted"]
    assert result["vendors"]["stale_approved"] == ["aws"]
    assert any(f["severity"] == "high" and "Disallowed vendor" in f["title"] for f in result["findings"])
    assert result["score"] < 100


def test_detects_missing_required_signals():
    files = {
        "src/api.py": 'def create_patient(): pass',
    }
    policy = {
        "required_auth_signals": ["require_user", "@login_required"],
        "required_audit_signals": ["audit_log"],
    }
    result = health.analyze_policy_health("acme/app", [], files, policy)

    missing = set(result["signals"]["required_signals_not_observed"])
    assert {"require_user", "@login_required", "audit_log"}.issubset(missing)
    assert any("Required compliance signals not observed" in f["title"] for f in result["findings"])


def test_detects_phi_terms_missing_from_policy():
    files = {
        "src/models.py": 'class Patient: date_of_birth = None',
    }
    policy = {"phi_field_patterns": ["patient_id"]}
    result = health.analyze_policy_health("acme/app", [], files, policy)

    assert "date_of_birth" in result["phi_patterns"]["missing_from_policy"]
    assert "patient_id" in result["phi_patterns"]["in_policy"]


def test_parses_multiple_manifest_formats():
    files = {
        "requirements.txt": "requests>=2.0\nopenai>=1.0\n",
        "package.json": '{"dependencies": {"sentry": "^7.0"}}',
        "go.mod": "require github.com/aws/aws-sdk-go v1.0\n",
    }
    result = health.analyze_policy_health("acme/app", [], files, {})
    observed = set(result["vendors"]["observed"])
    assert {"requests", "openai", "sentry", "aws"}.issubset(observed)


def test_score_is_perfect_when_all_default_signals_observed():
    # Provide every default required signal so nothing is flagged as missing.
    files = {
        "src/api.py": (
            "require_user Depends(require_user) @login_required "
            "audit audit_log 'audit trail' access_log at_rest in_transit "
            "schema validate ack nack message_control_id "
            "tls ssl https sftp vpn mllps"
        ),
    }
    result = health.analyze_policy_health("acme/app", [], files, {})
    assert result["score"] == 100
    assert not result["findings"]


def test_extract_manifest_packages_ignores_unsupported_files():
    assert health.extract_manifest_packages({"main.py": "import os"}) == set()
