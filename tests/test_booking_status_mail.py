"""Kundens statusmejl verifieras utan nätverk eller riktiga utskick."""

from email import policy
from email.parser import Parser
from html import escape
import logging
from smtplib import SMTPException, SMTPServerDisconnected
from unittest.mock import MagicMock

import pytest

import gen_func


EMAIL = "status-customer@example.test"
URL = "https://kursplan.se/bookings/42"
PASSWORD = "private-status-smtp-password"


@pytest.fixture
def status_mail(monkeypatch):
    for name, value in {
        "send_mail": "1", "smtp_email": "sender@example.test",
        "smtp_server_address": "smtp.example.test", "smtp_port": "587",
        "smtp_password": PASSWORD,
    }.items():
        monkeypatch.setenv(name, value)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    booking = {
        "id": 42, "email": EMAIL, "utbildning": "HLR & första hjälpen", "antal": 6,
        "faststallt_datum": "2030-05-03", "faststalld_ort": "Göteborg",
        "faststalld_lokal": "Ågatan 1", "onskat_datum": "2030-05-01",
        "onskad_ort": "Stockholm", "lokaladress": "Ursprungsgatan 9",
    }
    return smtp, booking


def send(setup, status="accepted", url=URL):
    return gen_func.send_booking_status_mail(setup[1], status, url)


def message_from(setup):
    payload = setup[0].return_value.__enter__.return_value.sendmail.call_args.args[2]
    return Parser(policy=policy.default).parsestr(payload)


@pytest.mark.parametrize(("status", "title"), [
    ("accepted", "Utbildningen är accepterad"),
    ("cancelled", "Bokningen är avbruten"),
])
def test_status_mail_identifies_event_recipient_and_booking(status_mail, status, title, caplog):
    smtp, booking = status_mail
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        assert send(status_mail, status) == "sent"
    server = smtp.return_value.__enter__.return_value
    assert server.sendmail.call_args.args[:2] == ("sender@example.test", EMAIL)
    message = message_from(status_mail)
    assert message.get_content_subtype() == "alternative"
    assert message["To"] == EMAIL and message["Bcc"] is None
    assert title in message["Subject"]
    for subtype in ("plain", "html"):
        part = message.get_body(preferencelist=(subtype,))
        assert part.get_content_charset() == "utf-8"
        content = part.get_content()
        assert title in content
        assert "42" in content and "6" in content and URL in content
        assert (escape(booking["utbildning"]) if subtype == "html" else booking["utbildning"]) in content
        assert PASSWORD not in content
    for private in (EMAIL, PASSWORD, booking["utbildning"], booking["faststalld_lokal"]):
        assert private not in caplog.text


def test_accepted_mail_uses_confirmed_details_instead_of_customer_wishes(status_mail):
    assert send(status_mail) == "sent"
    message = message_from(status_mail)
    for subtype in ("plain", "html"):
        content = message.get_body(preferencelist=(subtype,)).get_content()
        for confirmed in ("2030-05-03", "Göteborg", "Ågatan 1"):
            assert confirmed in content
        for original in ("2030-05-01", "Stockholm", "Ursprungsgatan 9"):
            assert original not in content


def test_cancellation_without_confirmed_details_is_still_delivered(status_mail):
    status_mail[1].update(faststallt_datum=None, faststalld_ort=None, faststalld_lokal=None)
    assert send(status_mail, "cancelled") == "sent"
    message = message_from(status_mail)
    for subtype in ("plain", "html"):
        content = message.get_body(preferencelist=(subtype,)).get_content()
        assert "Bokningen är avbruten" in content
        assert "None" not in content


def test_status_mail_escapes_user_fields_and_booking_link(status_mail):
    unsafe = '<img src=x onerror="alert(1)"> & Å'
    for field in ("utbildning", "faststalld_ort", "faststalld_lokal"):
        status_mail[1][field] = unsafe
    url = URL + '?view="<unsafe>"&mail=1'
    assert send(status_mail, url=url) == "sent"
    message = message_from(status_mail)
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert unsafe in plain and url in plain
    assert unsafe not in html and "<unsafe>" not in html
    assert escape(unsafe) in html and escape(url, quote=True) in html


def test_status_mail_uses_trimmed_smtp_settings_and_verified_tls(status_mail, monkeypatch):
    for name, value in {
        "smtp_email": ' "sender@example.test" ', "smtp_server_address": " 'smtp.example.test' ",
        "smtp_port": ' "587" ', "smtp_password": f" '{PASSWORD}' ",
    }.items():
        monkeypatch.setenv(name, value)
    assert send(status_mail) == "sent"
    smtp = status_mail[0]
    smtp.assert_called_once_with("smtp.example.test", 587, timeout=20)
    server = smtp.return_value.__enter__.return_value
    assert server.starttls.call_args.kwargs["context"].check_hostname
    server.login.assert_called_once_with("sender@example.test", PASSWORD)
    server.set_debuglevel.assert_not_called()


@pytest.mark.parametrize("status", ["accepted", "cancelled"])
def test_status_mail_disabled_never_connects(status_mail, monkeypatch, status):
    monkeypatch.setenv("send_mail", "0")
    assert send(status_mail, status) == "disabled"
    status_mail[0].assert_not_called()


@pytest.mark.parametrize(("setting", "value"), [
    ("smtp_email", None), ("smtp_server_address", ""), ("smtp_password", "''"),
    ("smtp_port", None), ("smtp_port", "not-a-port"), ("smtp_port", "0"),
    ("smtp_port", "65536"),
])
def test_status_mail_invalid_configuration_fails_before_connecting(status_mail, monkeypatch, setting, value):
    if value is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, value)
    assert send(status_mail) == "failed"
    status_mail[0].assert_not_called()


@pytest.mark.parametrize("field", ["sender", "recipient"])
def test_status_mail_rejects_injected_headers(status_mail, monkeypatch, field):
    injected = EMAIL + "\r\nBcc: attacker@example.test"
    if field == "sender":
        monkeypatch.setenv("smtp_email", injected)
    else:
        status_mail[1]["email"] = injected
    assert send(status_mail) == "failed"
    status_mail[0].assert_not_called()


@pytest.mark.parametrize("stage", ["connect", "starttls", "login", "sendmail"])
def test_status_mail_failure_does_not_claim_delivery_or_log_secrets(status_mail, caplog, stage):
    smtp, _ = status_mail
    server = smtp.return_value.__enter__.return_value
    error = SMTPException(f"{EMAIL} {PASSWORD}")
    if stage == "connect":
        smtp.side_effect = error
    else:
        getattr(server, stage).side_effect = error
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        assert send(status_mail) == "failed"
    if stage != "sendmail":
        server.sendmail.assert_not_called()
    for private in (EMAIL, PASSWORD):
        assert private not in caplog.text


@pytest.mark.parametrize("refused", [{}, {EMAIL: (550, b"refused")}])
def test_status_mail_handles_recipient_refusal_and_quit_failure(status_mail, refused):
    smtp, _ = status_mail
    smtp.return_value.__enter__.return_value.sendmail.return_value = refused
    smtp.return_value.__exit__.side_effect = SMTPServerDisconnected("QUIT disconnected")
    assert send(status_mail) == ("failed" if refused else "sent")


def test_unhandled_status_does_not_send_customer_mail(status_mail):
    assert send(status_mail, "registered") == "failed"
    status_mail[0].assert_not_called()
