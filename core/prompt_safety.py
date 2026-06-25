"""Helpers for redacting sensitive literals before prompts leave the app."""

from __future__ import annotations

import re

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9._-]+\.[A-Za-z0-9._-]+\b")
_GH_TOKEN_RE = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
_API_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{12,}\b")
_BEARER_RE = re.compile(r"(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)", re.IGNORECASE)
_SENSITIVE_FIELD_VALUE_RE = re.compile(
    r"(?P<prefix>\b(?:patient(?:_id|_name|_identifier)?|claim(?:_id)?|member(?:_id)?|"
    r"mrn|medical_record_number|ssn|email|phone|dob|date_of_birth)\b\s*[:=]\s*[\"']?)(?P<value>[^\"',\n]+)",
    re.IGNORECASE,
)


def sanitize_for_external_llm(text: str) -> str:
    """Redact common credential, PII, and PHI literals from prompt text."""
    sanitized = text or ""
    sanitized = _EMAIL_RE.sub("<REDACTED:EMAIL>", sanitized)
    sanitized = _PHONE_RE.sub("<REDACTED:PHONE>", sanitized)
    sanitized = _SSN_RE.sub("<REDACTED:SSN>", sanitized)
    sanitized = _JWT_RE.sub("<REDACTED:JWT>", sanitized)
    sanitized = _GH_TOKEN_RE.sub("<REDACTED:GITHUB_TOKEN>", sanitized)
    sanitized = _API_KEY_RE.sub("<REDACTED:API_KEY>", sanitized)
    sanitized = _BEARER_RE.sub(r"\1<REDACTED:BEARER_TOKEN>", sanitized)
    sanitized = _SENSITIVE_FIELD_VALUE_RE.sub(r"\g<prefix><REDACTED:SENSITIVE_VALUE>", sanitized)
    return sanitized
