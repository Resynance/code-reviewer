"""
hipaa.py — deterministic HIPAA-focused heuristics and policy helpers.

This module does not attempt to certify compliance. It provides:
  - repo/default policy normalization
  - lightweight deterministic scans for obvious PHI/compliance risks
  - prompt context for the LLM
  - normalization/bucketing for the structured HIPAA response
"""

from __future__ import annotations

import copy
import re
from typing import Iterable


DEFAULT_POLICY = {
    "notes": "",
    "approved_vendors": [],
    "disallowed_vendors": [],
    "required_auth_signals": ["require_user", "Depends(require_user)", "@login_required"],
    "required_audit_signals": ["audit", "audit_log", "audit trail", "access_log"],
    "required_encryption": ["at_rest", "in_transit"],
    "phi_field_patterns": [],
}

_PHI_TERMS = (
    "patient",
    "phi",
    "hipaa",
    "medical_record",
    "mrn",
    "diagnosis",
    "treatment",
    "dob",
    "date_of_birth",
    "ssn",
    "social_security",
    "member_id",
    "insurance",
    "claim",
    "prescription",
    "lab_result",
)

_EXTERNAL_VENDOR_RE = re.compile(
    r"(?:https?://|['\"])([^'\"\s]+(?:sentry|segment|mixpanel|amplitude|slack|openai|anthropic|"
    r"posthog|datadog|newrelic|mailgun|sendgrid|twilio)[^'\"\s]*)",
    re.IGNORECASE,
)
_ROUTE_RE = re.compile(r"@(app|router)\.(get|post|put|patch|delete)\([^)]*\)", re.IGNORECASE)
_HTTP_RE = re.compile(r"https?://", re.IGNORECASE)
_ENCRYPTION_RE = re.compile(r"\b(ssl|tls|encrypt|encrypted|kms|fernet|aes)\b", re.IGNORECASE)
_LOG_RE = re.compile(r"\b(log(?:ger)?\.(?:info|debug|warning|error)|print\(|console\.log)\b", re.IGNORECASE)
_AUTH_RE = re.compile(r"\b(auth|authorize|permission|scope|require_user|Depends\(require_user\)|jwt)\b", re.IGNORECASE)
_AUDIT_RE = re.compile(r"\b(audit|audit_log|access_log|history|event_log)\b", re.IGNORECASE)


def default_policies() -> dict:
    return {"default": copy.deepcopy(DEFAULT_POLICY), "repos": {}}


def normalize_policies(raw) -> dict:
    out = default_policies()
    if not isinstance(raw, dict):
        return out
    out["default"] = _normalize_policy(raw.get("default"))
    repos = raw.get("repos")
    if isinstance(repos, dict):
        normalized = {}
        for repo, policy in repos.items():
            if isinstance(repo, str) and repo.strip():
                normalized[repo.strip()] = _normalize_policy(policy)
        out["repos"] = normalized
    return out


def policy_for_repo(policies: dict, repo: str) -> dict:
    normalized = normalize_policies(policies)
    merged = copy.deepcopy(normalized["default"])
    repo_policy = normalized["repos"].get(repo, {})
    for key, value in repo_policy.items():
        merged[key] = value
    return _normalize_policy(merged)


def review_findings(diff: str, files_changed: Iterable[str], policy: dict) -> dict:
    path_lines = [f"+++ b/{p}" for p in (files_changed or [])]
    text = "\n".join(path_lines + [diff or ""])
    return _scan_text(text, files_changed or [], policy, mode="review")


def assessment_findings(tree_lines: list, files: dict, policy: dict) -> dict:
    chunks = ["\n".join(tree_lines or [])]
    file_paths = []
    for path, content in (files or {}).items():
        file_paths.append(path)
        chunks.append(f"--- {path} ---\n{content}")
    return _scan_text("\n\n".join(chunks), file_paths, policy, mode="assessment")


def prompt_section(policy: dict, deterministic: dict) -> str:
    notes = policy.get("notes", "").strip() or "(none)"
    approved = ", ".join(policy.get("approved_vendors") or []) or "(none listed)"
    disallowed = ", ".join(policy.get("disallowed_vendors") or []) or "(none listed)"
    auth = ", ".join(policy.get("required_auth_signals") or []) or "(none listed)"
    audit = ", ".join(policy.get("required_audit_signals") or []) or "(none listed)"
    findings = deterministic.get("hipaa_findings") or []
    if findings:
        finding_lines = []
        for item in findings:
            finding_lines.append(
                f"- [{item['severity']}] {item['title']} ({item['category']})"
                f"\n  evidence: {item['evidence']}"
                f"\n  recommendation: {item['recommendation']}"
            )
        findings_block = "\n".join(finding_lines)
    else:
        findings_block = "(no deterministic HIPAA findings)"
    return (
        "\n## HIPAA Review Mode\n"
        "This is a HIPAA-focused review, not a certification. Distinguish between:\n"
        "- evidence-backed code-level violations\n"
        "- HIPAA-relevant changes that require manual compliance review\n"
        "- low-confidence possibilities that should be omitted\n\n"
        "Severity guidance:\n"
        "- Use critical/high only for direct evidence of unsafe PHI handling or missing protections.\n"
        "- Use medium for concrete risk gaps needing remediation.\n"
        "- Use low only for minor hygiene issues.\n"
        "- Mark vendor/BAA uncertainty and policy-process checks as manual-review items unless the code proves a direct violation.\n\n"
        "Repo HIPAA policy context:\n"
        f"- Notes: {notes}\n"
        f"- Approved vendors: {approved}\n"
        f"- Disallowed vendors: {disallowed}\n"
        f"- Required auth signals: {auth}\n"
        f"- Required audit signals: {audit}\n\n"
        "Deterministic HIPAA findings from static heuristics:\n"
        f"{findings_block}\n"
    )


def normalize_result(section: dict | None, deterministic: dict | None, *, enabled: bool) -> dict:
    base = {
        "enabled": enabled,
        "hipaa_relevant": False,
        "requires_manual_compliance_review": False,
        "summary": "",
        "policy_notes_applied": [],
        "hipaa_findings": [],
        "phi_exposure_risk": [],
        "encryption_gaps": [],
        "access_control_gaps": [],
        "audit_trail_gaps": [],
        "minimum_necessary_gaps": [],
        "third_party_baa_risks": [],
    }
    if isinstance(section, dict):
        for key in base:
            if key in section:
                base[key] = section[key]

    merged_findings = []
    for source, items in (("deterministic", (deterministic or {}).get("hipaa_findings") or []),
                          ("llm", base.get("hipaa_findings") or [])):
        for item in items:
            finding = _normalize_finding(item, source=source)
            if finding:
                merged_findings.append(finding)
    deduped = []
    seen = set()
    for item in merged_findings:
        key = (item["category"], item["title"], item.get("file", ""), item["evidence"])
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    base["hipaa_findings"] = deduped
    for key in (
        "phi_exposure_risk",
        "encryption_gaps",
        "access_control_gaps",
        "audit_trail_gaps",
        "minimum_necessary_gaps",
        "third_party_baa_risks",
    ):
        base[key] = _normalize_gap_list(base.get(key))

    if deterministic:
        for key in (
            "phi_exposure_risk",
            "encryption_gaps",
            "access_control_gaps",
            "audit_trail_gaps",
            "minimum_necessary_gaps",
            "third_party_baa_risks",
        ):
            base[key] = _merge_gap_lists(base[key], deterministic.get(key) or [])
        base["hipaa_relevant"] = bool(
            base["hipaa_relevant"]
            or deterministic.get("hipaa_relevant")
            or base["hipaa_findings"]
        )
        base["requires_manual_compliance_review"] = bool(
            base["requires_manual_compliance_review"]
            or deterministic.get("requires_manual_compliance_review")
            or any(item.get("manual_review") for item in base["hipaa_findings"])
        )
    return base


def review_issue_overlays(hipaa_result: dict, *, enabled: bool) -> list:
    if not enabled:
        return []
    overlays = []
    for item in hipaa_result.get("hipaa_findings", []):
        if item["severity"] not in {"critical", "high", "medium"}:
            continue
        overlays.append({
            "severity": item["severity"],
            "file": item.get("file") or "compliance",
            "description": item["title"],
            "suggestion": item["recommendation"],
        })
    return overlays


def assessment_vulnerability_overlays(hipaa_result: dict, *, enabled: bool) -> list:
    if not enabled:
        return []
    overlays = []
    for item in hipaa_result.get("hipaa_findings", []):
        if item["severity"] not in {"critical", "high", "medium"}:
            continue
        overlays.append({
            "severity": item["severity"],
            "title": item["title"],
            "description": item["evidence"],
            "recommendation": item["recommendation"],
        })
    return overlays


def _normalize_policy(raw) -> dict:
    policy = copy.deepcopy(DEFAULT_POLICY)
    if not isinstance(raw, dict):
        return policy
    for key in ("notes",):
        if isinstance(raw.get(key), str):
            policy[key] = raw[key].strip()
    for key in (
        "approved_vendors",
        "disallowed_vendors",
        "required_auth_signals",
        "required_audit_signals",
        "required_encryption",
        "phi_field_patterns",
    ):
        values = raw.get(key)
        if isinstance(values, list):
            policy[key] = [str(v).strip() for v in values if str(v).strip()]
    return policy


def _scan_text(text: str, file_paths: Iterable[str], policy: dict, *, mode: str) -> dict:
    findings = []
    buckets = {
        "phi_exposure_risk": [],
        "encryption_gaps": [],
        "access_control_gaps": [],
        "audit_trail_gaps": [],
        "minimum_necessary_gaps": [],
        "third_party_baa_risks": [],
    }
    paths = list(file_paths or [])
    lower_text = text.lower()
    all_phi_terms = list(dict.fromkeys([*_PHI_TERMS, *(t.lower() for t in policy.get("phi_field_patterns") or [])]))

    def add(category, severity, title, evidence, recommendation, *, file="", manual_review=False, bucket=None):
        finding = {
            "category": category,
            "severity": severity,
            "title": title,
            "evidence": evidence,
            "recommendation": recommendation,
            "file": file,
            "manual_review": manual_review,
            "source": "deterministic",
        }
        findings.append(finding)
        if bucket:
            buckets[bucket] = _merge_gap_lists(buckets[bucket], [{"summary": title, "details": evidence, "file": file}])

    def evidence_for(patterns, preferred_paths=None):
        pref = preferred_paths or paths
        for path in pref:
            if any(p in path.lower() for p in patterns):
                return path
        return ""

    for line in text.splitlines():
        ll = line.lower()
        if _LOG_RE.search(ll) and any(term in ll for term in all_phi_terms):
            add(
                "phi_logging",
                "critical",
                "Potential PHI in logs or debug output",
                f"Observed logging statement with PHI-like terms: {line.strip()[:180]}",
                "Remove PHI from logs and add explicit redaction for healthcare payloads.",
                file=evidence_for(["log", "app", "api"]),
                bucket="phi_exposure_risk",
            )
        if any(t in ll for t in ("fixture", "seed", "sample", "demo", "test")) and any(term in ll for term in all_phi_terms):
            add(
                "test_data",
                "high",
                "Possible PHI in fixtures or non-production seed data",
                f"Found test/seed context with PHI-like terms: {line.strip()[:180]}",
                "Replace with synthetic data and document that production PHI is never copied into lower environments.",
                file=evidence_for(["test", "fixture", "seed", "demo", "sample"]),
                bucket="phi_exposure_risk",
            )
        if _HTTP_RE.search(ll) and any(term in ll for term in all_phi_terms) and not _ENCRYPTION_RE.search(ll):
            add(
                "encryption",
                "high",
                "Possible PHI transmitted without clear encryption controls",
                f"Observed HTTP or network handling near PHI-like terms: {line.strip()[:180]}",
                "Require TLS transport and explicit encryption for PHI in transit and at rest.",
                file=evidence_for(["api", "client", "service"]),
                bucket="encryption_gaps",
            )
        if _ROUTE_RE.search(ll) and any(term in ll for term in all_phi_terms) and not _AUTH_RE.search(lower_text):
            add(
                "access_control",
                "high" if mode == "review" else "medium",
                "HIPAA-relevant route lacks obvious access control signal",
                f"Detected a PHI-relevant route without nearby auth markers: {line.strip()[:180]}",
                "Add explicit authorization middleware/dependencies and scope checks for PHI access.",
                file=evidence_for(["api", "route", "controller"]),
                bucket="access_control_gaps",
            )
        if any(term in ll for term in all_phi_terms) and any(word in ll for word in ("update", "delete", "create", "write")) and not _AUDIT_RE.search(lower_text):
            add(
                "audit_trail",
                "medium",
                "PHI modification path lacks obvious audit trail signal",
                f"Detected PHI-related write behavior without visible audit instrumentation: {line.strip()[:180]}",
                "Record access and modification events for PHI-bearing operations.",
                file=evidence_for(["api", "service", "handler"]),
                bucket="audit_trail_gaps",
            )

    if any(term in lower_text for term in all_phi_terms) and not findings:
        add(
            "manual_review",
            "medium",
            "HIPAA-relevant data appears in scope",
            "The code references healthcare or patient-data concepts, but deterministic checks did not prove a direct violation.",
            "Run a manual compliance review for data classification, retention, and operational controls.",
            manual_review=True,
            bucket="minimum_necessary_gaps",
        )

    for match in _EXTERNAL_VENDOR_RE.finditer(text):
        host = match.group(1)
        lowered = host.lower()
        approved = [v.lower() for v in policy.get("approved_vendors") or []]
        disallowed = [v.lower() for v in policy.get("disallowed_vendors") or []]
        if any(v in lowered for v in disallowed):
            add(
                "third_party_baa",
                "high",
                "Disallowed third-party vendor appears in HIPAA review scope",
                f"Observed external integration matching a disallowed vendor policy: {host}",
                "Remove the integration or document an approved replacement before handling PHI.",
                manual_review=True,
                bucket="third_party_baa_risks",
            )
        elif approved and not any(v in lowered for v in approved):
            add(
                "third_party_baa",
                "medium",
                "Third-party integration is not in the approved HIPAA vendor list",
                f"Observed external integration that needs BAA/policy review: {host}",
                "Confirm BAA coverage and approved-vendor status before sending PHI to this service.",
                manual_review=True,
                bucket="third_party_baa_risks",
            )

    return {
        "hipaa_relevant": bool(findings),
        "requires_manual_compliance_review": any(item.get("manual_review") for item in findings),
        "hipaa_findings": findings,
        **buckets,
    }


def _normalize_finding(item, *, source: str):
    if not isinstance(item, dict):
        return None
    title = str(item.get("title") or "").strip()
    evidence = str(item.get("evidence") or "").strip()
    if not title or not evidence:
        return None
    severity = str(item.get("severity") or "medium").strip().lower()
    if severity not in {"critical", "high", "medium", "low"}:
        severity = "medium"
    category = str(item.get("category") or "general").strip().lower()
    return {
        "category": category,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "recommendation": str(item.get("recommendation") or "Review and remediate this HIPAA risk.").strip(),
        "file": str(item.get("file") or "").strip(),
        "manual_review": bool(item.get("manual_review", False)),
        "source": source,
    }


def _normalize_gap_list(items) -> list:
    out = []
    for item in items or []:
        if isinstance(item, str):
            txt = item.strip()
            if txt:
                out.append({"summary": txt, "details": txt, "file": ""})
        elif isinstance(item, dict):
            summary = str(item.get("summary") or "").strip()
            details = str(item.get("details") or summary).strip()
            if summary or details:
                out.append({"summary": summary or details, "details": details, "file": str(item.get("file") or "").strip()})
    return out


def _merge_gap_lists(existing: list, extra: list) -> list:
    merged = list(existing or [])
    seen = {(item.get("summary"), item.get("details"), item.get("file")) for item in merged if isinstance(item, dict)}
    for item in _normalize_gap_list(extra):
        key = (item.get("summary"), item.get("details"), item.get("file"))
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged
