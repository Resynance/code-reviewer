"""Tests for core/review_engine.py — prompt building, scoping, structured parsing.

The OpenRouter/OpenAI client is faked, so no network calls are made.
"""

import json
import types

import pytest

import decision_store as ds
from review_engine import CodeReviewEngine, ReviewRequest, _REVIEW_SCHEMA


# ----- fakes ----- #

def make_response(payload, name="submit_review", no_tool=False, content=None, raw_arguments=None):
    if no_tool:
        message = types.SimpleNamespace(tool_calls=[], content=content)
    else:
        fn = types.SimpleNamespace(name=name, arguments=raw_arguments if raw_arguments is not None else json.dumps(payload))
        message = types.SimpleNamespace(tool_calls=[types.SimpleNamespace(function=fn)], content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.captured = None
        self.calls = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.captured = kwargs
        self.calls.append(kwargs)
        if isinstance(self.response, list):
            current = self.response.pop(0)
        else:
            current = self.response
        if isinstance(current, Exception):
            raise current
        return current


def make_engine(store, payload, monkeypatch, no_tool=False, model_override=None, content=None, raw_arguments=None, response=None):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    eng = CodeReviewEngine(store, model=model_override)
    eng._test_client = FakeClient(response if response is not None else make_response(payload, no_tool=no_tool, content=content, raw_arguments=raw_arguments))
    monkeypatch.setattr(eng, "_make_client", lambda: eng._test_client)
    return eng


def review_payload(**over):
    p = {
        "summary": "looks fine",
        "approved": True,
        "confidence": 0.9,
        "issues": [],
        "suggestions": [],
        "past_decisions_applied": [],
        "compliance_review": {
            "hipaa_relevant": False,
            "hl7_relevant": False,
            "requires_manual_compliance_review": False,
            "summary": "",
            "policy_notes_applied": [],
            "hipaa_findings": [],
            "hl7_findings": [],
            "phi_exposure_risk": [],
            "encryption_gaps": [],
            "access_control_gaps": [],
            "audit_trail_gaps": [],
            "minimum_necessary_gaps": [],
            "third_party_baa_risks": [],
            "hl7_interface_gaps": [],
            "hl7_message_integrity_gaps": [],
            "hl7_transport_gaps": [],
        },
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


def test_review_schema_closes_all_object_nodes():
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert isinstance(node.get("properties"), dict)
                assert isinstance(node.get("required"), list)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(_REVIEW_SCHEMA)


def test_build_prompt_contains_compliance_context(store, cfg, monkeypatch):
    cfg.save_config({"compliance_policies": {"default": {"notes": "Use approved BAAs only"}, "repos": {}}})
    eng = make_engine(store, review_payload(), monkeypatch)
    prompt = eng._build_prompt(
        make_req(compliance=True, files_changed=["api/patient.py"], diff='+ logger.info("patient_ssn")'),
        [],
    )
    assert "HIPAA / HL7 Review Mode" in prompt
    assert "Use approved BAAs only" in prompt
    assert "Potential PHI in logs or debug output" in prompt


def test_build_prompt_contains_hl7_context(store, cfg, monkeypatch):
    cfg.save_config({"compliance_policies": {"default": {"notes": "Validate HL7 ACKs"}, "repos": {}}})
    eng = make_engine(store, review_payload(), monkeypatch)
    prompt = eng._build_prompt(
        make_req(
            compliance=True,
            files_changed=["integrations/hl7_listener.py"],
            diff='+ logger.info("MSH|^~\\\\&|ADT|PID|")\n+ start_hl7_listener("tcp://feed:2575")',
        ),
        [],
    )
    assert "HIPAA / HL7 Review Mode" in prompt
    assert "Validate HL7 ACKs" in prompt
    assert "Raw HL7 payload appears in logs or debug output" in prompt


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


def test_compliance_result_merges_deterministic_findings(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "hipaa_relevant": True,
            "hl7_relevant": True,
            "requires_manual_compliance_review": True,
            "summary": "Needs healthcare compliance review",
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
            "hl7_findings": [
                {
                    "category": "hl7_manual_review",
                    "severity": "medium",
                    "title": "Partner message contract review required",
                    "evidence": "ACK expectations are not fully visible in code.",
                    "recommendation": "Confirm the interface profile and ACK flow with the trading partner.",
                    "manual_review": True,
                }
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    req = make_req(
        compliance=True,
        files_changed=["api/patient.py", "integrations/hl7_listener.py"],
        diff='+ logger.info("patient_ssn")\n+ requests.post("http://vendor", json={"patient_id": 1})\n+ logger.info("MSH|^~\\\\&|ADT|PID|")',
    )
    result = eng.review(req)
    assert result.compliance_review["enabled"] is True
    assert result.compliance_review["hipaa_relevant"] is True
    assert result.compliance_review["hl7_relevant"] is True
    assert result.compliance_review["requires_manual_compliance_review"] is True
    titles = {f["title"] for f in result.compliance_review["hipaa_findings"]}
    hl7_titles = {f["title"] for f in result.compliance_review["hl7_findings"]}
    assert "Potential PHI in logs or debug output" in titles
    assert "Vendor review required" in titles
    assert "Raw HL7 payload appears in logs or debug output" in hl7_titles
    assert "Partner message contract review required" in hl7_titles
    assert any(i["description"] == "Potential PHI in logs or debug output" for i in result.issues)
    assert any(i["description"] == "Raw HL7 payload appears in logs or debug output" for i in result.issues)


def test_compliance_overlays_filtered_to_touched_files(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "api/patient.py",
                },
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "api/untouched.py",
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["api/patient.py"]))
    files = {i["file"] for i in result.issues}
    assert "api/patient.py" in files
    assert "api/untouched.py" not in files


def test_compliance_overlays_skip_malformed_file_labels(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": ",severity:",
                },
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "compliance",
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["api/patient.py"]))
    assert not any(i["file"] in {",severity:", "compliance"} for i in result.issues)


def test_vendor_policy_findings_survive_without_file_anchor(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "third_party_baa",
                    "severity": "high",
                    "title": "Disallowed third-party vendor appears in HIPAA review scope",
                    "evidence": "Observed external integration matching a disallowed vendor policy: api.segment.io",
                    "recommendation": "Remove the integration or document an approved replacement before handling PHI.",
                    "manual_review": True,
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["api/patient.py"]))
    assert any(i["description"] == "Disallowed third-party vendor appears in HIPAA review scope" for i in result.issues)
    assert any(i["file"] == "" for i in result.issues if i["description"] == "Disallowed third-party vendor appears in HIPAA review scope")


def test_compliance_overlays_deduped(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "api/patient.py",
                },
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "api/patient.py",
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["api/patient.py"]))
    assert len([i for i in result.issues if i["file"] == "api/patient.py"]) == 1


def test_compliance_overlays_suppress_test_docs(store, cfg, monkeypatch):
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "tests/test_patient.py",
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["tests/test_patient.py"]))
    assert not any(i["file"].startswith("tests/") for i in result.issues)


def test_pr_comment_body_has_no_malformed_file_labels(store, cfg, monkeypatch):
    """PR comment formatting must not turn arbitrary metadata into file names."""
    payload = review_payload(
        compliance_review={
            "enabled": True,
            "hipaa_relevant": True,
            "hipaa_findings": [
                {
                    "category": "phi_logging",
                    "severity": "critical",
                    "title": "Potential PHI in logs",
                    "evidence": "logger.info(patient_ssn)",
                    "recommendation": "Remove PHI from logs.",
                    "file": "api/patient.py",
                },
            ],
        }
    )
    eng = make_engine(store, payload, monkeypatch)
    result = eng.review(make_req(compliance=True, files_changed=["api/patient.py"]))

    # Simulate the frontend buildBody() formatting in Python.
    def format_comment_body(issues):
        lines = []
        for it in issues:
            file = str(it.get("file") or "").replace("`", "\\`").strip()
            lines.append(f"- **[{it.get('severity', '').upper()}]** `{file}` — {it.get('description', '')}")
        return "\n".join(lines)

    body = format_comment_body(result.issues)
    assert "`,severity:`" not in body
    assert "`compliance`" not in body
    assert "`api/patient.py`" in body


def test_pr_comment_body_omits_invalid_generic_issue_files():
    def format_comment_body(issues):
        def sanitize_file_label(value):
            file = str(value or "").replace("\n", "").replace("\r", "").replace("`", "").strip()
            if not file:
                return ""
            if file.lower() in {"compliance", "hipaa", "hl7"}:
                return ""
            if any(ch in file for ch in ',:;{}"\'[]'):
                return ""
            return file

        lines = []
        for it in issues:
            file = sanitize_file_label(it.get("file"))
            label = f" `{file}` -" if file else " -"
            lines.append(f"[{it.get('severity', '').upper()}]{label} {it.get('description', '')}")
        return "\n".join(lines)

    body = format_comment_body([
        {"severity": "critical", "file": ",severity:", "description": "bad file"},
        {"severity": "high", "file": "api/patient.py", "description": "good file"},
        {"severity": "medium", "file": "compliance", "description": "module fallback"},
    ])
    assert ",severity:" not in body
    assert "compliance" not in body
    assert "api/patient.py" in body


def test_missing_tool_call_raises(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch, no_tool=True)
    with pytest.raises(RuntimeError, match="submit_review"):
        eng.review(make_req())


def test_missing_tool_call_falls_back_to_raw_json_content(store, cfg, monkeypatch):
    payload = review_payload(summary="gemini fallback")
    eng = make_engine(store, payload, monkeypatch, no_tool=True, content=json.dumps(payload))
    result = eng.review(make_req(model="google/gemini-2.5-flash-lite"))
    assert result.summary == "gemini fallback"


def test_missing_tool_call_falls_back_to_fenced_json_content(store, cfg, monkeypatch):
    payload = review_payload(summary="fenced fallback")
    eng = make_engine(
        store,
        payload,
        monkeypatch,
        no_tool=True,
        content=f"```json\n{json.dumps(payload)}\n```",
    )
    result = eng.review(make_req(model="google/gemini-2.5-flash-lite"))
    assert result.summary == "fenced fallback"


def test_tool_call_arguments_tolerate_raw_newlines_in_strings(store, cfg, monkeypatch):
    payload = review_payload(summary="line one\nline two")
    raw_arguments = (
        '{"summary":"line one\nline two","approved":true,"confidence":0.9,'
        '"issues":[],"suggestions":[],"past_decisions_applied":[],"compliance_review":{}}'
    )
    eng = make_engine(store, payload, monkeypatch, raw_arguments=raw_arguments)
    result = eng.review(make_req())
    assert result.summary == "line one\nline two"


def test_unsupported_tool_call_retries_without_tools(store, cfg, monkeypatch):
    payload = review_payload(summary="retry fallback")
    responses = [
        RuntimeError("Provider error: unsupported parameter: tools"),
        make_response(payload, no_tool=True, content=json.dumps(payload)),
    ]
    eng = make_engine(store, payload, monkeypatch, response=responses)
    result = eng.review(make_req(model="google/gemini-2.5-flash-lite"))
    assert result.summary == "retry fallback"
    assert "tools" in eng._test_client.calls[0]
    assert "tool_choice" in eng._test_client.calls[0]
    assert "tools" not in eng._test_client.calls[1]
    assert "tool_choice" not in eng._test_client.calls[1]


# ----- model & provider resolution ----- #

def test_model_resolved_from_config(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_model": "openai/gpt-4o"})
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert eng._test_client.captured["model"] == "openai/gpt-4o"


def test_model_override_wins(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_model": "openai/gpt-4o"})
    eng = make_engine(store, review_payload(), monkeypatch, model_override="anthropic/claude-x")
    eng.review(make_req())
    assert eng._test_client.captured["model"] == "anthropic/claude-x"


def test_provider_sets_extra_body(store, cfg, monkeypatch):
    cfg.save_config({"openrouter_provider": "Anthropic"})
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    provider = eng._test_client.captured["extra_body"]["provider"]
    assert provider["order"] == ["Anthropic"]
    assert provider["allow_fallbacks"] is False


def test_no_provider_means_no_extra_body(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert "extra_body" not in eng._test_client.captured


def test_non_openrouter_target_skips_provider_routing(store, cfg, monkeypatch):
    cfg.save_config({
        "llm_base_url": "http://192.168.0.197:8080/",
        "openrouter_provider": "Anthropic",
    })
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert "extra_body" not in eng._test_client.captured


def test_forces_submit_review_tool(store, cfg, monkeypatch):
    eng = make_engine(store, review_payload(), monkeypatch)
    eng.review(make_req())
    assert eng._test_client.captured["tool_choice"] == {"type": "function", "function": {"name": "submit_review"}}
