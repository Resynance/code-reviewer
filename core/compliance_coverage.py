"""
compliance_coverage.py — compare deterministic vs. LLM compliance findings over
time to identify gaps in the deterministic scanner.

This is a read-only analyzer. It consumes the `compliance_review` payloads stored
in review/assessment history and reports categories where the LLM consistently
finds issues that the deterministic heuristics miss.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Iterable


# Categories that are meaningful for blind-spot analysis. These match the
# `category` field produced by core/compliance.py.
_MEANINGFUL_CATEGORIES = {
    "phi_logging",
    "test_data",
    "encryption",
    "access_control",
    "audit_trail",
    "third_party_baa",
    "manual_review",
    "hl7_logging",
    "hl7_transport",
    "hl7_validation",
    "hl7_manual_review",
}

_GAP_CATEGORY_BY_KEY = {
    "phi_exposure_risk": "phi_logging",
    "encryption_gaps": "encryption",
    "access_control_gaps": "access_control",
    "audit_trail_gaps": "audit_trail",
    "minimum_necessary_gaps": "manual_review",
    "third_party_baa_risks": "third_party_baa",
    "hl7_interface_gaps": "hl7_manual_review",
    "hl7_message_integrity_gaps": "hl7_validation",
    "hl7_transport_gaps": "hl7_transport",
}


class ComplianceCoverageAnalyzer:
    """Aggregate compliance findings and detect deterministic scanner blind spots."""

    def __init__(self, reviews: list[dict], assessments: list[dict]):
        self.reviews = reviews or []
        self.assessments = assessments or []

    def analyze(self, repo: str) -> dict:
        records = self._collect_records()
        categories: dict[str, dict[str, int]] = defaultdict(lambda: {"deterministic": 0, "llm": 0, "total": 0})
        trend_points: list[dict] = []

        for rec in records:
            compliance_review = rec.get("compliance_review") or {}
            deterministic_count = 0
            llm_count = 0
            for finding in self._findings_from(compliance_review):
                source = finding.get("source", "llm")
                category = self._normalize_category(finding.get("category", "general"))
                if category not in _MEANINGFUL_CATEGORIES:
                    category = "other"
                if source == "deterministic":
                    categories[category]["deterministic"] += 1
                    deterministic_count += 1
                else:
                    categories[category]["llm"] += 1
                    llm_count += 1
                categories[category]["total"] += 1

            trend_points.append({
                "date": rec.get("created_at", ""),
                "deterministic": deterministic_count,
                "llm": llm_count,
                "total": deterministic_count + llm_count,
            })

        blind_spots = self._detect_blind_spots(categories)
        coverage_score = self._compute_score(categories, blind_spots)
        summary = self._build_summary(categories, blind_spots, coverage_score)

        return {
            "repo": repo,
            "review_count": len(self.reviews),
            "assessment_count": len(self.assessments),
            "deterministic_count": sum(c["deterministic"] for c in categories.values()),
            "llm_count": sum(c["llm"] for c in categories.values()),
            "categories": dict(sorted(
                categories.items(),
                key=lambda item: item[1]["total"],
                reverse=True,
            )),
            "blind_spots": blind_spots,
            "trend": sorted(trend_points, key=lambda p: p["date"]),
            "coverage_score": coverage_score,
            "summary": summary,
        }

    # ------------------------------------------------------------------ #
    # Record normalization
    # ------------------------------------------------------------------ #

    def _collect_records(self) -> list[dict]:
        records = []
        for review in self.reviews:
            records.append({
                "created_at": review.get("created_at", ""),
                "compliance_review": review.get("compliance_review") or {},
            })
        for assessment in self.assessments:
            records.append({
                "created_at": assessment.get("created_at", ""),
                "compliance_review": assessment.get("compliance_review") or {},
            })
        return records

    def _findings_from(self, compliance_review: dict) -> list[dict]:
        findings = []
        seen_finding_keys = set()
        for key in ("hipaa_findings", "hl7_findings"):
            for item in compliance_review.get(key) or []:
                if isinstance(item, dict):
                    findings.append(item)
                    seen_finding_keys.add(self._finding_key(item))
        # LLM compliance output also has structured gap buckets. Gap entries can
        # be the only place a risk is recorded, so include gap-only items while
        # avoiding duplicate deterministic/LLM findings already counted above.
        for key, category in _GAP_CATEGORY_BY_KEY.items():
            for item in compliance_review.get(key) or []:
                if not isinstance(item, dict):
                    continue
                finding = self._finding_from_gap(item, category)
                if not finding:
                    continue
                if self._finding_key(finding) in seen_finding_keys:
                    continue
                findings.append(finding)
                seen_finding_keys.add(self._finding_key(finding))
        return findings

    def _finding_from_gap(self, item: dict, category: str) -> dict | None:
        title = str(item.get("summary") or "").strip()
        evidence = str(item.get("details") or title).strip()
        if not title and not evidence:
            return None
        return {
            "category": category,
            "title": title or evidence,
            "evidence": evidence or title,
            "file": str(item.get("file") or "").strip(),
            "source": str(item.get("source") or "llm").strip().lower() or "llm",
        }

    def _finding_key(self, item: dict) -> tuple[str, str, str]:
        title = str(item.get("title") or item.get("summary") or "").strip()
        evidence = str(item.get("evidence") or item.get("details") or title).strip()
        file = str(item.get("file") or "").strip()
        return title, evidence, file

    def _normalize_category(self, category: str) -> str:
        category = (category or "general").lower().strip()
        # Map the hl7_ prefixed gap buckets back to the finding categories.
        if category == "hl7_logging":
            return "hl7_logging"
        if category in ("hl7_transport", "hl7_interface"):
            return "hl7_transport"
        if category in ("hl7_validation", "hl7_message_integrity"):
            return "hl7_validation"
        if category == "hl7_manual_review":
            return "hl7_manual_review"
        return category

    # ------------------------------------------------------------------ #
    # Blind-spot detection
    # ------------------------------------------------------------------ #

    def _detect_blind_spots(self, categories: dict[str, dict[str, int]]) -> list[dict]:
        spots = []
        for category, counts in categories.items():
            deterministic = counts.get("deterministic", 0)
            llm = counts.get("llm", 0)
            if llm == 0:
                continue
            # Blind spot: LLM sees it but deterministic never does.
            if deterministic == 0:
                spots.append({
                    "category": category,
                    "llm_count": llm,
                    "deterministic_count": deterministic,
                    "severity": "high" if llm >= 3 else "medium",
                    "suggestion": _suggestion_for(category),
                })
            # LLM consistently sees more than deterministic — possible widening gap.
            elif llm >= deterministic * 2 and llm >= 3:
                spots.append({
                    "category": category,
                    "llm_count": llm,
                    "deterministic_count": deterministic,
                    "severity": "medium",
                    "suggestion": _suggestion_for(category),
                })

        return sorted(spots, key=lambda s: (s["severity"] != "high", -s["llm_count"]))

    # ------------------------------------------------------------------ #
    # Scoring & summary
    # ------------------------------------------------------------------ #

    def _compute_score(self, categories: dict[str, dict[str, int]], blind_spots: list[dict]) -> int:
        total = sum(c["total"] for c in categories.values())
        if total == 0:
            return 0
        deterministic = sum(c["deterministic"] for c in categories.values())
        # Base score from deterministic catch rate.
        score = int((deterministic / total) * 100)
        # Penalize blind spots.
        for spot in blind_spots:
            if spot["severity"] == "high":
                score -= 15
            elif spot["severity"] == "medium":
                score -= 8
        return max(0, min(100, score))

    def _build_summary(
        self,
        categories: dict[str, dict[str, int]],
        blind_spots: list[dict],
        score: int,
    ) -> str:
        total = sum(c["total"] for c in categories.values())
        if total == 0:
            return "No compliance findings recorded yet. Run a compliance-enabled review or assessment to build coverage data."

        deterministic = sum(c["deterministic"] for c in categories.values())
        llm = sum(c["llm"] for c in categories.values())
        parts = [
            f"Coverage score {score}/100 across {total} findings "
            f"({deterministic} deterministic, {llm} LLM)."
        ]
        if blind_spots:
            parts.append(
                f"Detected {len(blind_spots)} potential scanner blind spot(s): "
                f"{', '.join(s['category'] for s in blind_spots)}."
            )
        else:
            parts.append("Deterministic scanner appears aligned with LLM findings; no clear blind spots.")
        return " ".join(parts)


# ---------------------------------------------------------------------- #
# Convenience function
# ---------------------------------------------------------------------- #

def analyze_coverage(
    repo: str,
    reviews: list[dict],
    assessments: list[dict],
) -> dict:
    return ComplianceCoverageAnalyzer(reviews, assessments).analyze(repo)


# ---------------------------------------------------------------------- #
# Suggestion catalog
# ---------------------------------------------------------------------- #

def _suggestion_for(category: str) -> str:
    suggestions = {
        "phi_logging": "Add a deterministic check for PHI terms near logging statements.",
        "test_data": "Add heuristics that flag PHI in fixtures, seeds, or sample data.",
        "encryption": "Look for HTTP/network usage near PHI terms without encryption markers.",
        "access_control": "Detect PHI-relevant routes that lack configured auth signals.",
        "audit_trail": "Flag PHI write/update/delete paths missing configured audit signals.",
        "third_party_baa": "Expand vendor regexes or approved/disallowed vendor lists.",
        "manual_review": "When PHI is in scope but no violation is proven, raise a deterministic manual-review item.",
        "hl7_logging": "Detect raw HL7/FHIR payloads in logs or debug output.",
        "hl7_transport": "Check HL7 transport handling for missing secure-channel markers.",
        "hl7_validation": "Flag HL7 parsing without validation, ACK/NACK, or idempotency signals.",
        "hl7_manual_review": "Add a deterministic manual-review item when HL7 concepts are present.",
        "other": "Review LLM findings in this category for reproducible signals to add to the deterministic scanner.",
    }
    return suggestions.get(category, "Review LLM findings and add matching deterministic signals.")
