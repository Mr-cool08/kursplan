"""Äldre uppdragsmejl kontrolleras med isolerad databas och SMTP."""

from email import policy
from email.parser import Parser
from runpy import run_path
from unittest.mock import MagicMock

from flask import session
from markupsafe import escape
import pytest


SENDER = "sender@example.test"
PROVIDER = "provider@example.test"
CUSTOMER = "legacy-customer@example.test"
PASSWORD = "legacy-mail-test-password"
JOB_ID = 42


@pytest.fixture
def legacy_mail_app(monkeypatch, project_root):
    for name, value in {
        "PYTHON_DOTENV_DISABLED": "1",
        "DEV_MODE": "false",
        "db_path": "postgresql://unused.invalid/never-connected",
        "app_secret_key": "legacy-mail-test-session-key",
        "email": SENDER,
        "Email_password": PASSWORD,
        "smtp_server_address": "smtp.example.test",
        "smtp_port": "587",
        "send_mail": "0",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setattr("logging_config.configure_logging", lambda app: None)
    initialize = MagicMock(side_effect=AssertionError("Ingen databas får initieras."))
    monkeypatch.setattr("database.create_database", initialize)

    namespace = run_path(str(project_root / "app.py"))
    app = namespace["app"]
    app.config["TESTING"] = True
    app.template_folder = str(project_root / "templates")
    app.static_folder = str(project_root / "static")
    initialize.assert_not_called()

    row = (
        'Åsa <script>alert("namn")</script> & Co',
        CUSTOMER,
        "+46 70 123 45 67",
        'Svenska <b title="språk">& engelska</b>',
        '2030-05-03 09:00 <img src=x onerror="alert(1)">',
        '2030-05-03 12:00 <svg onload="alert(2)">',
        "5566778899",
        "Fakturaadressen 9",
        "billing@example.test",
        "Intern märkning",
        "Avtalskundens märkning",
        "Intern referens",
    )
    connect = MagicMock()
    connect.return_value.cursor.return_value.fetchone.return_value = row
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(namespace["sqlite3"], "connect", connect)
    monkeypatch.setattr(namespace["smtplib"], "SMTP", smtp)
    return namespace, connect, smtp, row


def test_legacy_acceptance_styles_both_messages_without_changing_recipients(legacy_mail_app):
    namespace, connect, smtp, row = legacy_mail_app
    with namespace["app"].test_request_context(f"/jobs/{JOB_ID}", method="POST"):
        session.update(authenticated=True, kursplan_email=PROVIDER)
        assert namespace["accept_job"](JOB_ID) == "Job accepted and email sent"

    cursor = connect.return_value.cursor.return_value
    assert cursor.execute.call_args_list[-1].args == (
        "UPDATE bookings SET status='accepted' WHERE id = ?", (JOB_ID,),
    )
    connect.return_value.commit.assert_called_once_with()
    deliveries = smtp.return_value.__enter__.return_value.sendmail.call_args_list
    assert len(deliveries) == 2
    assert smtp.call_count == 2

    for delivery, recipient, status, subject in zip(
        deliveries,
        (PROVIDER, CUSTOMER),
        ("Accepterat", "Accepterad"),
        (
            f"Bekräftelse för uppdrag #{JOB_ID} – Kursplan.se",
            f"Din ansökan för uppdrag #{JOB_ID} är accepterad – Kursplan.se",
        ),
    ):
        sender, envelope, payload = delivery.args
        assert sender == SENDER
        assert envelope == [recipient, SENDER]
        message = Parser(policy=policy.default).parsestr(payload)
        assert message["From"] == SENDER
        assert message["To"] == recipient
        assert message["Bcc"] == SENDER
        assert message["Subject"] == subject
        assert message.get_content_subtype() == "alternative"
        assert [part.get_content_type() for part in message.iter_parts()] == [
            "text/plain", "text/html",
        ]
        for part in message.iter_parts():
            assert part.get_content_charset() == "utf-8"
        plain = message.get_body(preferencelist=("plain",)).get_content()
        html = message.get_body(preferencelist=("html",)).get_content()
        assert f"uppdrag #{JOB_ID}" in plain
        assert f"uppdrag #{JOB_ID}" in html
        assert "accepterad" in plain.lower() or "bekräftelsen" in plain.lower()
        assert status in html
        assert 'lang="sv"' in html
        assert "Kursplan.se" in plain and "Kursplan.se" in html
        for value in (row[0], row[3], row[4], row[5]):
            assert value in plain
            assert value not in html
            assert str(escape(value)) in html
        for private in (PASSWORD, *row[6:]):
            assert private not in plain and private not in html
        if recipient == PROVIDER:
            for value in (CUSTOMER, row[2]):
                assert value in plain and value in html
        else:
            for value in (CUSTOMER, row[2], PROVIDER):
                assert value not in plain and value not in html


def test_legacy_acceptance_without_authentication_never_connects_or_sends(legacy_mail_app):
    namespace, connect, smtp, _ = legacy_mail_app
    with namespace["app"].test_request_context(f"/jobs/{JOB_ID}", method="POST"):
        html = namespace["accept_job"](JOB_ID)
    assert "Logga in" in html
    connect.assert_not_called()
    smtp.assert_not_called()
