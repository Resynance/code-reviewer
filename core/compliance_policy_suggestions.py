"""
compliance_policy_suggestions.py — detect dependency and signal drift between
assessments (or between the current assessment and an empty baseline) and emit
structured policy-patch suggestions.

Each suggestion carries enough metadata for the UI to render it with an
"Apply" button. Applying a suggestion updates the repo's compliance policy in
config_store.
"""

from __future__ import annotations

import copy
import re
from typing import Iterable

import compliance
import compliance_policy_health as _health


# Vendors we can confidently suggest adding to approved_vendors when observed.
_KNOWN_SAFE_VENDORS = {
    "sentry", "datadog", "newrelic", "posthog",
    "aws", "gcp", "google", "azure",
    "postgres", "redis",
}

# PHI terms that are strong enough to suggest adding to phi_field_patterns.
_SUGGESTIBLE_PHI_TERMS = {
    "patient_id", "patient_uuid", "patient_identifier",
    "mrn", "medical_record_number",
    "ssn", "social_security_number",
    "dob", "date_of_birth", "birth_date",
    "diagnosis_code", "procedure_code", "icd10", "icd9",
    "medication", "prescription_id", "claim_id", "lab_result_id",
}

# Regex to detect package imports / requires that reveal a vendor in code.
_VENDOR_IMPORT_RE = re.compile(
    r"(?:import\s+(?:.*\s+from\s+)?['\"]|require\(['\"]|from\s+)([^'\"\s]+)",
    re.IGNORECASE,
)


class PolicySuggestionEngine:
    """Generate and apply policy-patch suggestions from repo drift."""

    def __init__(self, repo: str, policy: dict):
        self.repo = repo
        self.policy = compliance._normalize_policy(policy)

    def suggest(
        self,
        current_files: dict[str, str],
        prior_files: dict[str, str] | None = None,
    ) -> list[dict]:
        """Return a list of policy update suggestions."""
        suggestions = []
        suggestions.extend(self._suggest_enable_compliance(current_files))
        suggestions.extend(self._suggest_vendor_changes(current_files, prior_files or {}))
        suggestions.extend(self._suggest_phi_patterns(current_files, prior_files or {}))
        suggestions.extend(self._suggest_signal_updates(current_files, prior_files or {}))
        return suggestions

    # ------------------------------------------------------------------ #
    # Compliance enablement
    # ------------------------------------------------------------------ #

    def _suggest_enable_compliance(self, files: dict[str, str]) -> list[dict]:
        if self.policy.get("enabled"):
            return []
        all_text = "\n".join(files.values()).lower()
        phi_hit = any(term in all_text for term in compliance._PHI_TERMS)
        hl7_hit = any(term in all_text for term in compliance._HL7_TERMS)
        if not (phi_hit or hl7_hit):
            return []
        evidence = "PHI/HL7-relevant terms detected in codebase." if (phi_hit and hl7_hit) else (
            "PHI-relevant terms detected in codebase." if phi_hit else "HL7-relevant terms detected in codebase."
        )
        return [{
            "id": f"{self.repo}:enable_compliance",
            "type": "enable_compliance",
            "field": "enabled",
            "value": True,
            "action": "replace",
            "severity": "medium",
            "reason": "The codebase appears to handle healthcare data, but HIPAA/HL7 compliance review is not enabled for this repo.",
            "evidence": evidence,
        }]

    # ------------------------------------------------------------------ #
    # Vendor suggestions
    # ------------------------------------------------------------------ #

    def _suggest_vendor_changes(
        self,
        current_files: dict[str, str],
        prior_files: dict[str, str],
    ) -> list[dict]:
        suggestions = []
        current_vendors = self._observed_vendors(current_files)
        prior_vendors = self._observed_vendors(prior_files)
        approved = {v.lower() for v in self.policy.get("approved_vendors") or []}
        disallowed = {v.lower() for v in self.policy.get("disallowed_vendors") or []}

        # New vendors since prior assessment.
        new_vendors = current_vendors - prior_vendors
        for vendor in sorted(new_vendors):
            vlower = vendor.lower()
            if vlower in approved or vlower in disallowed:
                continue
            if vlower in _KNOWN_SAFE_VENDORS:
                suggestions.append({
                    "id": f"{self.repo}:add_approved_vendor:{vendor}",
                    "type": "add_vendor",
                    "field": "approved_vendors",
                    "value": vendor,
                    "action": "add",
                    "severity": "low",
                    "reason": f"'{vendor}' appears to be a new dependency or integration. Suggest adding it to approved vendors after BAA review.",
                    "evidence": _vendor_evidence(current_files, vendor),
                })
            else:
                suggestions.append({
                    "id": f"{self.repo}:review_vendor:{vendor}",
                    "type": "review_vendor",
                    "field": "approved_vendors",
                    "value": vendor,
                    "action": "review",
                    "severity": "medium",
                    "reason": f"'{vendor}' appears to be a new dependency or integration not in the approved or disallowed vendor lists.",
                    "evidence": _vendor_evidence(current_files, vendor),
                })

        # Vendors that disappeared from code but remain approved.
        if prior_files:
            gone_vendors = approved & prior_vendors - current_vendors
            for vendor in sorted(gone_vendors):
                suggestions.append({
                    "id": f"{self.repo}:remove_approved_vendor:{vendor}",
                    "type": "remove_vendor",
                    "field": "approved_vendors",
                    "value": vendor,
                    "action": "remove",
                    "severity": "low",
                    "reason": f"'{vendor}' was observed in the previous assessment but is no longer present. Consider removing it from approved vendors to keep the policy current.",
                    "evidence": "Vendor no longer found in dependency manifests or source URLs.",
                })

        # New disallowed vendor hits.
        for vendor in sorted(current_vendors & disallowed):
            suggestions.append({
                "id": f"{self.repo}:disallowed_vendor_present:{vendor}",
                "type": "disallowed_vendor_present",
                "field": "disallowed_vendors",
                "value": vendor,
                "action": "review",
                "severity": "high",
                "reason": f"'{vendor}' is in the disallowed vendor list but still appears in the codebase.",
                "evidence": _vendor_evidence(current_files, vendor),
            })

        return suggestions

    def _observed_vendors(self, files: dict[str, str]) -> set[str]:
        manifest = _health.extract_manifest_packages(files)
        code = _health.extract_code_vendors(files)
        imports = set()
        for content in files.values():
            for match in _VENDOR_IMPORT_RE.finditer(content):
                path = match.group(1).lower()
                for known in _health._KNOWN_VENDORS:
                    if known in path:
                        imports.add(known)
        return {v.lower() for v in manifest | code | imports}

    # ------------------------------------------------------------------ #
    # PHI pattern suggestions
    # ------------------------------------------------------------------ #

    def _suggest_phi_patterns(
        self,
        current_files: dict[str, str],
        prior_files: dict[str, str],
    ) -> list[dict]:
        suggestions = []
        current_terms = self._observed_phi_terms(current_files)
        prior_terms = self._observed_phi_terms(prior_files)
        policy_patterns = {p.lower() for p in self.policy.get("phi_field_patterns") or []}

        new_terms = current_terms - prior_terms - policy_patterns
        for term in sorted(new_terms):
            if term not in _SUGGESTIBLE_PHI_TERMS:
                continue
            suggestions.append({
                "id": f"{self.repo}:add_phi_pattern:{term}",
                "type": "add_phi_pattern",
                "field": "phi_field_patterns",
                "value": term,
                "action": "add",
                "severity": "low",
                "reason": f"PHI-like term '{term}' was observed in the codebase and is not in phi_field_patterns.",
                "evidence": _term_evidence(current_files, term),
            })
        return suggestions

    def _observed_phi_terms(self, files: dict[str, str]) -> set[str]:
        all_text = "\n".join(files.values()).lower()
        observed = set()
        for term in _SUGGESTIBLE_PHI_TERMS:
            if term in all_text:
                observed.add(term)
        return observed

    # ------------------------------------------------------------------ #
    # Signal suggestions
    # ------------------------------------------------------------------ #

    def _suggest_signal_updates(
        self,
        current_files: dict[str, str],
        prior_files: dict[str, str],
    ) -> list[dict]:
        suggestions = []
        current_text = "\n".join(current_files.values()).lower()
        prior_text = "\n".join(prior_files.values()).lower() if prior_files else ""

        signal_fields = {
            "required_auth_signals": "auth",
            "required_audit_signals": "audit",
            "required_encryption": "encryption",
            "required_hl7_validation_signals": "HL7 validation",
            "required_hl7_transport_signals": "HL7 transport",
        }

        for field, label in signal_fields.items():
            configured = {s.lower() for s in self.policy.get(field) or []}
            for signal in configured:
                newly_present = signal in current_text and signal not in prior_text
                if newly_present:
                    suggestions.append({
                        "id": f"{self.repo}:signal_present:{field}:{signal}",
                        "type": "signal_present",
                        "field": field,
                        "value": signal,
                        "action": "review",
                        "severity": "low",
                        "reason": f"Required {label} signal '{signal}' now appears in code. This confirms the policy requirement is implemented.",
                        "evidence": _term_evidence(current_files, signal),
                    })
        return suggestions


# ---------------------------------------------------------------------- #
# Convenience function
# ---------------------------------------------------------------------- #

def suggest_policy_updates(
    repo: str,
    policy: dict,
    current_files: dict[str, str],
    prior_files: dict[str, str] | None = None,
) -> list[dict]:
    return PolicySuggestionEngine(repo, policy).suggest(current_files, prior_files)


# ---------------------------------------------------------------------- #
# Applying suggestions
# ---------------------------------------------------------------------- #

def apply_suggestion(repo_policy: dict, suggestion: dict) -> dict:
    """Return a new repo policy with the suggested change applied."""
    policy = copy.deepcopy(compliance._normalize_policy(repo_policy))
    stype = suggestion.get("type")
    field = suggestion.get("field")
    value = suggestion.get("value")
    action = suggestion.get("action")

    if stype == "enable_compliance" and field == "enabled":
        policy["enabled"] = bool(value)
        return policy

    if field not in policy:
        return policy

    current = list(policy.get(field) or [])

    if action == "add":
        if value not in current:
            current.append(value)
    elif action == "remove":
        current = [v for v in current if v != value]
    elif action == "replace":
        current = [value] if isinstance(value, list) else value
    elif action == "review":
        # Review actions are intentionally manual; do not mutate automatically.
        return policy

    policy[field] = current
    return policy


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _vendor_evidence(files: dict[str, str], vendor: str) -> str:
    vendor_lower = vendor.lower()
    for path, content in files.items():
        lower = content.lower()
        if vendor_lower in lower:
            # Find the line containing the vendor.
            for line in content.splitlines():
                if vendor_lower in line.lower():
                    return f"{path}: {line.strip()[:120]}"
    return f"Vendor '{vendor}' observed in codebase."


def _term_evidence(files: dict[str, str], term: str) -> str:
    term_lower = term.lower()
    for path, content in files.items():
        for line in content.splitlines():
            if term_lower in line.lower():
                return f"{path}: {line.strip()[:120]}"
    return f"Term '{term}' observed in codebase."
