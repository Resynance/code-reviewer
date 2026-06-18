"""
github_backfill.py — fetch GitHub PRs for the decision store.

Provides:
  - backfill(...)       import closed PRs as decisions (CLI + /api/backfill)
  - list_open_prs(...)  list a repo's open PRs (for /api/repos/open-prs)
  - pr_doc_id(...)      the canonical decision id for a PR — shared so the
                        "already backfilled?" check matches what backfill writes
"""

from datetime import datetime, timezone

GITHUB_API = "https://api.github.com"
PER_PAGE = 100


def pr_doc_id(repo: str, number) -> str:
    """The decision store doc_id for a given repo + PR number."""
    return f"{repo.replace('/', '-')}-pr-{number}"


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _validate(repo: str, token: str):
    if "/" not in repo:
        raise ValueError(f"repo must be in 'owner/repo' form, got {repo!r}")
    if not token:
        raise ValueError("GitHub token is not configured")


def _raise_for_status(resp, repo: str):
    if resp.status_code == 404:
        raise RuntimeError(
            f"Repo '{repo}' not found. Check the owner/repo name; if it's "
            "private, your GitHub token needs 'repo' scope and access to it "
            "(GitHub returns 404 rather than 403 for repos the token can't see)."
        )
    if resp.status_code in (401, 403):
        raise RuntimeError(
            "GitHub authentication failed. Check that the GitHub token is "
            "valid and has 'repo' read scope."
        )
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub returned {resp.status_code}: {resp.text[:200]}")


def _outcome_for(pr: dict) -> str:
    """Map a GitHub PR's final state to a decision outcome label."""
    if pr.get("merged_at"):
        return "approved_and_merged"
    if pr.get("state") == "closed":
        return "closed_without_merge"
    return "changes_requested"


def backfill(repo: str, pages: int, token: str, store, on_page=None) -> int:
    """Import up to `pages` pages of closed PRs from `repo` into `store`.

    Returns the number of decisions imported. Raises ValueError for bad input
    and RuntimeError if GitHub returns an error. `on_page(page, count)` is called
    after each page for progress reporting.
    """
    import httpx

    _validate(repo, token)

    imported = 0
    with httpx.Client(timeout=30.0, headers=_headers(token)) as client:
        for page in range(1, pages + 1):
            resp = client.get(
                f"{GITHUB_API}/repos/{repo}/pulls",
                params={"state": "closed", "per_page": PER_PAGE, "page": page},
            )
            _raise_for_status(resp, repo)

            prs = resp.json()
            if not prs:
                break  # No more closed PRs.

            for pr in prs:
                number = pr.get("number")
                date = (
                    pr.get("merged_at")
                    or pr.get("closed_at")
                    or datetime.now(timezone.utc).isoformat()
                )
                store.upsert(
                    doc_id=pr_doc_id(repo, number),
                    ref=f"PR #{number}",
                    summary=pr.get("title", "") or f"PR #{number}",
                    reasoning=(pr.get("body") or "").strip(),
                    outcome=_outcome_for(pr),
                    date=date,
                    metadata={
                        "repo": repo,
                        "author": (pr.get("user") or {}).get("login", "unknown"),
                        "url": pr.get("html_url", ""),
                    },
                )
                imported += 1

            if on_page:
                on_page(page, len(prs))

    return imported


def _pr_summary(pr: dict) -> dict:
    return {
        "number": pr.get("number"),
        "title": pr.get("title", ""),
        "author": (pr.get("user") or {}).get("login", "unknown"),
        "url": pr.get("html_url", ""),
        "state": pr.get("state", "open"),
        "draft": bool(pr.get("draft", False)),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
    }


def list_prs(repo: str, token: str, state: str = "all", pages: int = 3) -> list:
    """Return a repo's PRs as summary dicts, most-recently-updated first.

    `state` is one of GitHub's `open` / `closed` / `all`. Raises ValueError /
    RuntimeError on the same conditions as backfill().
    """
    import httpx

    _validate(repo, token)

    out = []
    with httpx.Client(timeout=30.0, headers=_headers(token)) as client:
        for page in range(1, pages + 1):
            resp = client.get(
                f"{GITHUB_API}/repos/{repo}/pulls",
                params={"state": state, "per_page": PER_PAGE, "page": page,
                        "sort": "updated", "direction": "desc"},
            )
            _raise_for_status(resp, repo)

            prs = resp.json()
            if not prs:
                break
            out.extend(_pr_summary(pr) for pr in prs)

    return out


def list_open_prs(repo: str, token: str, pages: int = 5) -> list:
    """Return a repo's open PRs as summary dicts."""
    return list_prs(repo, token, state="open", pages=pages)


def fetch_pr(repo: str, number, token: str) -> dict:
    """Fetch one PR's metadata + unified diff + changed files, shaped for the
    review form. Raises ValueError / RuntimeError on the usual conditions.
    """
    import httpx

    _validate(repo, token)

    base = f"{GITHUB_API}/repos/{repo}/pulls/{number}"
    with httpx.Client(timeout=30.0, headers=_headers(token)) as client:
        meta_resp = client.get(base)
        _raise_for_status(meta_resp, repo)
        meta = meta_resp.json()

        diff_resp = client.get(base, headers={"Accept": "application/vnd.github.v3.diff"})
        _raise_for_status(diff_resp, repo)
        diff = diff_resp.text

        files_resp = client.get(f"{base}/files", params={"per_page": PER_PAGE})
        files = (
            [f.get("filename") for f in files_resp.json()]
            if files_resp.status_code == 200 else []
        )

    return {
        "pr_number": meta.get("number", number),
        "repo": repo,
        "title": meta.get("title", ""),
        "description": meta.get("body") or "",
        "author": (meta.get("user") or {}).get("login", "unknown"),
        "base_branch": (meta.get("base") or {}).get("ref", "main"),
        "diff": diff,
        "files_changed": files,
    }
