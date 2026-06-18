"""
github_backfill.py — import closed GitHub PRs into the decision store.

Shared by the CLI (`cli.py backfill`) and the API (`POST /api/backfill`) so the
two stay in sync. Each closed PR becomes one decision: title as summary, body as
reasoning, merge state as outcome.
"""

from datetime import datetime, timezone

GITHUB_API = "https://api.github.com"
PER_PAGE = 100


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

    if "/" not in repo:
        raise ValueError(f"repo must be in 'owner/repo' form, got {repo!r}")
    if not token:
        raise ValueError("GitHub token is not configured")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    imported = 0
    with httpx.Client(timeout=30.0, headers=headers) as client:
        for page in range(1, pages + 1):
            resp = client.get(
                f"{GITHUB_API}/repos/{repo}/pulls",
                params={"state": "closed", "per_page": PER_PAGE, "page": page},
            )
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
                raise RuntimeError(
                    f"GitHub returned {resp.status_code}: {resp.text[:200]}"
                )

            prs = resp.json()
            if not prs:
                break  # No more closed PRs.

            for pr in prs:
                number = pr.get("number")
                doc_id = f"{repo.replace('/', '-')}-pr-{number}"
                date = (
                    pr.get("merged_at")
                    or pr.get("closed_at")
                    or datetime.now(timezone.utc).isoformat()
                )
                store.upsert(
                    doc_id=doc_id,
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
