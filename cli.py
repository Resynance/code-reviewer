#!/usr/bin/env python3
"""
cli.py — command-line tools for the Code Review Tool.

Usage:
    python cli.py backfill <owner/repo> [pages]

`backfill` imports closed pull requests from a GitHub repo into the decision
store so the reviewer has historical precedent to draw on.

The GitHub token is read from config.json (set via the Settings page) or, as a
fallback, the GITHUB_TOKEN environment variable.
"""

import sys
from pathlib import Path

# Make the core modules importable the same way the backend does.
sys.path.insert(0, str(Path(__file__).parent / "core"))

import config_store  # noqa: E402
from decision_store import create_store  # noqa: E402
from github_backfill import backfill as run_backfill  # noqa: E402


def backfill(repo: str, pages: int = 5) -> int:
    token = config_store.get_github_token()
    if not token:
        print(
            "ERROR: no GitHub token configured. Set it on the Settings page or "
            "via the GITHUB_TOKEN environment variable."
        )
        return 1

    store = create_store()
    try:
        imported = run_backfill(
            repo,
            pages,
            token,
            store,
            on_page=lambda page, count: print(f"  page {page}: imported {count} PRs"),
        )
    except (ValueError, RuntimeError) as e:
        print(f"ERROR: {e}")
        return 1

    print(f"Done. Imported {imported} decisions into the store.")
    return 0


def main(argv) -> int:
    if len(argv) < 2 or argv[1] != "backfill":
        print(__doc__.strip())
        return 1
    if len(argv) < 3:
        print("ERROR: missing <owner/repo>.\n")
        print("Usage: python cli.py backfill <owner/repo> [pages]")
        return 1

    repo = argv[2]
    pages = int(argv[3]) if len(argv) > 3 else 5
    return backfill(repo, pages)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
