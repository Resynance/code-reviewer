"""
compliance_analysis.py — orchestrate compliance health, coverage, and policy
suggestion analysis for a repository.

This module ties together:
  - GitHub repo content fetching (via assessment_engine)
  - configured compliance policy (via config_store)
  - review/assessment history (via review_store / assessment_store)
  - the focused analyzers in compliance_policy_health, compliance_coverage,
    and compliance_policy_suggestions
"""

from __future__ import annotations

import copy

import config_store
import review_store
import assessment_store
from assessment_engine import AssessmentEngine

import compliance
import compliance_policy_health
import compliance_coverage
import compliance_policy_suggestions


class ComplianceAnalysis:
    """Run all compliance-analysis features for a single repo."""

    def __init__(self, repo: str):
        self.repo = repo
        self._engine = AssessmentEngine()

    def dashboard(self, history_limit: int = 50) -> dict:
        """Return a unified dashboard payload for the repo."""
        tree_lines, files = self._fetch_repo_content()
        policy = config_store.get_compliance_policy(self.repo)
        assessments = assessment_store.list_assessments(repo=self.repo, limit=history_limit)
        reviews = review_store.list_reviews(repo=self.repo, limit=history_limit)

        latest_assessment_at = ""
        if assessments:
            latest_assessment_at = assessments[0].get("created_at", "")

        health = compliance_policy_health.analyze_policy_health(
            repo=self.repo,
            tree_lines=tree_lines,
            files=files,
            policy=policy,
            latest_assessment_at=latest_assessment_at,
            assessment_count=len(assessments),
        )

        coverage = compliance_coverage.analyze_coverage(
            repo=self.repo,
            reviews=reviews,
            assessments=assessments,
        )

        prior_files = self._prior_assessment_files(assessments)
        suggestions = compliance_policy_suggestions.suggest_policy_updates(
            repo=self.repo,
            policy=policy,
            current_files=files,
            prior_files=prior_files,
        )

        return {
            "repo": self.repo,
            "policy_enabled": bool(policy.get("enabled")),
            "health": health,
            "coverage": coverage,
            "suggestions": suggestions,
        }

    def health(self) -> dict:
        """Return only the policy-health report."""
        tree_lines, files = self._fetch_repo_content()
        policy = config_store.get_compliance_policy(self.repo)
        assessments = assessment_store.list_assessments(repo=self.repo, limit=1)
        latest_assessment_at = assessments[0].get("created_at", "") if assessments else ""
        assessment_count = len(assessment_store.list_assessments(repo=self.repo, limit=10000))
        return compliance_policy_health.analyze_policy_health(
            repo=self.repo,
            tree_lines=tree_lines,
            files=files,
            policy=policy,
            latest_assessment_at=latest_assessment_at,
            assessment_count=assessment_count,
        )

    def coverage(self, limit: int = 50) -> dict:
        """Return only the coverage report."""
        reviews = review_store.list_reviews(repo=self.repo, limit=limit)
        assessments = assessment_store.list_assessments(repo=self.repo, limit=limit)
        return compliance_coverage.analyze_coverage(
            repo=self.repo,
            reviews=reviews,
            assessments=assessments,
        )

    def suggestions(self) -> list[dict]:
        """Return only the policy update suggestions."""
        tree_lines, files = self._fetch_repo_content()
        policy = config_store.get_compliance_policy(self.repo)
        assessments = assessment_store.list_assessments(repo=self.repo, limit=10)
        prior_files = self._prior_assessment_files(assessments)
        return compliance_policy_suggestions.suggest_policy_updates(
            repo=self.repo,
            policy=policy,
            current_files=files,
            prior_files=prior_files,
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _fetch_repo_content(self):
        owner = self.repo.split("/")[0] if "/" in self.repo else self.repo
        token = config_store.get_token_for(owner)
        if not token:
            raise ValueError("No GitHub token configured for this repo's owner")
        return self._engine._fetch_repo_content(self.repo, token)

    def _prior_assessment_files(self, assessments: list[dict]) -> dict[str, str] | None:
        """Return file contents from the most recent prior assessment, if any.

        Assessments currently do not persist full file contents, so we fall back
        to an empty baseline. In the future this can be extended to store a
        content snapshot alongside each assessment.
        """
        return None


# ---------------------------------------------------------------------- #
# Convenience functions
# ---------------------------------------------------------------------- #

def get_dashboard(repo: str, history_limit: int = 50) -> dict:
    return ComplianceAnalysis(repo).dashboard(history_limit=history_limit)


def get_health(repo: str) -> dict:
    return ComplianceAnalysis(repo).health()


def get_coverage(repo: str, limit: int = 50) -> dict:
    return ComplianceAnalysis(repo).coverage(limit=limit)


def get_suggestions(repo: str) -> list[dict]:
    return ComplianceAnalysis(repo).suggestions()


def apply_suggestion(repo: str, suggestion: dict) -> dict:
    """Apply a single suggestion to the repo's compliance policy and persist it.

    Returns the updated compliance_policies object.
    """
    policies = config_store.get_compliance_policies()
    repo_policy = compliance.policy_for_repo(policies, repo)
    updated_repo_policy = compliance_policy_suggestions.apply_suggestion(repo_policy, suggestion)

    # Merge the updated repo policy back into the full policies object.
    next_policies = copy.deepcopy(policies)
    if "repos" not in next_policies or not isinstance(next_policies["repos"], dict):
        next_policies["repos"] = {}
    next_policies["repos"][repo] = updated_repo_policy

    config_store.save_config({"compliance_policies": next_policies})
    return config_store.get_compliance_policies()
