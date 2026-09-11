import logging

from collector import run_logging


def test_collection_logging_writes_to_stdout_and_rotating_file(
    tmp_path, monkeypatch, capsys
):
    logger = logging.getLogger(run_logging.LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    if hasattr(logger, "_jmm_configured"):
        delattr(logger, "_jmm_configured")

    log_path = tmp_path / "collection.log"
    monkeypatch.setattr(run_logging, "LOG_PATH", log_path)
    configured = run_logging.configure_collection_logging()
    configured.info("logging-probe run_id=42")
    for handler in configured.handlers:
        handler.flush()

    assert "logging-probe run_id=42" in capsys.readouterr().out
    assert "logging-probe run_id=42" in log_path.read_text(encoding="utf-8")

    for handler in list(configured.handlers):
        configured.removeHandler(handler)
        handler.close()
    if hasattr(configured, "_jmm_configured"):
        delattr(configured, "_jmm_configured")
