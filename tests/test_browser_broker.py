from collector.browser_broker import BrowserBrokerError


def test_browser_broker_error_is_runtime_error():
    assert issubclass(BrowserBrokerError, RuntimeError)


def test_browser_attaches_to_long_lived_jmm_chrome():
    import inspect

    import collector.browser_broker as broker

    start_source = inspect.getsource(broker.start_browser)
    close_source = inspect.getsource(broker.close_browser)
    assert "connect_over_cdp" in start_source
    assert "launch_persistent_context" not in start_source
    assert "_context.close" not in close_source
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


def test_snapshot_fails_after_bounded_timeout_retries(monkeypatch):
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
    assert calls == [("snapshot", 42)] * 12


def test_snapshot_retries_navigation_race(monkeypatch):
    import collector.browser_broker as broker

    calls = []

    def fake_command(command, payload=None, *, page_id=None, **_kwargs):
        calls.append((command, page_id))
        if len(calls) == 1:
            raise broker.BrowserBrokerError(
                "Page.evaluate: Execution context was destroyed, most likely because of a navigation"
            )
        return broker.BrokerResponse(
            result={"url": "https://au.seek.com/jobs"}, elapsed_seconds=0.1
        )

    monkeypatch.setattr(broker, "browser_command", fake_command)
    monkeypatch.setattr(broker.time, "sleep", lambda *_args: None)
    result = broker.snapshot(42)
    assert result.result["url"] == "https://au.seek.com/jobs"
    assert calls == [("snapshot", 42), ("snapshot", 42)]


def test_browser_lost_classifies_only_recoverable_transport_failures():
    import collector.browser_broker as broker

    assert broker._browser_lost(
        broker.BrowserBrokerError("Target page, context or browser has been closed")
    )
    assert broker._browser_lost(
        broker.BrowserBrokerError("stale JMM browser page id: 17")
    )
    assert not broker._browser_lost(
        broker.BrowserBrokerError("SEEK human-check wait expired")
    )


def test_recover_browser_pages_preserves_page_ids_and_reuses_matching_tabs(monkeypatch):
    import collector.browser_broker as broker

    class FakePage:
        def __init__(self, url):
            self.url = url
            self.goto_calls = []

        def is_closed(self):
            return False

        def goto(self, url, **kwargs):
            self.url = url
            self.goto_calls.append((url, kwargs))

    class FakeContext:
        def __init__(self, pages):
            self.pages = pages
            self.created = []
            self.init_scripts = []

        def add_init_script(self, script):
            self.init_scripts.append(script)

        def new_page(self):
            page = FakePage("about:blank")
            self.created.append(page)
            return page

    class FakeChromium:
        def __init__(self, browser):
            self.browser = browser
            self.calls = []

        def connect_over_cdp(self, url, timeout):
            self.calls.append((url, timeout))
            return self.browser

    target_a = "https://au.seek.com/jobs/in-Australian-Capital-Territory-ACT"
    target_b = "https://au.seek.com/jobs-in-legal/in-Australian-Capital-Territory-ACT"
    existing = FakePage(target_a)
    context = FakeContext([existing])
    browser = type("FakeBrowser", (), {"contexts": [context]})()
    chromium = FakeChromium(browser)
    fake_pw = type("FakePlaywright", (), {"chromium": chromium})()

    monkeypatch.setattr(broker, "_pw", fake_pw)
    monkeypatch.setattr(broker, "_browser", object())
    monkeypatch.setattr(broker, "_context", object())
    monkeypatch.setattr(broker, "_pages", {41: FakePage("stale")})
    monkeypatch.setattr(broker, "_page_targets", {41: target_a, 42: target_b})
    monkeypatch.setattr(broker, "_ensure_browser_service", lambda: None)

    broker._recover_browser_pages()

    assert chromium.calls == [(broker.JMM_BROWSER_CDP_URL, 5000)]
    assert broker._pages[41] is existing
    assert broker._pages[42] is context.created[0]
    assert context.created[0].goto_calls == [
        (target_b, {"wait_until": "domcontentloaded", "timeout": 30000})
    ]
    assert context.init_scripts == [broker._WEBDRIVER_INIT]


def test_browser_command_recovers_once_then_retries_same_command(monkeypatch):
    import collector.browser_broker as broker

    calls = []
    recoveries = []

    def fake_execute(command, payload, *, page_id, timeout_seconds):
        calls.append((command, payload, page_id, timeout_seconds))
        if len(calls) == 1:
            raise broker.BrowserBrokerError(
                "Target page, context or browser has been closed"
            )
        return {"url": "https://au.seek.com/jobs"}

    monkeypatch.setattr(broker, "start_browser", lambda: None)
    monkeypatch.setattr(broker, "_execute_browser_command", fake_execute)
    monkeypatch.setattr(broker, "_recover_browser_pages", lambda: recoveries.append(True))

    result = broker.browser_command(
        "snapshot", {"verbose": True}, page_id=77, timeout_seconds=9
    )

    assert result.result == {"url": "https://au.seek.com/jobs"}
    assert recoveries == [True]
    assert calls == [
        ("snapshot", {"verbose": True}, 77, 9),
        ("snapshot", {"verbose": True}, 77, 9),
    ]


def test_browser_command_does_not_loop_recovery_after_retry(monkeypatch):
    import pytest

    import collector.browser_broker as broker

    calls = []
    recoveries = []

    def always_lost(command, payload, *, page_id, timeout_seconds):
        calls.append((command, page_id))
        raise broker.BrowserBrokerError("browser has been closed")

    monkeypatch.setattr(broker, "start_browser", lambda: None)
    monkeypatch.setattr(broker, "_execute_browser_command", always_lost)
    monkeypatch.setattr(broker, "_recover_browser_pages", lambda: recoveries.append(True))

    with pytest.raises(broker.BrowserBrokerError, match="browser has been closed"):
        broker.browser_command("snapshot", page_id=77)

    assert recoveries == [True]
    assert calls == [("snapshot", 77), ("snapshot", 77)]


def test_browser_command_recovers_from_playwright_browser_loss(monkeypatch):
    import collector.browser_broker as broker

    class FakePlaywrightError(Exception):
        pass

    calls = []
    recoveries = []

    def fake_execute(command, payload, *, page_id, timeout_seconds):
        calls.append(command)
        if len(calls) == 1:
            raise FakePlaywrightError("Target page, context or browser has been closed")
        return {"ok": True}

    monkeypatch.setattr(broker, "PlaywrightError", FakePlaywrightError)
    monkeypatch.setattr(broker, "start_browser", lambda: None)
    monkeypatch.setattr(broker, "_execute_browser_command", fake_execute)
    monkeypatch.setattr(broker, "_recover_browser_pages", lambda: recoveries.append(True))

    result = broker.browser_command("snapshot", page_id=88)

    assert result.result == {"ok": True}
    assert recoveries == [True]
    assert calls == ["snapshot", "snapshot"]
