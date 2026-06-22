#!/usr/bin/env python3
"""
Local LLM worker for ReviewBot (gitignored, runs only on your machine).

Polls the ReviewBot local-queue worker endpoints, runs queued reviews and
assessments against a local OpenAI-compatible LLM (e.g. Ollama, llama.cpp,
LocalAI, vLLM), and posts results back to the ReviewBot API.

Prerequisites:
    source .venv/bin/activate
    ReviewBot must be configured with llm_execution_mode="local_queue".
    A non-empty llm_worker_secret is required for local-LLM queue jobs and
    assessments, but local agentic review jobs may run without one.

Environment variables:
    REVIEWBOT_API_URL      Base URL of the ReviewBot API (default: http://localhost:1500)
    REVIEWBOT_WORKER_SECRET Shared secret for /worker/llm/* endpoints (optional for agentic-only local reviews)
    REVIEWBOT_WORKER_ID    Optional worker identifier (default: local-worker)
    REVIEWBOT_POLL_INTERVAL Seconds between empty-queue polls (default: 1.0)
    LOCAL_LLM_BASE_URL     OpenAI-compatible local LLM base URL (default: http://localhost:11434/v1)
    LOCAL_LLM_API_KEY      API key for the local LLM, if required (default: local-llm)
    LOCAL_LLM_MODEL        Optional model name override for every job
    GITHUB_TOKEN           GitHub token for assessments; falls back to config.json/GITHUB_TOKEN

Usage:
    export REVIEWBOT_WORKER_SECRET="your-secret-from-settings"  # optional for agentic-only local reviews
    export LOCAL_LLM_MODEL="llama3.1:8b"
    python local_worker.py
"""

from __future__ import annotations

import os
import sys
import time
import json
import dataclasses
import logging
import subprocess
import tempfile
import shutil
from pathlib import Path

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("local_worker")


def _normalize_base_url(value: str) -> str:
    return (value or "").strip().rstrip("/")

# --------------------------------------------------------------------------- #
# Point the ReviewBot LLM engines at the local LLM *before* importing them.
# The engines read OPENROUTER_BASE_URL / OPENROUTER_API_KEY as defaults.
# --------------------------------------------------------------------------- #
os.environ.setdefault(
    "OPENROUTER_BASE_URL",
    _normalize_base_url(os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:11434/v1")),
)
os.environ.setdefault(
    "OPENROUTER_API_KEY",
    os.getenv("LOCAL_LLM_API_KEY", "local-llm"),
)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))

from decision_store import create_store
from review_engine import CodeReviewEngine, ReviewRequest, _REVIEW_SCHEMA
from assessment_engine import AssessmentEngine, AssessmentRequest
import config_store
import compliance

API_URL = os.getenv("REVIEWBOT_API_URL", "http://localhost:1500").rstrip("/")
WORKER_SECRET = os.getenv("REVIEWBOT_WORKER_SECRET", "")
WORKER_ID = os.getenv("REVIEWBOT_WORKER_ID", "local-worker")
POLL_INTERVAL = float(os.getenv("REVIEWBOT_POLL_INTERVAL", "1.0"))
LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "")
AGENT_TIMEOUT_SECONDS = int(os.getenv("REVIEWBOT_AGENT_TIMEOUT_SECONDS", "600"))


# --------------------------------------------------------------------------- #
# ReviewBot API helpers
# --------------------------------------------------------------------------- #

def _worker_headers() -> dict:
    return {"X-Worker-Secret": WORKER_SECRET} if WORKER_SECRET else {}


def claim_job(client: httpx.Client) -> dict | None:
    try:
        resp = client.post(
            f"{API_URL}/worker/llm/claim",
            headers=_worker_headers(),
            json={"worker_id": WORKER_ID, "job_types": ["review", "assessment"]},
            timeout=10.0,
        )
    except httpx.RequestError as e:
        logger.warning("Could not reach ReviewBot API: %s", e)
        return None

    if resp.status_code == 204:
        return None
    if resp.status_code != 200:
        logger.warning("Claim failed (%s): %s", resp.status_code, resp.text[:200])
        return None
    return resp.json()


def complete_job(client: httpx.Client, job_id: str, result: dict) -> None:
    resp = client.post(
        f"{API_URL}/worker/llm/{job_id}/complete",
        headers=_worker_headers(),
        json={"result": result},
        timeout=10.0,
    )
    resp.raise_for_status()
    logger.info("Completed job %s", job_id)


def fail_job(client: httpx.Client, job_id: str, error: str) -> None:
    logger.warning("Failing job %s: %s", job_id, error)
    try:
        resp = client.post(
            f"{API_URL}/worker/llm/{job_id}/error",
            headers=_worker_headers(),
            json={"error": error},
            timeout=10.0,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.error("Could not report job failure: %s", e)


# --------------------------------------------------------------------------- #
# Job execution
# --------------------------------------------------------------------------- #

def _effective_model(req_model: str | None) -> str | None:
    """LOCAL_LLM_MODEL, if set, overrides the model requested by the UI."""
    return LOCAL_LLM_MODEL or req_model or None


def _effective_provider(req_provider: str | None) -> str | None:
    """When LOCAL_LLM_MODEL is forced, drop any OpenRouter provider routing."""
    if LOCAL_LLM_MODEL:
        return ""
    return req_provider or None


def _agentic_prompt(engine: CodeReviewEngine, request: ReviewRequest) -> str:
    decisions = engine._retrieve_context(request)
    base_prompt = engine._build_prompt(request, decisions)
    return (
        f"{base_prompt}\n"
        "## Agentic Review Instructions\n"
        "Return only structured JSON matching the supplied schema.\n"
        "Keep issues evidence-backed and specific to the diff.\n"
        "Use an empty file string only when the finding is real but not attributable to one touched file.\n"
    )


def _normalize_agent(agent: dict) -> dict | None:
    if not isinstance(agent, dict):
        return None
    agent_id = str(agent.get("id") or "").strip().lower()
    if not agent_id:
        return None
    label = str(agent.get("label") or agent_id).strip() or agent_id
    command = [str(v).strip() for v in (agent.get("command") or []) if str(v).strip()]
    return {
        "id": agent_id,
        "label": label,
        "enabled": bool(agent.get("enabled", True)),
        "command": command,
    }


class AgenticReviewRunner:
    def __init__(self, engine: CodeReviewEngine):
        self.engine = engine

    def _configured_agents(self) -> list[dict]:
        out = []
        for item in config_store.get_local_review_agents():
            normalized = _normalize_agent(item)
            if normalized:
                out.append(normalized)
        return out

    def _selected_agents(self, requested: list[str]) -> list[dict]:
        configured = [agent for agent in self._configured_agents() if agent.get("enabled")]
        if not requested:
            return configured
        wanted = {str(v).strip().lower() for v in requested if str(v).strip()}
        return [agent for agent in configured if agent["id"] in wanted]

    def _parse_json_payload(self, text: str) -> dict:
        candidate = (text or "").strip()
        if not candidate:
            raise RuntimeError("agent returned invalid JSON")
        try:
            return json.loads(candidate)
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

        raise RuntimeError("agent returned invalid JSON")

    def _decode_agent_output(self, content: str) -> dict:
        text = (content or "").strip()
        if not text:
            raise RuntimeError("agent returned no output")
        try:
            payload = self._parse_json_payload(text)
            if not (
                isinstance(payload, dict)
                and isinstance(payload.get("role"), str)
                and "content" in payload
                and "summary" not in payload
            ):
                return payload
        except RuntimeError:
            pass

        assistant_chunks = []
        ignored_lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                ignored_lines.append(line)
                continue
            if event.get("role") == "assistant" and isinstance(event.get("content"), str):
                assistant_chunks.append(event["content"])

        merged = "".join(assistant_chunks).strip()
        if not merged:
            if ignored_lines:
                sample = ignored_lines[0][:200]
                raise RuntimeError(f"agent returned invalid JSON; first non-JSON output: {sample}")
            raise RuntimeError("agent returned invalid JSON")
        try:
            return self._parse_json_payload(merged)
        except RuntimeError:
            try:
                json.loads(merged)
            except json.JSONDecodeError as e:
                raise RuntimeError(f"agent returned invalid JSON: {e}") from e
            raise

    def _run_command_agent(self, agent: dict, prompt: str) -> dict:
        command = list(agent.get("command") or [])
        if not command:
            raise RuntimeError(f"{agent['label']} has no command configured")
        exe = command[0]
        if shutil.which(exe) is None:
            raise RuntimeError(f"{agent['label']} command not found: {exe}")
        use_stdin = not any("{prompt}" in str(part) for part in command)

        with tempfile.TemporaryDirectory(prefix=f"reviewbot-{agent['id']}-") as tmpdir:
            schema_path = Path(tmpdir) / "schema.json"
            output_path = Path(tmpdir) / "output.json"
            schema_path.write_text(json.dumps(_REVIEW_SCHEMA), encoding="utf-8")
            rendered = [
                part.format(
                    schema_path=str(schema_path),
                    output_path=str(output_path),
                    prompt_path="",
                    prompt=prompt,
                )
                for part in command
            ]
            try:
                proc = subprocess.run(
                    rendered,
                    input=prompt if use_stdin else None,
                    text=True,
                    capture_output=True,
                    cwd=str(ROOT),
                    timeout=AGENT_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as e:
                raise RuntimeError(f"{agent['label']} timed out after {AGENT_TIMEOUT_SECONDS}s") from e
            if proc.returncode != 0:
                detail = (proc.stderr or proc.stdout or "").strip()
                raise RuntimeError(detail or f"{agent['label']} exited with code {proc.returncode}")
            if output_path.exists():
                content = output_path.read_text(encoding="utf-8").strip()
            else:
                content = (proc.stdout or "").strip()
            if not content:
                raise RuntimeError(f"{agent['label']} returned no output")
            try:
                return self._decode_agent_output(content)
            except RuntimeError as e:
                raise RuntimeError(f"{agent['label']} {e}") from e

    def _merge_compliance_reviews(self, reviews: list[dict], *, enabled: bool) -> dict:
        merged = {"hipaa_findings": [], "hl7_findings": []}
        for review in reviews:
            if not isinstance(review, dict):
                continue
            merged["hipaa_findings"].extend(review.get("hipaa_findings") or [])
            merged["hl7_findings"].extend(review.get("hl7_findings") or [])
            for key in (
                "phi_exposure_risk",
                "encryption_gaps",
                "access_control_gaps",
                "audit_trail_gaps",
                "minimum_necessary_gaps",
                "third_party_baa_risks",
                "hl7_interface_gaps",
                "hl7_message_integrity_gaps",
                "hl7_transport_gaps",
            ):
                merged[key] = [*(merged.get(key) or []), *(review.get(key) or [])]
            merged["hipaa_relevant"] = bool(merged.get("hipaa_relevant") or review.get("hipaa_relevant"))
            merged["hl7_relevant"] = bool(merged.get("hl7_relevant") or review.get("hl7_relevant"))
            merged["requires_manual_compliance_review"] = bool(
                merged.get("requires_manual_compliance_review") or review.get("requires_manual_compliance_review")
            )
            merged["policy_notes_applied"] = [*(merged.get("policy_notes_applied") or []), *(review.get("policy_notes_applied") or [])]
        return compliance.normalize_result(merged, None, enabled=enabled)

    def _serialize_result(self, result) -> dict:
        return {
            "summary": result.summary,
            "approved": result.approved,
            "confidence": result.confidence,
            "issues": result.issues,
            "suggestions": result.suggestions,
            "past_decisions_applied": result.past_decisions_applied,
            "compliance_review": result.compliance_review,
            "model": result.model,
        }

    def _merge_results(self, results: list, sources: list[str], errors: list[str]) -> dict:
        issues, suggestions, decisions = [], [], []
        seen_issues, seen_suggestions, seen_decisions = set(), set(), set()
        for result in results:
            for issue in result.issues:
                key = (issue.get("severity"), issue.get("file"), issue.get("description"))
                if key not in seen_issues:
                    seen_issues.add(key)
                    issues.append(issue)
            for suggestion in result.suggestions:
                key = (suggestion.get("type"), suggestion.get("description"))
                if key not in seen_suggestions:
                    seen_suggestions.add(key)
                    suggestions.append(suggestion)
            for decision in result.past_decisions_applied:
                key = (decision.get("ref"), decision.get("how_applied"))
                if key not in seen_decisions:
                    seen_decisions.add(key)
                    decisions.append(decision)
        summaries = [f"- {name}: {result.summary}" for name, result in zip(sources, results)]
        if errors:
            summaries.append("")
            summaries.append("Unavailable sources:")
            summaries.extend(f"- {msg}" for msg in errors)
        compliance_review = self._merge_compliance_reviews(
            [result.compliance_review for result in results],
            enabled=any(result.compliance_review.get("enabled") for result in results),
        )
        approved = all(result.approved for result in results) and not any(
            issue.get("severity") in {"critical", "high"} for issue in issues
        )
        confidence = sum(result.confidence for result in results) / len(results)
        return {
            "summary": "\n".join(summaries),
            "approved": approved,
            "confidence": confidence,
            "issues": issues,
            "suggestions": suggestions,
            "past_decisions_applied": decisions,
            "compliance_review": compliance_review,
            "model": f"agentic: {', '.join(sources)}",
            "agent_results": [
                {
                    "source": source,
                    "result": self._serialize_result(result),
                }
                for source, result in zip(sources, results)
            ],
            "agent_errors": errors,
        }

    def run(self, request: ReviewRequest) -> dict:
        agents = self._selected_agents(request.agent_sources)
        if not agents:
            raise RuntimeError("No enabled local review agents are configured for this request")
        prompt = _agentic_prompt(self.engine, request)
        decisions = self.engine._retrieve_context(request)
        successful = []
        source_labels = []
        errors = []
        for agent in agents:
            try:
                payload = self._run_command_agent(agent, prompt)
                result = self.engine._to_result(request, payload, decisions)
                result.model = agent["label"]
                successful.append(result)
                source_labels.append(agent["label"])
            except Exception as e:
                logger.warning("Agent %s failed: %s", agent["id"], e)
                errors.append(f"{agent['label']}: {e}")
        if not successful:
            raise RuntimeError("; ".join(errors) or "All local review agents failed")
        merged = self._merge_results(successful, source_labels, errors)
        merged["pr_number"] = request.pr_number
        return merged


class Worker:
    def __init__(self):
        self.review_engine = CodeReviewEngine(create_store())
        self.assessment_engine = AssessmentEngine()
        self.agentic_runner = AgenticReviewRunner(self.review_engine)

    def run_review(self, job: dict) -> dict:
        req = job["request"]
        request = ReviewRequest(
            pr_number=req["pr_number"],
            repo=req["repo"],
            title=req.get("title", ""),
            description=req.get("description", ""),
            diff=req.get("diff", ""),
            author=req.get("author", "unknown"),
            base_branch=req.get("base_branch", "main"),
            files_changed=req.get("files_changed", []) or [],
            model=_effective_model(req.get("model")),
            provider=_effective_provider(req.get("provider")),
            compliance=req.get("compliance", False),
            agentic=bool(req.get("agentic", False)),
            agent_sources=req.get("agent_sources", []) or [],
        )
        if request.agentic:
            return self.agentic_runner.run(request)
        result = self.review_engine.review(request)
        return dataclasses.asdict(result)

    def run_assessment(self, job: dict) -> dict:
        req = job["request"]
        request = AssessmentRequest(
            repo=req["repo"],
            model=_effective_model(req.get("model")),
            provider=_effective_provider(req.get("provider")),
            compliance=req.get("compliance", False),
        )
        result = self.assessment_engine.assess(request)
        return dataclasses.asdict(result)

    def process(self, client: httpx.Client, job: dict) -> None:
        job_id = job["id"]
        job_type = job.get("job_type", "review")
        logger.info("Claimed job %s (%s)", job_id, job_type)

        try:
            if job_type == "review":
                result = self.run_review(job)
            elif job_type == "assessment":
                result = self.run_assessment(job)
            else:
                raise ValueError(f"Unknown job_type: {job_type}")
            complete_job(client, job_id, result)
        except Exception as e:
            fail_job(client, job_id, f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------- #
# Main loop
# --------------------------------------------------------------------------- #

def main() -> int:
    logger.info(
        "Local LLM worker starting — API: %s, LLM: %s, worker: %s",
        API_URL,
        os.environ["OPENROUTER_BASE_URL"],
        WORKER_ID,
    )
    if not WORKER_SECRET:
        logger.info("No REVIEWBOT_WORKER_SECRET set — worker will claim only agentic local review jobs.")

    worker = Worker()

    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                job = claim_job(client)
                if job:
                    worker.process(client, job)
                else:
                    time.sleep(POLL_INTERVAL)
            except KeyboardInterrupt:
                logger.info("Shutting down.")
                break
            except Exception:
                logger.exception("Unexpected error in worker loop")
                time.sleep(POLL_INTERVAL)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
