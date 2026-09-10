from collector.browser_broker import BrowserBrokerError


def test_browser_broker_error_is_runtime_error():
    assert issubclass(BrowserBrokerError, RuntimeError)


def test_browser_uses_visible_persistent_jmm_profile():
    import inspect

    import collector.browser_broker as broker

    source = inspect.getsource(broker.start_browser)
    assert "launch_persistent_context" in source
    assert "headless=False" in source
    assert broker.SEEK_PLAYWRIGHT_USER_DATA_DIR.name == "playwright_jmm_seek_user_data"


def test_snapshot_retries_one_broker_timeout(monkeypatch):
    import collector.browser_broker as broker

    calls = []

    def fake_command(command, payload=None, *, page_id=None, **_kwargs):
        calls.append((command, page_id))
        if len(calls) == 1:
            raise broker.BrowserBrokerTimeout("transient")
        return broker.BrokerResponse(
            result={"url": "https://au.seek.com/jobs"}, elapsed_seconds=0.1
        )

    monkeypatch.setattr(broker, "browser_command", fake_command)
    monkeypatch.setattr(broker.time, "sleep", lambda *_args: None)
    result = broker.snapshot(42)
    assert result.result["url"] == "https://au.seek.com/jobs"
    assert calls == [("snapshot", 42), ("snapshot", 42)]


def test_snapshot_fails_after_second_timeout(monkeypatch):
    import pytest

    import collector.browser_broker as broker

    calls = []

    def always_timeout(command, payload=None, *, page_id=None, **_kwargs):
        calls.append((command, page_id))
        raise broker.BrowserBrokerTimeout("still stalled")

    monkeypatch.setattr(broker, "browser_command", always_timeout)
    monkeypatch.setattr(broker.time, "sleep", lambda *_args: None)
    with pytest.raises(broker.BrowserBrokerTimeout):
        broker.snapshot(42)
    assert calls == [("snapshot", 42), ("snapshot", 42)]
