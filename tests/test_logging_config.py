import logging
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import warnings

import pytest

from logging_config import LoggedFlask, configure_logging, register_request_logging


@pytest.fixture
def logged_app(tmp_path, monkeypatch):
    root = logging.getLogger()
    original_level = root.level
    existing = [handler for handler in root.handlers if getattr(handler, "kursplan_handler", False)]
    for handler in existing:
        root.removeHandler(handler)
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("LOG_MAX_BYTES", "10485760")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "5")
    monkeypatch.setenv("DEBUG", "false")
    # Återställ processens globala hooks efter varje test.
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    monkeypatch.setattr(warnings, "showwarning", warnings.showwarning)
    app = LoggedFlask("logging-tests", root_path=str(tmp_path))
    yield app
    for handler in root.handlers[:]:
        if getattr(handler, "kursplan_handler", False):
            root.removeHandler(handler)
            handler.close()
    for handler in existing:
        root.addHandler(handler)
    root.setLevel(original_level)


def _logs(app):
    return Path(app.config["LOG_FILE"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("setting,debug", [(None, False), ("false", False), ("0", False), ("true", True), (" On ", True), ("1", True)])
def test_levels_in_file_and_console_follow_debug_setting(logged_app, monkeypatch, capsys, setting, debug):
    if setting is None:
        monkeypatch.delenv("DEBUG")
    else:
        monkeypatch.setenv("DEBUG", setting)
    configure_logging(logged_app)
    for level in (logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR, logging.CRITICAL):
        logged_app.logger.log(level, "level-marker-%s", logging.getLevelName(level))
    # Även en uttryckligen DEBUG-aktiverad barnlogger filtreras av handlers.
    child = logging.getLogger("database.logging-test")
    child.setLevel(logging.DEBUG)
    try:
        child.debug("child-debug-marker")
    finally:
        child.setLevel(logging.NOTSET)
    for output in (_logs(logged_app), capsys.readouterr().err):
        assert ("level-marker-DEBUG" in output) is debug
        assert ("child-debug-marker" in output) is debug
        for level in ("INFO", "WARNING", "ERROR", "CRITICAL"):
            assert f"level-marker-{level}" in output
    assert not logged_app.debug


def test_request_logs_cover_success_http_errors_and_exceptions_without_private_input(logged_app):
    configure_logging(logged_app)
    register_request_logging(logged_app)

    @logged_app.route("/items/<int:item_id>", methods=["POST"])
    def item(item_id):
        return {"id": item_id}

    @logged_app.route("/broken")
    def broken():
        raise ValueError("private-exception-secret@example.test")

    client = logged_app.test_client()
    ok = client.post("/items/7?token=private-query", data={"password": "private-password"},
                     headers={"Authorization": "Bearer private-auth", "Cookie": "private-cookie"})
    missing = client.get("/private-url-name?email=private-email@example.test")
    failed = client.get("/broken")
    assert [ok.status_code, missing.status_code, failed.status_code] == [200, 404, 500]
    assert len({response.headers["X-Request-ID"] for response in (ok, missing, failed)}) == 3
    output = _logs(logged_app)
    assert "route=/items/<int:item_id> status=200" in output
    assert "WARNING" in output and "status=404" in output
    assert "ERROR" in output and "status=500" in output
    assert "duration_ms=" in output
    assert "ValueError" in output and "test_logging_config.py:" in output
    assert output.count("Ohanterat fel under HTTP-anrop.") == 1
    for value in ("private-query", "private-password", "private-auth", "private-cookie", "private-url-name", "private-email", "private-exception"):
        assert value not in output


def test_propagated_exceptions_are_logged(logged_app):
    configure_logging(logged_app)
    register_request_logging(logged_app)
    logged_app.config["TESTING"] = True

    @logged_app.route("/broken")
    def broken():
        raise RuntimeError("private-error-detail")

    with pytest.raises(RuntimeError):
        logged_app.test_client().get("/broken")
    output = _logs(logged_app)
    assert "HTTP-anrop avbrutet" in output
    assert "RuntimeError" in output
    assert "private-error-detail" not in output


def test_rotation_is_bounded_and_reconfiguration_does_not_duplicate_records(logged_app, monkeypatch):
    monkeypatch.setenv("LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("LOG_BACKUP_COUNT", "2")
    configure_logging(logged_app)
    for index in range(80):
        logged_app.logger.info("Rotationskontroll nummer %s: %s", index, "x" * 60)
    path = Path(logged_app.config["LOG_FILE"])
    assert {p.name for p in path.parent.iterdir()} == {"kursplan.log", "kursplan.log.1", "kursplan.log.2"}
    assert all(p.stat().st_size <= 1024 for p in path.parent.iterdir())
    configure_logging(logged_app)
    logged_app.logger.info("unique-after-reconfigure")
    output = "".join(p.read_text(encoding="utf-8") for p in path.parent.iterdir())
    assert output.count("unique-after-reconfigure") == 1


@pytest.mark.parametrize("key,value", [("LOG_MAX_BYTES", "0"), ("LOG_MAX_BYTES", "invalid-secret"), ("LOG_BACKUP_COUNT", "-1")])
def test_invalid_rotation_settings_abort_startup_without_echoing_values(logged_app, monkeypatch, capsys, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        configure_logging(logged_app)
    output = capsys.readouterr().err
    assert "CRITICAL" in output and "Loggningen kunde inte initieras" in output
    assert "invalid-secret" not in output


def test_unwritable_log_directory_aborts_startup(logged_app, tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("existing", encoding="utf-8")
    monkeypatch.setenv("LOG_DIR", str(blocked))
    with pytest.raises(OSError):
        configure_logging(logged_app)
    assert "CRITICAL" in capsys.readouterr().err
    assert blocked.read_text(encoding="utf-8") == "existing"


def test_python_warnings_process_and_thread_failures_are_sanitized(logged_app):
    configure_logging(logged_app)
    warnings.warn("private-warning-value", RuntimeWarning)
    try:
        raise RuntimeError("private-failure-value")
    except RuntimeError:
        info = sys.exc_info()
        sys.excepthook(*info)
        threading.excepthook(SimpleNamespace(exc_type=info[0], exc_value=info[1], exc_traceback=info[2]))
    output = _logs(logged_app)
    assert "Python-varning" in output and "RuntimeWarning" in output
    assert output.count("CRITICAL") == 2
    assert "RuntimeError" in output
    assert "private-warning-value" not in output and "private-failure-value" not in output


def test_relative_log_directory_and_raw_server_driver_logs(logged_app, monkeypatch):
    monkeypatch.setenv("LOG_DIR", "relative-logs")
    configure_logging(logged_app)
    assert Path(logged_app.config["LOG_FILE"]) == Path(logged_app.root_path) / "relative-logs" / "kursplan.log"
    logging.getLogger("werkzeug").warning("private-url?token=private-token")
    logging.getLogger("werkzeug").info('127.0.0.1 - - [timestamp] "%s" %s %s', "GET /private-token HTTP/1.1", "200", "-")
    logging.getLogger("psycopg").warning("password=private-database-password")
    output = _logs(logged_app)
    assert "HTTP-serverhändelse" in output and "Databasdrivrutinshändelse" in output
    assert output.count("HTTP-serverhändelse") == 1
    assert "private-" not in output


def test_rotation_failure_reports_critical_error_without_raw_record(logged_app, monkeypatch, capfd):
    configure_logging(logged_app)
    handler = next(handler for handler in logging.getLogger().handlers if hasattr(handler, "doRollover"))

    def fail_rotation():
        raise OSError("private-filesystem-detail")

    monkeypatch.setattr(handler, "shouldRollover", lambda record: True)
    monkeypatch.setattr(handler, "doRollover", fail_rotation)
    logging.getLogger("psycopg").error("password=private-password")
    output = capfd.readouterr().err
    assert "CRITICAL kursplan.logging" in output
    assert "private-password" not in output and "private-filesystem-detail" not in output


def test_request_flags_reset_when_application_context_is_reused(logged_app):
    configure_logging(logged_app)
    register_request_logging(logged_app)
    logged_app.config["TESTING"] = True

    @logged_app.route("/ok")
    def ok():
        return "ok"

    @logged_app.route("/broken")
    def broken():
        raise RuntimeError("private-error")

    with logged_app.app_context():
        client = logged_app.test_client()
        client.get("/ok")
        with pytest.raises(RuntimeError):
            client.get("/broken")
    assert "HTTP-anrop avbrutet" in _logs(logged_app)
