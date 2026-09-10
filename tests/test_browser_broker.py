from collector.browser_broker import BrowserBrokerError


def test_browser_broker_error_is_runtime_error():
    assert issubclass(BrowserBrokerError, RuntimeError)


def test_broker_source_uses_base64_response_transport():
    import inspect

    import collector.browser_broker as broker

    source = inspect.getsource(broker.browser_command)
    assert "ToBase64String" in source
    assert "b64decode" in source
