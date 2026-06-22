"""
review_engine.py — LLM orchestration for AI code review.

Given a PR diff, the engine:
  1. Retrieves semantically-similar past decisions from the decision store.
  2. Builds a prompt that grounds the review in those decisions.
  3. Asks the model to return a structured review (issues, suggestions, verdict)
     via a forced function/tool call, so the output is always machine-parseable.

The model is served through OpenRouter (https://openrouter.ai), an
OpenAI-compatible gateway, so any model OpenRouter offers can be used by setting
OPENROUTER_MODEL. Authentication uses OPENROUTER_API_KEY.
"""

import os
import json
from dataclasses import dataclass, field
from typing import Optional

import config_store
import compliance


OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# How many past decisions to pull in as context for each review.
DEFAULT_CONTEXT_K = 6


@dataclass
class ReviewRequest:
    pr_number: int
    repo: str
    title: str
    diff: str
    description: str = ""
    author: str = "unknown"
    base_branch: str = "main"
    files_changed: list = field(default_factory=list)
    # Per-request model override. When set, takes precedence over the engine's
    # model_override and the config-store default, so the frontend can select
    # which configured model slot to run against.
    model: Optional[str] = None
    provider: Optional[str] = None
    compliance: bool = False
    agentic: bool = False
    agent_sources: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    pr_number: int
    summary: str
    approved: bool
    confidence: float
    issues: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)
    past_decisions_applied: list = field(default_factory=list)
    compliance_review: dict = field(default_factory=dict)
    model: str = ""


# JSON schema for the structured review. The model is forced to call the
# submit_review function with arguments matching this, guaranteeing a
# machine-parseable payload instead of free-form prose.
_ISSUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "severity": {
            "type": "string",
            "enum": ["critical", "high", "medium", "low"],
        },
        "file": {"type": "string"},
        "description": {"type": "string"},
        "suggestion": {"type": "string"},
        "past_decision_ref": {
            "type": "string",
            "description": "Ref of a past decision that informed this, if any.",
        },
    },
    "required": ["severity", "file", "description", "suggestion", "past_decision_ref"],
}

_SUGGESTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "type": {
            "type": "string",
            "enum": [
                "security",
                "performance",
                "architecture",
                "style",
                "test_coverage",
            ],
        },
        "description": {"type": "string"},
        "past_decision_ref": {"type": "string"},
    },
    "required": ["type", "description", "past_decision_ref"],
}

_PAST_DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "ref": {"type": "string"},
        "summary": {"type": "string"},
        "how_applied": {
            "type": "string",
            "description": "How this past decision was applied to the current PR.",
        },
    },
    "required": ["ref", "summary", "how_applied"],
}

_COMPLIANCE_FINDING_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "category": {"type": "string"},
        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
        "title": {"type": "string"},
        "evidence": {"type": "string"},
        "recommendation": {"type": "string"},
        "file": {"type": "string"},
        "manual_review": {"type": "boolean"},
    },
    "required": ["category", "severity", "title", "evidence", "recommendation", "file", "manual_review"],
}

_COMPLIANCE_GAP_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "details": {"type": "string"},
        "file": {"type": "string"},
    },
    "required": ["summary", "details", "file"],
}

_COMPLIANCE_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "description": "HIPAA / HL7-focused findings. Use this only when healthcare compliance review mode is enabled.",
    "properties": {
        "hipaa_relevant": {"type": "boolean"},
        "hl7_relevant": {"type": "boolean"},
        "requires_manual_compliance_review": {"type": "boolean"},
        "summary": {"type": "string"},
        "policy_notes_applied": {"type": "array", "items": {"type": "string"}},
        "hipaa_findings": {"type": "array", "items": _COMPLIANCE_FINDING_SCHEMA},
        "hl7_findings": {"type": "array", "items": _COMPLIANCE_FINDING_SCHEMA},
        "phi_exposure_risk": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "encryption_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "access_control_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "audit_trail_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "minimum_necessary_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "third_party_baa_risks": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "hl7_interface_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "hl7_message_integrity_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
        "hl7_transport_gaps": {"type": "array", "items": _COMPLIANCE_GAP_SCHEMA},
    },
    "required": [
        "hipaa_relevant",
        "hl7_relevant",
        "requires_manual_compliance_review",
        "summary",
        "policy_notes_applied",
        "hipaa_findings",
        "hl7_findings",
        "phi_exposure_risk",
        "encryption_gaps",
        "access_control_gaps",
        "audit_trail_gaps",
        "minimum_necessary_gaps",
        "third_party_baa_risks",
        "hl7_interface_gaps",
        "hl7_message_integrity_gaps",
        "hl7_transport_gaps",
    ],
}

_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": "One or two sentence overall assessment of the PR.",
        },
        "approved": {
            "type": "boolean",
            "description": "True if the PR is safe to merge as-is.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in this assessment, from 0.0 to 1.0.",
        },
        "issues": {
            "type": "array",
            "description": "Concrete problems found in the diff.",
            "items": _ISSUE_SCHEMA,
        },
        "suggestions": {
            "type": "array",
            "description": "Non-blocking improvements.",
            "items": _SUGGESTION_SCHEMA,
        },
        "past_decisions_applied": {
            "type": "array",
            "description": "Past decisions that meaningfully shaped this review.",
            "items": _PAST_DECISION_SCHEMA,
        },
        "compliance_review": _COMPLIANCE_REVIEW_SCHEMA,
    },
    "required": ["summary", "approved", "confidence", "issues", "suggestions", "past_decisions_applied", "compliance_review"],
}

# OpenAI-compatible function tool wrapping the schema above.
_REVIEW_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_review",
        "description": "Submit the structured result of a code review.",
        "parameters": _REVIEW_SCHEMA,
    },
}

_SYSTEM_PROMPT = """You are a senior code reviewer for an engineering team.
Review the pull request diff for correctness, security, performance, architecture,
and test coverage. Be specific and reference exact files.

You are given relevant past decisions the team has made on earlier PRs and ADRs.
When a past decision applies to the current change, follow that precedent and cite
its ref. Report it in past_decisions_applied so the author sees the connection.

Call the submit_review function exactly once with your structured findings. Set
approved=false if there is any critical or high-severity issue.
If tool calling is unavailable, return a raw JSON object matching the
submit_review schema and nothing else.

If HIPAA / HL7 review mode is enabled, use the compliance_review object for evidence-backed
healthcare-compliance findings. Do not claim legal certification. Mark
requires_manual_compliance_review=true when the change is HIPAA-relevant but the
remaining decision depends on organizational process, BAA status, or deployment
controls that are not provable from code alone. For HL7, also mark
requires_manual_compliance_review=true when interface-partner requirements,
message contracts, or transport assumptions are not provable from code alone."""


class CodeReviewEngine:
    def __init__(self, store, model: Optional[str] = None, context_k: int = DEFAULT_CONTEXT_K):
        from openai import OpenAI

        self._store = store
        # Explicit override (mainly for tests). When None, the model and provider
        # are resolved from config_store per review, so UI changes take effect
        # without restarting the server.
        self._model_override = model
        self._context_k = context_k
        # OpenRouter is OpenAI-compatible; point the OpenAI SDK at its base URL.
        # The optional headers populate OpenRouter's app-attribution rankings.
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost:1500"),
                "X-Title": "ReviewBot",
            },
            # Fail with a clear error inside the serverless function limit
            # (vercel.json maxDuration=300) rather than letting the platform kill
            # a slow model call with an opaque 504. max_retries=0 is essential:
            # the SDK's default retries would re-fire a timed-out call and push
            # total time past the limit anyway.
            timeout=240,
            max_retries=0,
        )

    def review(self, request: ReviewRequest) -> ReviewResult:
        decisions = self._retrieve_context(request)
        prompt = self._build_prompt(request, decisions)

        model = request.model or self._model_override or config_store.get_model()
        kwargs = dict(
            model=model,
            # Enough for a thorough structured review; kept modest so generation
            # finishes well inside the serverless function window.
            max_tokens=2500,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=[_REVIEW_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_review"}},
        )

        # Optionally pin OpenRouter to a specific upstream provider.
        provider = request.provider if request.provider is not None else config_store.get_provider()
        if provider:
            kwargs["extra_body"] = {
                "provider": {"order": [provider], "allow_fallbacks": False}
            }

        response = self._create_review_completion(kwargs)

        payload = self._extract_tool_input(response)
        result = self._to_result(request, payload, decisions)
        result.model = model
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    @staticmethod
    def _should_retry_without_tools(exc: Exception) -> bool:
        details = []
        for value in (
            str(exc),
            getattr(exc, "message", None),
            getattr(exc, "body", None),
            getattr(getattr(exc, "response", None), "text", None),
        ):
            if value:
                details.append(str(value).lower())
        haystack = "\n".join(details)
        unsupported_markers = (
            "tool_choice",
            "tools are not supported",
            "tool use is not supported",
            "does not support tools",
            "doesn't support tools",
            "function calling is not supported",
            "tool calling is not supported",
            "unsupported tools",
            "unsupported parameter: tools",
            "unsupported parameter: tool_choice",
        )
        return any(marker in haystack for marker in unsupported_markers)

    def _create_review_completion(self, kwargs: dict):
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            if "tools" not in kwargs or not self._should_retry_without_tools(exc):
                raise
            retry_kwargs = dict(kwargs)
            retry_kwargs.pop("tools", None)
            retry_kwargs.pop("tool_choice", None)
            return self._client.chat.completions.create(**retry_kwargs)

    def _retrieve_context(self, request: ReviewRequest):
        # Search the decision store using the PR's intent and touched files.
        query_parts = [request.title, request.description]
        query_parts.extend(request.files_changed or [])
        query = "\n".join(p for p in query_parts if p) or request.title
        try:
            # Ground the review in this repo's precedent plus global decisions.
            return self._store.retrieve(
                query,
                k=self._context_k,
                repo=request.repo or None,
                include_global=True,
            )
        except Exception:
            # A missing/empty store should never block a review.
            return []

    def _build_prompt(self, request: ReviewRequest, decisions) -> str:
        if decisions:
            decision_lines = []
            for d in decisions:
                decision_lines.append(
                    f"- {d.get('ref', '?')} ({d.get('outcome', 'unknown')}): "
                    f"{d.get('summary', '')}\n  reasoning: {d.get('reasoning', '')}"
                )
            decisions_block = "\n".join(decision_lines)
        else:
            decisions_block = "(no relevant past decisions found)"

        files = ", ".join(request.files_changed) or "(not specified)"
        compliance_section = ""
        if request.compliance:
            policy = config_store.get_compliance_policy(request.repo)
            deterministic = compliance.review_findings(request.diff, request.files_changed, policy)
            compliance_section = compliance.prompt_section(policy, deterministic)
        return (
            f"# Pull Request #{request.pr_number} — {request.repo}\n"
            f"Title: {request.title}\n"
            f"Author: {request.author}\n"
            f"Base branch: {request.base_branch}\n"
            f"Files changed: {files}\n\n"
            f"## Description\n{request.description or '(none)'}\n\n"
            f"## Relevant past decisions\n{decisions_block}\n"
            f"{compliance_section}"
            f"\n## Diff\n```diff\n{request.diff}\n```\n"
        )

    @staticmethod
    def _escape_json_string_controls(text: str) -> str:
        out = []
        in_string = False
        escape = False
        for ch in text:
            if in_string:
                if escape:
                    out.append(ch)
                    escape = False
                    continue
                if ch == "\\":
                    out.append(ch)
                    escape = True
                    continue
                if ch == '"':
                    out.append(ch)
                    in_string = False
                    continue
                if ch == "\n":
                    out.append("\\n")
                    continue
                if ch == "\r":
                    out.append("\\r")
                    continue
                if ch == "\t":
                    out.append("\\t")
                    continue
                out.append(ch)
                continue
            out.append(ch)
            if ch == '"':
                in_string = True
        return "".join(out)

    @classmethod
    def _parse_json_payload(cls, text: str) -> dict:
        candidate = (text or "").strip()
        if not candidate:
            raise RuntimeError("Model returned empty content.")
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

        escaped = cls._escape_json_string_controls(candidate)
        if escaped != candidate:
            try:
                return json.loads(escaped)
            except json.JSONDecodeError:
                pass

        if "```" in candidate:
            for block in candidate.split("```"):
                block = block.strip()
                if not block:
                    continue
                if "\n" in block:
                    first, rest = block.split("\n", 1)
                    if first.strip().lower() in {"json", "javascript", "js"}:
                        try:
                            return json.loads(rest.strip())
                        except json.JSONDecodeError:
                            pass
                try:
                    return json.loads(block)
                except json.JSONDecodeError:
                    pass

        start = candidate.find("{")
        while start != -1:
            depth = 0
            in_string = False
            escape = False
            for idx in range(start, len(candidate)):
                ch = candidate[idx]
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == '"':
                        in_string = False
                    continue
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        snippet = candidate[start:idx + 1]
                        try:
                            return json.loads(snippet)
                        except json.JSONDecodeError:
                            break
            start = candidate.find("{", start + 1)

        raise RuntimeError("Model did not return valid JSON content.")

    def _extract_tool_input(self, response) -> dict:
        message = response.choices[0].message
        tool_calls = getattr(message, "tool_calls", None) or []
        for call in tool_calls:
            if call.function.name == "submit_review":
                # Function arguments arrive as a JSON string.
                return self._parse_json_payload(call.function.arguments)
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return self._parse_json_payload(content)
        if isinstance(content, list):
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                    text_parts.append(item["text"])
                elif hasattr(item, "type") and getattr(item, "type") == "text" and isinstance(getattr(item, "text", None), str):
                    text_parts.append(item.text)
            if text_parts:
                return self._parse_json_payload("\n".join(text_parts))
        raise RuntimeError("Model did not return a submit_review function call.")

    @staticmethod
    def _is_valid_overlay_file(file: str, files_changed: list) -> bool:
        """Compliance overlays must point at real, touched repo paths."""
        if not file or not isinstance(file, str):
            return False
        file = file.strip()
        if not file:
            return False
        # Reject module-name fallbacks and malformed metadata.
        if file.lower() in {"compliance", "hipaa", "hl7"}:
            return False
        forbidden = {",", ":", ";", "{", "}", "\"", "'", "[", "]"}
        if any(ch in file for ch in forbidden):
            return False
        if file not in (files_changed or []):
            return False
        lower = file.lower()
        if lower.startswith(("tests/", "test/", "docs/", "doc/")):
            return False
        return True

    @staticmethod
    def _allows_fileless_overlay(category: str) -> bool:
        """Some policy findings are real review items even without a precise file anchor."""
        return category in {"third_party_baa"}

    def _clean_compliance_overlays(self, overlays: list, files_changed: list) -> list:
        """Keep only valid, non-duplicate compliance overlays."""
        cleaned = []
        seen = set()
        for overlay in overlays or []:
            file = overlay.get("file")
            category = str(overlay.get("category") or "").strip().lower()
            normalized = dict(overlay)
            if self._is_valid_overlay_file(file, files_changed):
                normalized["file"] = file.strip()
            elif self._allows_fileless_overlay(category):
                normalized["file"] = ""
            else:
                continue
            key = (
                normalized.get("severity"),
                normalized.get("file"),
                normalized.get("description"),
                normalized.get("suggestion"),
            )
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(normalized)
        return cleaned

    def _to_result(self, request: ReviewRequest, payload: dict, decisions) -> ReviewResult:
        # Enrich applied decisions with the stored summary when the model omitted it.
        by_ref = {d.get("ref"): d for d in decisions if d.get("ref")}
        applied = []
        for entry in payload.get("past_decisions_applied", []) or []:
            ref = entry.get("ref", "")
            applied.append(
                {
                    "ref": ref,
                    "summary": entry.get("summary") or by_ref.get(ref, {}).get("summary", ""),
                    "how_applied": entry.get("how_applied", ""),
                }
            )

        confidence = float(payload.get("confidence", 0.0) or 0.0)
        confidence = max(0.0, min(1.0, confidence))

        deterministic_compliance = None
        if request.compliance:
            deterministic_compliance = compliance.review_findings(
                request.diff,
                request.files_changed,
                config_store.get_compliance_policy(request.repo),
            )
        compliance_review = compliance.normalize_result(
            payload.get("compliance_review"),
            deterministic_compliance,
            enabled=request.compliance,
        )
        issues = payload.get("issues", []) or []
        overlays = compliance.review_issue_overlays(compliance_review, enabled=request.compliance)
        issues.extend(self._clean_compliance_overlays(overlays, request.files_changed))
        deduped_issues = []
        seen_issues = set()
        for issue in issues:
            key = (issue.get("severity"), issue.get("file"), issue.get("description"))
            if key not in seen_issues:
                seen_issues.add(key)
                deduped_issues.append(issue)

        return ReviewResult(
            pr_number=request.pr_number,
            summary=payload.get("summary", ""),
            approved=bool(payload.get("approved", False)) and not any(
                i.get("severity") in {"critical", "high"} for i in deduped_issues
            ),
            confidence=confidence,
            issues=deduped_issues,
            suggestions=payload.get("suggestions", []) or [],
            past_decisions_applied=applied,
            compliance_review=compliance_review,
        )
