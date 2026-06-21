"""Tests for core/review_engine.py — prompt building, scoping, structured parsing.

The OpenRouter/OpenAI client is faked, so no network calls are made.
"""

import json
import types

import pytest

import decision_store as ds
from review_engine import CodeReviewEngine, ReviewRequest


# ----- fakes ----- #

def make_response(payload, name="submit_review", no_tool=False):
    if no_tool:
        message = types.SimpleNamespace(tool_calls=[])
    else:
        fn = types.SimpleNamespace(name=name, arguments=json.dumps(payload))
        message = types.SimpleNamespace(tool_calls=[types.SimpleNamespace(function=fn)])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.captured = None
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.captured = kwargs
        return self.response


def make_engine(store, payload, monkeypatch, no_tool=False, model_override=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    eng = CodeReviewEngine(store, model=model_override)
    eng._client = FakeClient(make_response(payload, no_tool=no_tool))
    return eng


def review_payload(**over):
    p = {
        "summary": "looks fine",
        "approved": True,
        "confidence": 0.9,
        "issues": [],
        "suggestions": [],
        "past_decisions_applied": [],
    }
    p.update(over)
    return p


def make_req(**over):
    base = dict(pr_number=1, repo="org/a", title="auth change", diff="+x",
                description="d", author="dev", base_branch="main", files_changed=["a.py"])
    base.update(over)
    return ReviewRequest(**base)


def _seed(store):
    store.upsert(doc_id="a1", ref="PR #1", summary="auth in api", reasoning="jwt",
                 outcome="approved_and_merged", date="d", metadata={"repo": "org/a"})
    store.upsert(doc_id="b1", ref="PR #2", summary="web styling", reasoning="css",
                 outcome="changes_requested", date="d", metadata={"repo": "org/b"})
    store.upsert(doc_id="g1", ref="ADR-1", summary="org policy", reasoning="std",
                 outcome="approved_and_merged", date="d", metadata={"repo": ds.GLOBAL_REPO})


# ----- prompt & context ----- #

def test_build_prompt_contains_key_parts(store, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch)
    prompt = eng._build_prompt(make_req(), [])
    assert "Pull Request #1" in prompt
    assert "a.py" in prompt
    assert "+x" in prompt
    assert "no relevant past decisions" in prompt


def test_build_prompt_contains_hipaa_context(store, cfg, monkeypatch):
    cfg.save_config({"hipaa_policies": {"default": {"notes": "Use approved BAAs only"}, "repos": {}}})
    eng = make_engine(store, review_payload(), monkeypatch)
    prompt = eng._build_prompt(make_req(hipaa=True, diff='+ logger.info("patient_ssn")'), [])
    assert "HIPAA Review Mode" in prompt
    assert "Use approved BAAs only" in prompt
    assert "Potential PHI in logs or debug output" in prompt


def test_retrieve_context_scopes_to_repo_plus_global(store, monkeypatch):
    _seed(store)
    eng = make_engine(store, review_payload(), monkeypatch)
    ctx = eng._retrieve_context(make_req(repo="org/a"))
    assert {d["ref"] for d in ctx} == {"PR #1", "ADR-1"}


# ----- review() output mapping ----- #

def test_review_maps_structured_result(store, cfg, monkeypatch):
    payload = review_payload(
        approved=False, confidence=0.7,
        issues=[{"severity": "high", "file": "a.py", "description": "bug", "suggestion": "fix"}],
        suggestions=[{"type": "security", "description": "sanitize"}],
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req())
    assert result.pr_number == 1
    assert result.approved is False
    assert result.confidence == 0.7
    assert result.issues[0]["severity"] == "high"
    assert result.suggestions[0]["type"] == "security"


@pytest.mark.parametrize("raw,expected", [(1.5, 1.0), (-0.2, 0.0), (0.42, 0.42)])
def test_confidence_clamped(store, cfg, monkeypatch, raw, expected):
    eng = make_engine(store, review_payload(confidence=raw), monkeypatch)
    assert eng.review(make_req()).confidence == expected


def test_past_decisions_enriched_from_store(store, cfg, monkeypatch):
    _seed(store)
    payload = review_payload(
        past_decisions_applied=[{"ref": "PR #1", "how_applied": "followed jwt precedent"}]
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(repo="org/a"))
    applied = result.past_decisions_applied[0]
    assert applied["ref"] == "PR #1"
    assert applied["summary"] == "auth in api"  # filled in from the store
    assert applied["how_applied"] == "followed jwt precedent"


def test_hipaa_result_merges_deterministic_findings(store, cfg, monkeypatch):
    payload = review_payload(
        hipaa_review={
            "hipaa_relevant": True,
            "requires_manual_compliance_review": True,
            "summary": "Needs HIPAA review",
            "hipaa_findings": [
                {
                    "category": "manual_review",
                    "severity": "medium",
                    "title": "Vendor review required",
                    "evidence": "External processor usage is policy-sensitive.",
                    "recommendation": "Confirm BAA coverage.",
                    "manual_review": True,
                }
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    req = make_req(
        hipaa=True,
        files_changed=["api/patient.py"],
        diff='+ logger.info("patient_ssn")\n+ requests.post("http://vendor", json={"patient_id": 1})',
    )
    result = eng.review(req)
    assert result.hipaa_review["enabled"] is True
    assert result.hipaa_review["hipaa_relevant"] is True
    assert result.hipaa_review["requires_manual_compliance_review"] is True
    titles = {f["title"] for f in result.hipaa_review["hipaa_findings"]}
    assert "Potential PHI in logs or debug output" in titles
    assert "Vendor review required" in titles
    assert any(i["description"] == "Potential PHI in logs or debug output" for i in result.issues)


def test_missing_tool_call_raises(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch, no_tool=True)
    with pytest.raises(RuntimeError, match="submit_review"):
        eng.review(make_req())


# ----- model & provider resolution ----- #

def test_model_resolved_from_config(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_model": "openai/gpt-4o"})
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert eng._client.captured["model"] == "openai/gpt-4o"


def test_model_override_wins(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_model": "openai/gpt-4o"})
    eng = make_engine(store, review_payload(), monkeypatch, model_override="anthropic/claude-x")
    eng.review(make_req())
    assert eng._client.captured["model"] == "anthropic/claude-x"


def test_provider_sets_extra_body(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_provider": "Anthropic"})
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    provider = eng._client.captured["extra_body"]["provider"]
    assert provider["order"] == ["Anthropic"]
    assert provider["allow_fallbacks"] is False


def test_no_provider_means_no_extra_body(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert "extra_body" not in eng._client.captured


def test_forces_submit_review_tool(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert eng._client.captured["tool_choice"] == {"type": "function", "function": {"name": "submit_review"}}
