"""
compliance_policy_health.py — audit a repo's HIPAA/HL7 compliance policy against
its actual code and dependencies.

The goal is to flag stale or misaligned policies before they cause blind spots
in deterministic compliance scanning. This is a read-only analyzer; it never
mutates the configured policy.
"""

from __future__ import annotations

import json
import re
from typing import Iterable

import compliance


# Dependency manifest filenames (case-insensitive basename match).
_DEPENDENCY_MANIFESTS = {
    "package.json": "npm",
    "requirements.txt": "pip",
    "pyproject.toml": "python",
    "go.mod": "go",
    "cargo.toml": "rust",
    "composer.json": "php",
    "gemfile": "ruby",
}

# Vendor names that are relevant to BAA / HIPAA third-party checks. The scanner
# looks for these in dependency manifests and in source URLs.
_KNOWN_VENDORS = {
    # analytics / observability
    "sentry", "segment", "mixpanel", "amplitude", "posthog", "datadog", "newrelic",
    # messaging
    "slack", "mailgun", "sendgrid", "twilio",
    # LLM / AI
    "openai", "anthropic", "cohere", "huggingface", "huggingface_hub",
    # cloud
    "aws", "gcp", "google", "azure",
    # databases / infra
    "mongodb", "postgres", "mysql", "redis", "elasticsearch", "snowflake", "databricks",
}

# Built-in PHI-ish terms from compliance.py so we can detect observed patterns
# that are missing from the policy.
_KNOWN_PHI_TERMS = set(compliance._PHI_TERMS)

# Regex to extract URLs / hostnames from code.
_VENDOR_URL_RE = re.compile(
    r"(?:https?://|['\"])([^'\"\s]+(?:sentry|segment|mixpanel|amplitude|slack|openai|"
    r"anthropic|posthog|datadog|newrelic|mailgun|sendgrid|twilio)[^'\"\s]*)",
    re.IGNORECASE,
)


class PolicyHealthAnalyzer:
    """Analyze a compliance policy against fetched repo content."""

    def __init__(self, policy: dict):
        self.policy = compliance._normalize_policy(policy)

    def analyze(
        self,
        repo: str,
        tree_lines: list[str],
        files: dict[str, str],
        latest_assessment_at: str | None = None,
        assessment_count: int = 0,
    ) -> dict:
        """Return a structured health report for the configured policy."""
        manifest_packages = self._extract_manifest_packages(files)
        code_vendors = self._extract_code_vendors(files)
        all_vendors = {*manifest_packages, *code_vendors}

        approved = {v.lower() for v in self.policy.get("approved_vendors") or []}
        disallowed = {v.lower() for v in self.policy.get("disallowed_vendors") or []}

        vendor_report = self._analyze_vendors(all_vendors, approved, disallowed)
        signal_report = self._analyze_signals(files)
        phi_report = self._analyze_phi_patterns(files)

        findings = []
        findings.extend(self._vendor_findings(vendor_report))
        findings.extend(self._signal_findings(signal_report))
        findings.extend(self._phi_findings(phi_report))

        score = self._compute_score(vendor_report, signal_report, phi_report, findings)

        return {
            "repo": repo,
            "policy_enabled": bool(self.policy.get("enabled")),
            "score": score,
            "freshness": {
                "latest_assessment_at": latest_assessment_at or "",
                "assessment_count": assessment_count,
            },
            "vendors": vendor_report,
            "signals": signal_report,
            "phi_patterns": phi_report,
            "findings": findings,
        }

    # ------------------------------------------------------------------ #
    # Vendor analysis
    # ------------------------------------------------------------------ #

    def _analyze_vendors(self, observed: set[str], approved: set[str], disallowed: set[str]) -> dict:
        observed_lower = {v.lower() for v in observed}
        approved_hits = sorted(observed & approved)
        disallowed_hits = sorted(observed & disallowed)
        unlisted = sorted(observed - approved - disallowed)
        stale_approved = sorted(approved - observed_lower - {"none"})
        return {
            "observed": sorted(observed),
            "approved_hits": approved_hits,
            "disallowed_hits": disallowed_hits,
            "unlisted": unlisted,
            "stale_approved": stale_approved,
        }

    def _vendor_findings(self, vendor_report: dict) -> list[dict]:
        findings = []
        if vendor_report["disallowed_hits"]:
            findings.append({
                "severity": "high",
                "category": "vendor_policy",
                "title": "Disallowed vendor in use",
                "evidence": f"Observed disallowed vendors: {', '.join(vendor_report['disallowed_hits'])}",
                "recommendation": "Remove the integration or update the disallowed-vendor policy and document the exception.",
            })
        if vendor_report["unlisted"]:
            findings.append({
                "severity": "medium",
                "category": "vendor_policy",
                "title": "Third-party vendors not covered by approved/disallowed lists",
                "evidence": f"Observed unlisted vendors: {', '.join(vendor_report['unlisted'])}",
                "recommendation": "Add these vendors to approved_vendors or disallowed_vendors after BAA/policy review.",
            })
        if vendor_report["stale_approved"]:
            findings.append({
                "severity": "low",
                "category": "vendor_policy",
                "title": "Approved vendors not observed in code",
                "evidence": f"Approved but not observed: {', '.join(vendor_report['stale_approved'])}",
                "recommendation": "Review whether these approved vendors are still in use; remove stale entries to keep the policy current.",
            })
        return findings

    def _extract_manifest_packages(self, files: dict[str, str]) -> set[str]:
        return extract_manifest_packages(files)

    def _extract_code_vendors(self, files: dict[str, str]) -> set[str]:
        return extract_code_vendors(files)

    @staticmethod
    def _parse_package_json(content: str) -> set[str]:
        data = json.loads(content)
        deps = data.get("dependencies") or {}
        dev = data.get("devDependencies") or {}
        return {str(k).lower() for k in {**deps, **dev}}

    @staticmethod
    def _parse_requirements_txt(content: str) -> set[str]:
        packages: set[str] = set()
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            # Handle extras and version specifiers: package[extra]>=1.0
            name = re.split(r"[\[\=\<\>\!\ ~]", line)[0].strip().lower()
            if name:
                packages.add(name)
        return packages

    @staticmethod
    def _parse_pyproject_toml(content: str) -> set[str]:
        packages: set[str] = set()
        # Lightweight parser: extract bare package names from dependency lists.
        # project.dependencies
        for match in re.finditer(r'^dependencies\s*=\s*\[(.*?)\]', content, re.MULTILINE | re.DOTALL):
            packages.update(_bare_package_names(match.group(1)))
        # project.optional-dependencies.<group>
        for match in re.finditer(r'optional-dependencies\..*?=\s*\[(.*?)\]', content, re.MULTILINE | re.DOTALL):
            packages.update(_bare_package_names(match.group(1)))
        # build-system.requires
        for match in re.finditer(r'requires\s*=\s*\[(.*?)\]', content, re.MULTILINE | re.DOTALL):
            packages.update(_bare_package_names(match.group(1)))
        # tool.poetry.dependencies (table keys)
        in_poetry_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if re.match(r'^\[tool\.poetry\.dependencies\]', stripped):
                in_poetry_deps = True
                continue
            if in_poetry_deps and stripped.startswith("["):
                break
            if in_poetry_deps and "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip().lower()
                if key and key != "python":
                    packages.add(key)
        return packages

    @staticmethod
    def _parse_go_mod(content: str) -> set[str]:
        packages: set[str] = set()
        in_require = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "require (":
                in_require = True
                continue
            if in_require and stripped == ")":
                in_require = False
                continue
            if in_require or stripped.startswith("require "):
                # module path, possibly with version
                parts = stripped.replace("require ", "").split()
                if parts:
                    mod = parts[0].lower()
                    packages.add(mod.rsplit("/", 1)[-1])
                    # Also capture known vendor names embedded in the path.
                    for known in _KNOWN_VENDORS:
                        if known in mod:
                            packages.add(known)
        return packages

    @staticmethod
    def _parse_cargo_toml(content: str) -> set[str]:
        packages: set[str] = set()
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if re.match(r'^\[dependencies\]', stripped) or re.match(r'^\[dev-dependencies\]', stripped):
                in_deps = True
                continue
            if in_deps and stripped.startswith("["):
                in_deps = False
            if in_deps and "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip().lower()
                if key:
                    packages.add(key)
        return packages

    @staticmethod
    def _parse_composer_json(content: str) -> set[str]:
        data = json.loads(content)
        req = data.get("require") or {}
        dev = data.get("require-dev") or {}
        return {str(k).split("/")[-1].lower() for k in {**req, **dev}}

    @staticmethod
    def _parse_gemfile(content: str) -> set[str]:
        packages: set[str] = set()
        for line in content.splitlines():
            line = line.strip()
            match = re.match(r'gem\s+["\']([^"\']+)["\']', line)
            if match:
                packages.add(match.group(1).lower())
        return packages

    # ------------------------------------------------------------------ #
    # Signal analysis
    # ------------------------------------------------------------------ #

    def _analyze_signals(self, files: dict[str, str]) -> dict:
        all_text = "\n".join(files.values())
        lower_text = all_text.lower()

        def observed(signals: list[str]) -> list[str]:
            return sorted({s for s in signals if s.lower() in lower_text})

        auth_observed = observed(self.policy.get("required_auth_signals") or [])
        audit_observed = observed(self.policy.get("required_audit_signals") or [])
        encryption_observed = observed(self.policy.get("required_encryption") or [])
        hl7_validation_observed = observed(self.policy.get("required_hl7_validation_signals") or [])
        hl7_transport_observed = observed(self.policy.get("required_hl7_transport_signals") or [])

        required_auth = set(self.policy.get("required_auth_signals") or [])
        required_audit = set(self.policy.get("required_audit_signals") or [])
        required_encryption = set(self.policy.get("required_encryption") or [])
        required_hl7_validation = set(self.policy.get("required_hl7_validation_signals") or [])
        required_hl7_transport = set(self.policy.get("required_hl7_transport_signals") or [])

        not_observed = sorted(
            (required_auth - set(auth_observed))
            | (required_audit - set(audit_observed))
            | (required_encryption - set(encryption_observed))
            | (required_hl7_validation - set(hl7_validation_observed))
            | (required_hl7_transport - set(hl7_transport_observed))
        )

        return {
            "auth_observed": auth_observed,
            "audit_observed": audit_observed,
            "encryption_observed": encryption_observed,
            "hl7_validation_observed": hl7_validation_observed,
            "hl7_transport_observed": hl7_transport_observed,
            "required_signals_not_observed": not_observed,
        }

    def _signal_findings(self, signal_report: dict) -> list[dict]:
        findings = []
        if signal_report["required_signals_not_observed"]:
            findings.append({
                "severity": "medium",
                "category": "signal_policy",
                "title": "Required compliance signals not observed in code",
                "evidence": f"Missing signals: {', '.join(signal_report['required_signals_not_observed'])}",
                "recommendation": "Verify these signals are truly required; if they are obsolete, remove them from the policy. If they are missing from code, add the required controls or update the policy after review.",
            })
        return findings

    # ------------------------------------------------------------------ #
    # PHI pattern analysis
    # ------------------------------------------------------------------ #

    def _analyze_phi_patterns(self, files: dict[str, str]) -> dict:
        all_text = "\n".join(files.values()).lower()
        policy_patterns = {p.lower() for p in self.policy.get("phi_field_patterns") or []}
        observed = {term for term in _KNOWN_PHI_TERMS if term in all_text}
        for pattern in policy_patterns:
            if pattern in all_text:
                observed.add(pattern)
        missing_from_policy = sorted(observed - policy_patterns)
        return {
            "observed": sorted(observed),
            "in_policy": sorted(policy_patterns),
            "missing_from_policy": missing_from_policy,
        }

    def _phi_findings(self, phi_report: dict) -> list[dict]:
        findings = []
        if phi_report["missing_from_policy"]:
            findings.append({
                "severity": "low",
                "category": "phi_policy",
                "title": "PHI-like terms observed but not in policy",
                "evidence": f"Observed terms: {', '.join(phi_report['missing_from_policy'])}",
                "recommendation": "Add relevant terms to phi_field_patterns so deterministic scanning covers them.",
            })
        return findings

    # ------------------------------------------------------------------ #
    # Scoring
    # ------------------------------------------------------------------ #

    def _compute_score(
        self,
        vendor_report: dict,
        signal_report: dict,
        phi_report: dict,
        findings: list[dict],
    ) -> int:
        score = 100
        if vendor_report["disallowed_hits"]:
            score -= 25
        if vendor_report["unlisted"]:
            score -= 10
        if vendor_report["stale_approved"]:
            score -= 5
        if signal_report["required_signals_not_observed"]:
            score -= 10
        if phi_report["missing_from_policy"]:
            score -= 5
        # Penalize high/medium findings
        for f in findings:
            if f["severity"] == "high":
                score -= 10
            elif f["severity"] == "medium":
                score -= 5
            elif f["severity"] == "low":
                score -= 2
        return max(0, min(100, score))


# ---------------------------------------------------------------------- #
# Convenience function
# ---------------------------------------------------------------------- #

def analyze_policy_health(
    repo: str,
    tree_lines: list[str],
    files: dict[str, str],
    policy: dict,
    latest_assessment_at: str | None = None,
    assessment_count: int = 0,
) -> dict:
    return PolicyHealthAnalyzer(policy).analyze(
        repo=repo,
        tree_lines=tree_lines,
        files=files,
        latest_assessment_at=latest_assessment_at,
        assessment_count=assessment_count,
    )


# ---------------------------------------------------------------------- #
# Shared extraction helpers (used by suggestion engine)
# ---------------------------------------------------------------------- #

def extract_manifest_packages(files: dict[str, str]) -> set[str]:
    """Return package names extracted from known dependency manifests."""
    packages: set[str] = set()
    for path, content in files.items():
        basename = path.rsplit("/", 1)[-1].lower()
        kind = _DEPENDENCY_MANIFESTS.get(basename)
        if not kind:
            continue
        try:
            if kind == "npm":
                packages.update(PolicyHealthAnalyzer._parse_package_json(content))
            elif kind == "pip":
                packages.update(PolicyHealthAnalyzer._parse_requirements_txt(content))
            elif kind == "python":
                packages.update(PolicyHealthAnalyzer._parse_pyproject_toml(content))
            elif kind == "go":
                packages.update(PolicyHealthAnalyzer._parse_go_mod(content))
            elif kind == "rust":
                packages.update(PolicyHealthAnalyzer._parse_cargo_toml(content))
            elif kind == "php":
                packages.update(PolicyHealthAnalyzer._parse_composer_json(content))
            elif kind == "ruby":
                packages.update(PolicyHealthAnalyzer._parse_gemfile(content))
        except Exception:
            continue
    return packages


def extract_code_vendors(files: dict[str, str]) -> set[str]:
    """Return vendor names observed in source URLs / hostnames."""
    vendors: set[str] = set()
    for content in files.values():
        for match in _VENDOR_URL_RE.finditer(content):
            host = match.group(1).lower()
            for known in _KNOWN_VENDORS:
                if known in host:
                    vendors.add(known)
    return vendors


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #

def _bare_package_names(list_fragment: str) -> set[str]:
    """Extract bare package names from a TOML/JSON list fragment like '"foo>=1", "bar"'."""
    names: set[str] = set()
    for token in re.findall(r'"([^"]+)"', list_fragment):
        token = token.strip()
        if not token or token.startswith("-"):
            continue
        # Strip extras and version specifiers.
        name = re.split(r"[\[\=\<\>\!\ ~]", token)[0].strip().lower()
        if name:
            names.add(name)
    return names
