"""
assessment_engine.py — LLM-powered project assessment.

Fetches a repo's file tree and key source files via the GitHub API, then asks
the model for a structured analysis: what the project does, its tech stack,
key components, and any high-level security vulnerabilities.
"""

import os
import json
import base64
from dataclasses import dataclass, field
from typing import Optional

import config_store

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
GITHUB_API_BASE = "https://api.github.com"

_MAX_FILE_BYTES = 25_000   # content limit per file
_MAX_TOTAL_BYTES = 90_000  # total content budget across all selected files

# Basenames that should always be included when present (case-insensitive).
_PRIORITY_NAMES = frozenset({
    "readme.md", "readme.rst", "readme.txt", "readme",
    "package.json", "requirements.txt", "pyproject.toml",
    "cargo.toml", "go.mod", "composer.json", "gemfile",
    "dockerfile", "docker-compose.yml", ".env.example",
    "vercel.json", "vercel.ts",
})

# Common entry-point basenames to pick up wherever they live in the tree.
_ENTRY_NAMES = frozenset({
    "main.py", "app.py", "server.py", "api.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.ts", "main.go",
})

# Files that must never be sent to the LLM regardless of selection pass.
_SENSITIVE_NAMES = frozenset({
    ".env", ".env.local", ".env.development", ".env.production", ".env.staging",
    ".netrc", ".npmrc", ".pypirc", ".htpasswd",
    "id_rsa", "id_rsa.pub", "id_dsa", "id_ecdsa", "id_ed25519", "id_ed25519.pub",
    "secrets.yml", "secrets.yaml",
})
_SENSITIVE_EXTENSIONS = frozenset({
    ".pem", ".key", ".p12", ".pfx", ".cer", ".crt", ".der",
})


@dataclass
class AssessmentRequest:
    repo: str
    model: Optional[str] = None
    provider: Optional[str] = None
    hipaa: bool = False


@dataclass
class AssessmentResult:
    repo: str
    summary: str
    purpose: str
    tech_stack: list = field(default_factory=list)
    key_components: list = field(default_factory=list)
    vulnerabilities: list = field(default_factory=list)
    model: str = ""


_ASSESSMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "2-3 sentence description of what this project does.",
        },
        "purpose": {
            "type": "string",
            "description": "Who the intended users are and the core problem it solves.",
        },
        "tech_stack": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Technologies, frameworks, and languages used (e.g. Python, FastAPI, React).",
        },
        "key_components": {
            "type": "array",
            "description": "Major logical components of the project.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {
                        "type": "string",
                        "description": "What this component does and why it matters.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Key files that implement this component.",
                    },
                },
                "required": ["name", "role"],
            },
        },
        "vulnerabilities": {
            "type": "array",
            "description": "High-level security vulnerabilities or architectural risks. Omit low-value speculative items.",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["severity", "title", "description", "recommendation"],
            },
        },
    },
    "required": ["summary", "purpose", "tech_stack", "key_components", "vulnerabilities"],
}

_ASSESSMENT_TOOL = {
    "type": "function",
    "function": {
        "name": "submit_assessment",
        "description": "Submit the structured project assessment.",
        "parameters": _ASSESSMENT_SCHEMA,
    },
}

_SYSTEM_PROMPT = """You are a senior software architect performing a project assessment.

Based on the provided file tree and key source files, produce a thorough analysis covering:
- What the project does and who it serves (summary + purpose)
- Its technology stack (languages, frameworks, infrastructure)
- The key logical components — group by responsibility, cite specific files
- High-level security vulnerabilities or architectural risks — focus on real, observable
  concerns from the code (exposed secrets, missing auth, dangerous patterns). Skip
  speculative items with no evidence in the provided files.

Call submit_assessment exactly once with your findings."""


class AssessmentEngine:
    def __init__(self, model: Optional[str] = None):
        from openai import OpenAI

        self._model_override = model
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=os.getenv("OPENROUTER_API_KEY"),
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost:1500"),
                "X-Title": "ReviewBot",
            },
            timeout=240,
            max_retries=0,
        )

    def assess(self, request: AssessmentRequest) -> AssessmentResult:
        owner = request.repo.split("/")[0]
        token = config_store.get_token_for(owner)
        if not token:
            raise ValueError("No GitHub token configured for this repo's owner")

        tree_lines, file_contents = self._fetch_repo_content(request.repo, token)
        prompt = self._build_prompt(request.repo, tree_lines, file_contents, request.hipaa)

        model = request.model or self._model_override or config_store.get_model()
        provider = request.provider if request.provider is not None else config_store.get_provider()

        kwargs = dict(
            model=model,
            max_tokens=3000,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            tools=[_ASSESSMENT_TOOL],
            tool_choice={"type": "function", "function": {"name": "submit_assessment"}},
        )
        if provider:
            kwargs["extra_body"] = {"provider": {"order": [provider], "allow_fallbacks": False}}

        response = self._client.chat.completions.create(**kwargs)
        payload = self._extract_tool_input(response)

        result = AssessmentResult(
            repo=request.repo,
            summary=payload.get("summary", ""),
            purpose=payload.get("purpose", ""),
            tech_stack=payload.get("tech_stack", []) or [],
            key_components=payload.get("key_components", []) or [],
            vulnerabilities=payload.get("vulnerabilities", []) or [],
        )
        result.model = model
        return result

    # ------------------------------------------------------------------ #
    # GitHub fetching
    # ------------------------------------------------------------------ #

    def _fetch_repo_content(self, repo: str, token: str):
        import httpx

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        with httpx.Client(timeout=30.0, headers=headers) as client:
            r = client.get(f"{GITHUB_API_BASE}/repos/{repo}")
            r.raise_for_status()
            default_branch = r.json().get("default_branch", "main")

            r = client.get(
                f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{default_branch}",
                params={"recursive": "1"},
            )
            r.raise_for_status()
            all_blobs = [
                item for item in r.json().get("tree", [])
                if item.get("type") == "blob"
            ]

        tree_lines = [item["path"] for item in all_blobs]
        selected = self._select_files(all_blobs)

        file_contents = {}
        total = 0

        with httpx.Client(timeout=30.0, headers=headers) as client:
            for item in selected:
                if total >= _MAX_TOTAL_BYTES:
                    break
                path = item["path"]
                if item.get("size", 0) > _MAX_FILE_BYTES:
                    continue
                try:
                    r = client.get(f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}")
                    r.raise_for_status()
                    raw = r.json().get("content", "").replace("\n", "")
                    content = base64.b64decode(raw).decode("utf-8", errors="replace")
                    content = content[:_MAX_FILE_BYTES]
                    file_contents[path] = content
                    total += len(content)
                except Exception:
                    continue

        return tree_lines, file_contents

    def _is_sensitive(self, path: str) -> bool:
        basename = path.rsplit("/", 1)[-1].lower()
        _, ext = os.path.splitext(basename)
        return basename in _SENSITIVE_NAMES or ext in _SENSITIVE_EXTENSIONS

    def _select_files(self, all_blobs: list) -> list:
        selected = []
        seen_paths = set()

        def add(item):
            if item["path"] not in seen_paths and not self._is_sensitive(item["path"]):
                selected.append(item)
                seen_paths.add(item["path"])

        # Pass 1: priority names (README, manifests, config)
        for item in all_blobs:
            basename = item["path"].rsplit("/", 1)[-1].lower()
            if basename in _PRIORITY_NAMES:
                add(item)

        # Pass 2: entry-point files (pick only top-level or src/ ones first)
        for item in all_blobs:
            basename = item["path"].rsplit("/", 1)[-1].lower()
            depth = item["path"].count("/")
            if basename in _ENTRY_NAMES and depth <= 1:
                add(item)

        # Pass 3: deeper entry-points if we have room
        for item in all_blobs:
            basename = item["path"].rsplit("/", 1)[-1].lower()
            if basename in _ENTRY_NAMES:
                add(item)
            if len(selected) >= 20:
                break

        # Pass 4: first CI workflow
        for item in all_blobs:
            path = item["path"]
            if path.startswith(".github/workflows/") and path.endswith(".yml"):
                if not any(s["path"].startswith(".github/workflows/") for s in selected):
                    add(item)
                break

        return selected

    # ------------------------------------------------------------------ #
    # Prompt + parsing
    # ------------------------------------------------------------------ #

    def _build_prompt(self, repo: str, tree_lines: list, files: dict, hipaa: bool = False) -> str:
        tree_text = "\n".join(tree_lines[:400])
        truncated = len(tree_lines) > 400
        files_text = ""
        for path, content in files.items():
            files_text += f"\n--- {path} ---\n{content}\n"

        hipaa_section = (
            "\n## HIPAA Compliance\n"
            "This assessment must evaluate HIPAA compliance. In addition to the standard "
            "security findings, flag any of the following as high or critical severity:\n"
            "- PHI (Protected Health Information) stored or transmitted without encryption\n"
            "- PHI appearing in logs, error messages, or debug output\n"
            "- Missing or insufficient access controls around health data\n"
            "- Absence of audit trails for PHI access or modification\n"
            "- Data access broader than the minimum necessary principle allows\n"
            "- PHI used in test fixtures, seeds, or non-production environments\n"
            "- Third-party integrations that may receive PHI without BAA consideration\n"
        ) if hipaa else ""

        return (
            f"# Repository: {repo}\n\n"
            f"## File tree ({len(tree_lines)} files total"
            + (", first 400 shown" if truncated else "")
            + ")\n"
            f"```\n{tree_text}\n```\n\n"
            f"## Key file contents\n{files_text}"
            f"{hipaa_section}"
        )

    def _extract_tool_input(self, response) -> dict:
        message = response.choices[0].message
        for call in getattr(message, "tool_calls", None) or []:
            if call.function.name == "submit_assessment":
                return json.loads(call.function.arguments)
        raise RuntimeError("Model did not return a submit_assessment function call.")
