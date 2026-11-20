"""Log business events without storing submitted personal data or credentials."""
import logging
from html.parser import HTMLParser

from flask import Flask
import pytest

import database
from admin import admin_page
from users import user_page


class _TokenParser(HTMLParser):
    token = None

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name") == "csrf_token":
            self.token = fields.get("value")


def _token(client, path):
    parser = _TokenParser()
    parser.feed(client.get(path).get_data(as_text=True))
    assert parser.token
    return parser.token


@pytest.fixture
def logging_client(tmp_path, project_root, monkeypatch):
    monkeypatch.setenv("send_mail", "0")
    db_path = tmp_path / "route-logging.db"
    database.create_database(db_path)
    app = Flask("route-logging-tests", template_folder=str(project_root / "templates"),
                static_folder=str(project_root / "static"))
    app.config.update(TESTING=True, SECRET_KEY="route-logging-test-secret", DB_PATH=db_path)
    app.register_blueprint(user_page)
    app.register_blueprint(admin_page)
    database.create_user("Privat kundnamn", "private-customer@example.test", "private-password",
                         "private-organization", "user", db_path)
    return app, app.test_client(), db_path


def _login(client):
    return client.post("/login", data={
        "csrf_token": _token(client, "/login"),
        "email": "private-customer@example.test", "password": "private-password",
    })


def _messages(caplog, app):
    return "\n".join(record.getMessage() for record in caplog.records if record.name == app.name)


def test_authentication_logs_success_and_failure_without_credentials(logging_client, caplog):
    app, client, _ = logging_client
    with caplog.at_level(logging.DEBUG, logger=app.name):
        assert client.post("/login", data={
            "csrf_token": _token(client, "/login"),
            "email": "private-customer@example.test", "password": "private-invalid-password",
        }).status_code == 200
        assert _login(client).status_code == 302

    messages = _messages(caplog, app)
    assert any(r.levelno == logging.WARNING and "Inloggning nekades" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.INFO and "Inloggning lyckades" in r.getMessage() for r in caplog.records)
    for value in ("private-customer@example.test", "private-password", "private-invalid-password", "Privat kundnamn"):
        assert value not in messages


def test_booking_logs_validation_csrf_and_creation_without_form_contents(logging_client, caplog):
    app, client, _ = logging_client
    assert _login(client).status_code == 302
    with caplog.at_level(logging.DEBUG, logger=app.name):
        assert client.post("/booking", data={"csrf_token": "private-invalid-token"}).status_code == 400
        csrf_token = _token(client, "/booking")
        assert client.post("/booking", data={
            "csrf_token": csrf_token, "antal": "private-invalid-number",
        }).status_code == 400
        assert client.post("/booking", data={
            "csrf_token": csrf_token, "datumtyp": "exakt", "onskat_datum": "2030-05-01",
            "utbildningskalla": "fritext", "egen_utbildning": "Privat kursnamn",
            "antal": "3", "ort": "Privat ort", "lokalansvar": "kund", "lokaladress": "Privat adress",
        }).status_code == 302

    messages = _messages(caplog, app)
    assert any(r.levelno == logging.WARNING and "CSRF-kontrollen misslyckades" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.WARNING and "ogiltiga fält=" in r.getMessage() for r in caplog.records)
    assert any(r.levelno == logging.INFO and "Bokningsförfrågan skapad" in r.getMessage() for r in caplog.records)
    for value in (csrf_token, "private-invalid-token", "private-invalid-number", "Privat kursnamn",
                  "Privat ort", "Privat adress", "private-customer@example.test", "private-organization"):
        assert value not in messages


def test_administrative_access_denial_is_logged_without_session_data(logging_client, caplog):
    app, client, _ = logging_client
    with caplog.at_level(logging.DEBUG, logger=app.name):
        assert client.get("/admin/").status_code == 302
        assert _login(client).status_code == 302
        assert client.get("/admin/").status_code == 403

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("inloggning krävs" in message for message in warnings)
    assert any("administratörsbehörighet saknas" in message for message in warnings)
    for value in ("Privat kundnamn", "private-customer@example.test", "private-organization"):
        assert value not in _messages(caplog, app)
