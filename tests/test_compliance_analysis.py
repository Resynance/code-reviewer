import concurrent.futures
import threading


def test_apply_suggestion_preserves_concurrent_repo_policy_updates(cfg, monkeypatch):
    import compliance_analysis

    cfg.save_config({
        "compliance_policies": {
            "default": {"enabled": True, "approved_vendors": ["aws"]},
            "repos": {
                "org/a": {"approved_vendors": ["aws"]},
                "org/b": {"approved_vendors": ["aws"]},
            },
        },
    })
    original_get_policies = cfg.get_compliance_policies
    reads_started = threading.Barrier(2)

    def read_after_both_requests_started():
        policies = original_get_policies()
        reads_started.wait(timeout=5)
        return policies

    monkeypatch.setattr(
        compliance_analysis.config_store,
        "get_compliance_policies",
        read_after_both_requests_started,
    )

    suggestions = [
        (
            "org/a",
            {"type": "add_vendor", "field": "approved_vendors", "value": "sentry", "action": "add"},
        ),
        (
            "org/b",
            {"type": "add_vendor", "field": "approved_vendors", "value": "datadog", "action": "add"},
        ),
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(compliance_analysis.apply_suggestion, repo, suggestion)
            for repo, suggestion in suggestions
        ]
        for future in futures:
            future.result(timeout=5)

    policies = original_get_policies()
    assert "sentry" in policies["repos"]["org/a"]["approved_vendors"]
    assert "datadog" in policies["repos"]["org/b"]["approved_vendors"]
