"""Tests for assessment engine LLM response handling."""

import json
import types

from assessment_engine import AssessmentEngine, AssessmentRequest


def assessment_payload(**over):
    payload = {
        "summary": "A service for reviewing code",
        "purpose": "Helps engineering teams assess repositories",
        "tech_stack": ["Python", "FastAPI"],
        "key_components": [
            {"name": "API", "role": "Serves assessment requests", "files": ["backend/main.py"]}
        ],
        "vulnerabilities": [],
    }
    payload.update(over)
    return payload


def make_response(payload, name="submit_assessment", no_tool=False, content=None, raw_arguments=None):
    if no_tool:
        message = types.SimpleNamespace(tool_calls=[], content=content)
    else:
        fn = types.SimpleNamespace(
            name=name,
            arguments=raw_arguments if raw_arguments is not None else json.dumps(payload),
        )
        message = types.SimpleNamespace(tool_calls=[types.SimpleNamespace(function=fn)], content=content)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.captured = None
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


def make_engine(cfg, monkeypatch, response):
    cfg.save_config(
        {
            "github_tokens": [{"username": "org", "orgs": [], "token": "gh-token"}],
            "llm_base_url": "http://localhost:11434/v1",
            "llm_api_key": "local-key",
            "openrouter_model": "local-model",
        }
    )
    engine = AssessmentEngine()
    engine._test_client = FakeClient(response)
    monkeypatch.setattr(engine, "_make_client", lambda: engine._test_client)
    monkeypatch.setattr(engine, "_fetch_repo_content", lambda repo, token: (["README.md"], {"README.md": "# App"}))
    return engine


def test_missing_tool_call_falls_back_to_raw_json_content(cfg, monkeypatch):
    payload = assessment_payload(summary="raw json fallback")
    engine = make_engine(
        cfg,
        monkeypatch,
        make_response(payload, no_tool=True, content=json.dumps(payload)),
    )

    result = engine.assess(AssessmentRequest(repo="org/a"))

    assert result.summary == "raw json fallback"
    assert result.tech_stack == ["Python", "FastAPI"]
    assert "tools" in engine._test_client.captured
    assert engine._test_client.captured["tool_choice"] == {
        "type": "function",
        "function": {"name": "submit_assessment"},
    }


def test_missing_tool_call_falls_back_to_fenced_json_content(cfg, monkeypatch):
    payload = assessment_payload(summary="fenced json fallback")
    engine = make_engine(
        cfg,
        monkeypatch,
        make_response(payload, no_tool=True, content=f"```json\n{json.dumps(payload)}\n```"),
    )

    result = engine.assess(AssessmentRequest(repo="org/a"))

    assert result.summary == "fenced json fallback"


def test_unsupported_tool_call_retries_without_tools(cfg, monkeypatch):
    payload = assessment_payload(summary="retry fallback")
    responses = [
        RuntimeError("Provider error: unsupported parameter: tools"),
        make_response(payload, no_tool=True, content=json.dumps(payload)),
    ]
    engine = make_engine(cfg, monkeypatch, responses)

    result = engine.assess(AssessmentRequest(repo="org/a"))

    assert result.summary == "retry fallback"
    assert "tools" in engine._test_client.calls[0]
    assert "tool_choice" in engine._test_client.calls[0]
    assert "tools" not in engine._test_client.calls[1]
    assert "tool_choice" not in engine._test_client.calls[1]


def test_tool_call_arguments_tolerate_raw_newlines_in_strings(cfg, monkeypatch):
    payload = assessment_payload(summary="line one\nline two")
    raw_arguments = (
        '{"summary":"line one\nline two","purpose":"Assess repositories",'
        '"tech_stack":["Python"],"key_components":[],"vulnerabilities":[]}'
    )
    engine = make_engine(
        cfg,
        monkeypatch,
        make_response(payload, raw_arguments=raw_arguments),
    )

    result = engine.assess(AssessmentRequest(repo="org/a"))

    assert result.summary == "line one\nline two"
