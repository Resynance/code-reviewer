"""Regression tests for Vercel route rewrites."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_worker_routes_reach_api_function_before_spa_fallback():
    config = json.loads((ROOT / "vercel.json").read_text())
    rewrites = config["rewrites"]
    sources = [rewrite["source"] for rewrite in rewrites]

    worker_index = sources.index("/worker/(.*)")
    fallback_index = next(
        index for index, rewrite in enumerate(rewrites) if rewrite["destination"] == "/index.html"
    )

    assert rewrites[worker_index]["destination"] == "/api/index"
    assert worker_index < fallback_index
    assert "worker/" in rewrites[fallback_index]["source"]
