"""Unit tests for core/rate_limit.py — sliding window logic."""

import time
import rate_limit


def setup_function():
    rate_limit._windows.clear()


def test_allows_up_to_limit(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "3")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    for _ in range(3):
        allowed, retry = rate_limit.check("user@example.com")
        assert allowed
        assert retry == 0


def test_blocks_on_limit_exceeded(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    rate_limit.check("u")
    rate_limit.check("u")
    allowed, retry = rate_limit.check("u")
    assert not allowed
    assert retry > 0


def test_retry_after_is_positive_seconds(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "30")
    rate_limit.check("u")
    allowed, retry = rate_limit.check("u")
    assert not allowed
    # retry_after should be within the window, not negative or zero
    assert 0 < retry <= 30


def test_disabled_when_limit_is_zero(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "0")
    for _ in range(100):
        allowed, retry = rate_limit.check("u")
        assert allowed
        assert retry == 0


def test_keys_are_independent(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    allowed_a, _ = rate_limit.check("alice@example.com")
    allowed_b, _ = rate_limit.check("bob@example.com")
    assert allowed_a
    assert allowed_b
    # both are now at limit — further calls blocked independently
    assert not rate_limit.check("alice@example.com")[0]
    assert not rate_limit.check("bob@example.com")[0]


def test_old_timestamps_are_evicted(monkeypatch):
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "2")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "1")
    rate_limit.check("u")
    rate_limit.check("u")
    assert not rate_limit.check("u")[0]

    # Backdate existing timestamps so they fall outside the window.
    dq = rate_limit._windows["u"]
    for i in range(len(dq)):
        dq[i] -= 2  # 2 seconds ago, window is 1s

    # Next call should evict old entries and allow through.
    allowed, _ = rate_limit.check("u")
    assert allowed


def test_prefixed_keys_are_independent(monkeypatch):
    """review: and assess: buckets for the same user don't interfere."""
    monkeypatch.setenv("RATE_LIMIT_REQUESTS", "1")
    monkeypatch.setenv("RATE_LIMIT_WINDOW", "60")
    allowed_r, _ = rate_limit.check("review:u@x.com")
    allowed_a, _ = rate_limit.check("assess:u@x.com")
    assert allowed_r
    assert allowed_a
