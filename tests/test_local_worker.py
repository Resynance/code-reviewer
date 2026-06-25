"""Tests for local_worker.py agentic review orchestration."""

import subprocess
import pytest

from review_engine import ReviewRequest, ReviewResult

import local_worker


class FakeEngine:
    def _retrieve_context(self, request):
        return [{"ref": "ADR-1", "summary": "Use JWT", "reasoning": "stateless"}]

    def _build_prompt(self, request, decisions):
        return f"Prompt for {request.repo}"

    def _to_result(self, request, payload, decisions):
        return ReviewResult(
            pr_number=request.pr_number,
            summary=payload.get("summary", ""),
            approved=payload.get("approved", True),
            confidence=payload.get("confidence", 0.5),
            issues=payload.get("issues", []) or [],
            suggestions=payload.get("suggestions", []) or [],
            past_decisions_applied=payload.get("past_decisions_applied", []) or [],
            compliance_review=payload.get("compliance_review", {}) or {},
        )


def test_agentic_runner_merges_multiple_sources(monkeypatch):
    runner = local_worker.AgenticReviewRunner(FakeEngine())
    monkeypatch.setattr(local_worker.config_store, "get_local_review_agents", lambda: [
        {"id": "codex", "label": "Codex", "enabled": True, "command": ["codex"]},
        {"id": "kimi", "label": "Kimi", "enabled": True, "command": ["kimi"]},
    ])
    payloads = {
        "codex": {
            "summary": "Codex found one issue",
            "approved": False,
            "confidence": 0.9,
            "issues": [{"severity": "high", "file": "api/auth.py", "description": "Missing expiry check", "suggestion": "Validate exp"}],
            "suggestions": [],
            "past_decisions_applied": [{"ref": "ADR-1", "summary": "Use JWT", "how_applied": "validated token design"}],
        },
        "kimi": {
            "summary": "Kimi found another issue",
            "approved": True,
            "confidence": 0.7,
            "issues": [{"severity": "medium", "file": "api/auth.py", "description": "Improve logging hygiene", "suggestion": "Redact token ids"}],
            "suggestions": [{"type": "test_coverage", "description": "Add token expiry tests"}],
            "past_decisions_applied": [{"ref": "ADR-1", "summary": "Use JWT", "how_applied": "validated token design"}],
        },
    }
    monkeypatch.setattr(runner, "_run_command_agent", lambda agent, prompt: payloads[agent["id"]])

    result = runner.run(ReviewRequest(
        pr_number=7,
        repo="org/a",
        title="t",
        diff="+x",
        files_changed=["api/auth.py"],
        agentic=True,
        agent_sources=["codex", "kimi"],
    ))

    assert result["approved"] is False
    assert len(result["issues"]) == 2
    assert len(result["suggestions"]) == 1
    assert result["model"] == "agentic: Codex, Kimi"
    assert [item["source"] for item in result["agent_results"]] == ["Codex", "Kimi"]
    assert result["agent_results"][0]["result"]["summary"] == "Codex found one issue"
    assert result["agent_errors"] == []


def test_agentic_runner_keeps_successful_sources_when_one_fails(monkeypatch):
    runner = local_worker.AgenticReviewRunner(FakeEngine())
    monkeypatch.setattr(local_worker.config_store, "get_local_review_agents", lambda: [
        {"id": "codex", "label": "Codex", "enabled": True, "command": ["codex"]},
        {"id": "kimi", "label": "Kimi", "enabled": True, "command": ["kimi"]},
    ])

    def fake_run(agent, prompt):
        if agent["id"] == "kimi":
            raise RuntimeError("command not found")
        return {
            "summary": "Codex review",
            "approved": True,
            "confidence": 0.8,
            "issues": [],
            "suggestions": [],
            "past_decisions_applied": [],
        }

    monkeypatch.setattr(runner, "_run_command_agent", fake_run)

    result = runner.run(ReviewRequest(
        pr_number=7,
        repo="org/a",
        title="t",
        diff="+x",
        files_changed=["api/auth.py"],
        agentic=True,
        agent_sources=["codex", "kimi"],
    ))

    assert result["approved"] is True
    assert "Unavailable sources:" in result["summary"]
    assert "Kimi: command not found" in result["summary"]
    assert [item["source"] for item in result["agent_results"]] == ["Codex"]
    assert result["agent_errors"] == ["Kimi: command not found"]


def test_decode_agent_output_supports_kimi_stream_json():
    runner = local_worker.AgenticReviewRunner(FakeEngine())

    payload = runner._decode_agent_output(
        '\n'.join([
            '{"role":"assistant","content":"{\\"summary\\":\\"Kimi review\\",\\"approved\\":true,\\"confidence\\":0.8,\\"issues\\":[],\\"suggestions\\":[],\\"past_decisions_applied\\":[],\\"compliance_review\\":{}}"}',
            '{"type":"meta","stats":{"tokens":123}}',
        ])
    )

    assert payload["summary"] == "Kimi review"
    assert payload["approved"] is True


def test_decode_agent_output_ignores_non_json_lines_before_stream():
    runner = local_worker.AgenticReviewRunner(FakeEngine())

    payload = runner._decode_agent_output(
        '\n'.join([
            'Warning: using fallback config',
            '{"role":"assistant","content":"{\\"summary\\":\\"Kimi review\\",\\"approved\\":true,\\"confidence\\":0.8,\\"issues\\":[],\\"suggestions\\":[],\\"past_decisions_applied\\":[],\\"compliance_review\\":{}}"}',
            '{"type":"meta","stats":{"tokens":123}}',
        ])
    )

    assert payload["summary"] == "Kimi review"


def test_decode_agent_output_accepts_fenced_json_from_assistant():
    runner = local_worker.AgenticReviewRunner(FakeEngine())

    payload = runner._decode_agent_output(
        '\n'.join([
            '{"role":"assistant","content":"```json\\n{\\"summary\\":\\"Kimi review\\",\\"approved\\":true,\\"confidence\\":0.8,\\"issues\\":[],\\"suggestions\\":[],\\"past_decisions_applied\\":[],\\"compliance_review\\":{}}\\n```"}',
            '{"type":"meta","stats":{"tokens":123}}',
        ])
    )

    assert payload["summary"] == "Kimi review"


def test_decode_agent_output_extracts_json_after_preamble():
    runner = local_worker.AgenticReviewRunner(FakeEngine())

    payload = runner._decode_agent_output(
        '\n'.join([
            '{"role":"assistant","content":"Here is the requested JSON:\\n{\\"summary\\":\\"Kimi review\\",\\"approved\\":true,\\"confidence\\":0.8,\\"issues\\":[],\\"suggestions\\":[],\\"past_decisions_applied\\":[],\\"compliance_review\\":{}}"}',
            '{"type":"meta","stats":{"tokens":123}}',
        ])
    )

    assert payload["summary"] == "Kimi review"


def test_run_command_agent_reports_timeout_without_prompt_dump(monkeypatch):
    runner = local_worker.AgenticReviewRunner(FakeEngine())
    monkeypatch.setattr(local_worker.shutil, "which", lambda exe: f"/usr/bin/{exe}")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["kimi", "--yolo", "-p", "very long prompt"], timeout=local_worker.AGENT_TIMEOUT_SECONDS)

    monkeypatch.setattr(local_worker.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match=f"Kimi timed out after {local_worker.AGENT_TIMEOUT_SECONDS}s"):
        runner._run_command_agent(
            {"id": "kimi", "label": "Kimi", "enabled": True, "command": ["kimi", "-p", "{prompt}", "--output-format", "stream-json"]},
            "very long prompt",
        )


def test_compliance_followup_runner_returns_structured_result(monkeypatch):
    runner = local_worker.ComplianceFollowupRunner()
    monkeypatch.setattr(local_worker.config_store, "get_local_agentic_targets", lambda: [
        {"id": "codex", "label": "Codex", "enabled": True, "command": ["codex"]},
    ])
    monkeypatch.setattr(runner, "_run_target", lambda target, prompt: '{"status":"completed","summary":"Opened PR","pr_url":"https://github.com/org/a/pull/9"}')

    result = runner.run({
        "repo": "org/a",
        "issue_url": "https://github.com/org/a/issues/7",
        "target_id": "codex",
        "analysis": {"health": {"score": 90}},
    })

    assert result["status"] == "completed"
    assert result["pr_url"].endswith("/pull/9")
    assert result["target"] == "Codex"


def test_compliance_followup_runner_falls_back_to_manual_summary(monkeypatch):
    runner = local_worker.ComplianceFollowupRunner()
    monkeypatch.setattr(local_worker.config_store, "get_local_agentic_targets", lambda: [
        {"id": "codex", "label": "Codex", "enabled": True, "command": ["codex"]},
    ])
    monkeypatch.setattr(runner, "_run_target", lambda target, prompt: "Manual follow-up needed")

    result = runner.run({
        "repo": "org/a",
        "issue_url": "https://github.com/org/a/issues/7",
        "target_id": "codex",
        "analysis": {"health": {"score": 90}},
    })

    assert result["status"] == "manual_followup"
    assert result["summary"] == "Manual follow-up needed"
