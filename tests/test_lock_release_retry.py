"""lock_release._get_json: retry with backoff on transient failures only."""

import urllib.error

import pytest

from tools import lock_release


class _Resp:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(req, timeout=30):
        calls["n"] += 1
        if calls["n"] < 3:
            raise urllib.error.URLError("throttled")
        return _Resp()

    monkeypatch.setattr(lock_release.urllib.request, "urlopen", flaky)
    monkeypatch.setattr(lock_release.time, "sleep", lambda s: None)
    assert lock_release._get_json("https://x") == {}
    assert calls["n"] == 3


def test_http_429_and_5xx_retried_4xx_not(monkeypatch):
    def err(code):
        def f(req, timeout=30):
            raise urllib.error.HTTPError("u", code, "m", {}, None)
        return f

    monkeypatch.setattr(lock_release.time, "sleep", lambda s: None)
    monkeypatch.setattr(lock_release.urllib.request, "urlopen", err(429))
    with pytest.raises(urllib.error.HTTPError):
        lock_release._get_json("https://x", attempts=2)
    monkeypatch.setattr(lock_release.urllib.request, "urlopen", err(503))
    with pytest.raises(urllib.error.HTTPError):
        lock_release._get_json("https://x", attempts=2)
    monkeypatch.setattr(lock_release.urllib.request, "urlopen", err(404))
    with pytest.raises(urllib.error.HTTPError):
        lock_release._get_json("https://x", attempts=4)  # immediate


def test_permanent_network_failure_raises_after_attempts(monkeypatch):
    monkeypatch.setattr(
        lock_release.urllib.request, "urlopen",
        lambda req, timeout=30: (_ for _ in ()).throw(TimeoutError()),
    )
    monkeypatch.setattr(lock_release.time, "sleep", lambda s: None)
    with pytest.raises(TimeoutError):
        lock_release._get_json("https://x", attempts=3)
