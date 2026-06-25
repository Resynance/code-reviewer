"""Tests for core/compliance_policy_suggestions.py."""

import compliance_policy_suggestions as suggestions


def test_suggests_enabling_compliance_when_phi_present():
    files = {"src/models.py": "patient_id = None"}
    policy = {"enabled": False}
    result = suggestions.suggest_policy_updates("acme/app", policy, files)

    assert any(s["type"] == "enable_compliance" and s["value"] is True for s in result)


def test_no_enable_suggestion_when_already_enabled():
    files = {"src/models.py": "patient_id = None"}
    policy = {"enabled": True}
    result = suggestions.suggest_policy_updates("acme/app", policy, files)

    assert not any(s["type"] == "enable_compliance" for s in result)


def test_suggests_adding_new_vendor_to_approved():
    files = {"package.json": '{"dependencies": {"sentry": "^7.0"}}'}
    policy = {"approved_vendors": []}
    result = suggestions.suggest_policy_updates("acme/app", policy, files)

    assert any(s["type"] == "add_vendor" and s["value"] == "sentry" for s in result)


def test_suggests_reviewing_unknown_vendor():
    files = {"package.json": '{"dependencies": {"weird-vendor": "^1.0"}}'}
    policy = {"approved_vendors": [], "disallowed_vendors": []}
    result = suggestions.suggest_policy_updates("acme/app", policy, files)

    # Unknown vendors should be flagged for manual review rather than auto-added.
    assert any(s["type"] == "review_vendor" for s in result)


def test_suggests_adding_phi_pattern():
    files = {"src/models.py": "date_of_birth = None"}
    policy = {"phi_field_patterns": []}
    result = suggestions.suggest_policy_updates("acme/app", policy, files)

    assert any(s["type"] == "add_phi_pattern" and s["value"] == "date_of_birth" for s in result)


def test_apply_suggestion_adds_vendor():
    policy = {"approved_vendors": ["aws"]}
    suggestion = {"type": "add_vendor", "field": "approved_vendors", "value": "sentry", "action": "add"}
    updated = suggestions.apply_suggestion(policy, suggestion)

    assert "sentry" in updated["approved_vendors"]
    assert "aws" in updated["approved_vendors"]


def test_apply_suggestion_enables_compliance():
    policy = {"enabled": False}
    suggestion = {"type": "enable_compliance", "field": "enabled", "value": True, "action": "replace"}
    updated = suggestions.apply_suggestion(policy, suggestion)

    assert updated["enabled"] is True


def test_apply_review_action_does_not_mutate():
    policy = {"approved_vendors": ["aws"]}
    suggestion = {"type": "review_vendor", "field": "approved_vendors", "value": "weird", "action": "review"}
    updated = suggestions.apply_suggestion(policy, suggestion)

    assert updated["approved_vendors"] == ["aws"]
