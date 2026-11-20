"""Gemensam, beständig loggning utan formulärdata eller hemligheter."""

import atexit
import copy
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from uuid import uuid4
import warnings

from flask import Flask, g, has_request_context, request
from flask.logging import default_handler, wsgi_errors_stream


TRUE_VALUES = {"1", "true", "yes", "on"}
_exit_registered = False


class SafeFormatter(logging.Formatter):
    """Behåll felsökbara stackramar, men aldrig feltext, källkod eller lokaler."""

    converter = time.gmtime

    def formatException(self, exc_info):
        exception = exc_info[1]
        lines = ["Traceback (feltext och variabelvärden utelämnas):"]
        seen = set()
        while exception is not None and id(exception) not in seen:
            seen.add(id(exception))
            for frame, lineno in traceback.walk_tb(exception.__traceback__):
                lines.append(f"  {Path(frame.f_code.co_filename).name}:{lineno} i {frame.f_code.co_name}")
            lines.append(type(exception).__name__)
            exception = exception.__cause__ or (
                None if exception.__suppress_context__ else exception.__context__
            )
        return "\n".join(lines)

    def format(self, record):
        # Ett annat formatters cache kan innehålla osanerad exceptiontext.
        record = copy.copy(record)
        record.exc_text = None
        if record.name == "werkzeug":
            # Serverns råa loggar kan innehålla IP, URL-parametrar, headers och
            # exceptiontext. Flask-anrop loggas separat med route-mallen.
            record.msg = "HTTP-serverhändelse (rått meddelande utelämnat)."
            record.args = ()
        if record.name.startswith("psycopg"):
            record.msg = "Databasdrivrutinshändelse (anslutningsuppgifter utelämnade)."
            record.args = ()
        record.stack_info = None
        return super().format(record)


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(g, "log_request_id", "-") if has_request_context() else "-"
        # Undvik dubbla åtkomstrader och den råa server-URL:en.
        return not (
            record.name == "werkzeug" and record.levelno == logging.INFO
            and isinstance(record.msg, str) and record.msg.endswith('\"%s\" %s %s')
            and isinstance(record.args, tuple) and len(record.args) == 3
        )


class SafeHandlerErrors:
    def handleError(self, record):
        # stdlib skriver annars ut original-record/feltext vid t.ex. full disk.
        # Gå direkt till stderr för att undvika rekursion via filhandlern.
        try:
            os.write(2, b"CRITICAL kursplan.logging: Loggning misslyckades; kontrollera lagring och konsol.\n")
        except OSError:
            pass


class SafeStreamHandler(SafeHandlerErrors, logging.StreamHandler):
    pass


class SafeRotatingFileHandler(SafeHandlerErrors, RotatingFileHandler):
    def _open(self):
        descriptor = os.open(self.baseFilename, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        return os.fdopen(descriptor, self.mode, encoding=self.encoding, errors=self.errors)


class LoggedFlask(Flask):
    def log_exception(self, exc_info):
        self.logger.error("Ohanterat fel under HTTP-anrop.", exc_info=exc_info)
        g.log_exception_recorded = True


def _positive_integer(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ValueError(f"{name} måste vara ett positivt heltal.") from None
    if value <= 0:
        raise ValueError(f"{name} måste vara ett positivt heltal.")
    return value


def configure_logging(app):
    """Anropas efter dotenv och före initiering av databasen."""
    global _exit_registered
    debug = os.getenv("DEBUG", "false").strip().lower() in TRUE_VALUES
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    formatter = SafeFormatter(
        "%(asctime)sZ %(levelname)s %(name)s pid=%(process)d "
        "request_id=%(request_id)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    console = SafeStreamHandler(wsgi_errors_stream)
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(RequestContextFilter())

    # Behåll externa handlers (exempelvis pytest), ersätt bara våra egna.
    for handler in root.handlers[:]:
        if getattr(handler, "kursplan_handler", False):
            root.removeHandler(handler)
            handler.close()
    console.kursplan_handler = True
    root.addHandler(console)
    root.setLevel(level)
    app.logger.removeHandler(default_handler)
    app.logger.setLevel(level)
    app.logger.propagate = True
    try:
        log_dir = Path(os.getenv("LOG_DIR", "logs"))
        if not log_dir.is_absolute():
            log_dir = Path(app.root_path) / log_dir
        max_bytes = _positive_integer("LOG_MAX_BYTES", 10 * 1024 * 1024)
        backups = _positive_integer("LOG_BACKUP_COUNT", 5)
        log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        file_handler = SafeRotatingFileHandler(
            log_dir / "kursplan.log", maxBytes=max_bytes,
            backupCount=backups, encoding="utf-8",
        )
    except (OSError, ValueError):
        app.logger.critical("Loggningen kunde inte initieras; appstart avbryts.", exc_info=True)
        raise
    file_handler.kursplan_handler = True
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RequestContextFilter())
    root.addHandler(file_handler)
    app.config["LOG_FILE"] = str(log_dir / "kursplan.log")
    app.config["LOG_DEBUG"] = debug

    def process_exception(exc_type, exception, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            app.logger.info("Processen avbröts av användaren.")
        else:
            app.logger.critical("Ohanterat processfel.", exc_info=(exc_type, exception, tb))

    def thread_exception(args):
        app.logger.critical(
            "Ohanterat fel i bakgrundstråd.",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    def python_warning(message, category, filename, lineno, file=None, line=None):
        app.logger.warning("Python-varning: typ=%s fil=%s rad=%s.", category.__name__, Path(filename).name, lineno)

    sys.excepthook = process_exception
    threading.excepthook = thread_exception
    warnings.showwarning = python_warning
    if not _exit_registered:
        atexit.register(lambda: logging.getLogger("kursplan.lifecycle").info("Processen avslutas."))
        _exit_registered = True
    app.logger.info("Loggning initierad: nivå=%s, beständiga roterande loggfiler aktiverade.", logging.getLevelName(level))


def register_request_logging(app):
    @app.before_request
    def start_request_log():
        g.log_request_id = uuid4().hex
        g.log_request_started = time.perf_counter()
        g.log_request_finished = False
        g.log_exception_recorded = False
        app.logger.debug("HTTP-anrop påbörjat: method=%s route=%s.", _method(), _route())

    @app.after_request
    def finish_request_log(response):
        duration = (time.perf_counter() - g.log_request_started) * 1000
        level = logging.ERROR if response.status_code >= 500 else (
            logging.WARNING if response.status_code >= 400 else logging.INFO
        )
        app.logger.log(level, "HTTP-anrop avslutat: method=%s route=%s status=%s duration_ms=%.2f.", _method(), _route(), response.status_code, duration)
        response.headers["X-Request-ID"] = g.log_request_id
        g.log_request_finished = True
        return response

    @app.teardown_request
    def failed_request_log(exception):
        if exception is not None and not getattr(g, "log_exception_recorded", False):
            app.logger.error("Ohanterat fel under HTTP-anrop.", exc_info=(type(exception), exception, exception.__traceback__))
            g.log_exception_recorded = True
        if exception is not None and not getattr(g, "log_request_finished", False):
            app.logger.error("HTTP-anrop avbrutet: method=%s route=%s.", _method(), _route())
            g.log_request_finished = True


def _route():
    return request.url_rule.rule if request.url_rule else "<okänd route>"


def _method():
    return request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "TRACE", "CONNECT"} else "OTHER"
