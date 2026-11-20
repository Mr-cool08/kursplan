"""Verifieringsmejl testas utan nätverk eller riktiga utskick."""

from email import policy
from email.parser import Parser
import logging
from smtplib import SMTPException, SMTPServerDisconnected
from unittest.mock import MagicMock

import pytest

import gen_func


EMAIL = "verification-customer@example.test"
URL = "https://kursplan.se/verify-email?token=private-verification-token"


@pytest.fixture
def verification_smtp(monkeypatch):
    for name, value in {
        "send_mail": "1", "smtp_email": "sender@example.test",
        "smtp_server_address": "smtp.example.test", "smtp_port": "587",
        "smtp_password": "private-smtp-password",
    }.items():
        monkeypatch.setenv(name, value)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    return smtp


def test_verification_mail_has_correct_recipient_link_expiry_and_tls(verification_smtp, caplog):
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        assert gen_func.send_verification_mail(EMAIL, URL) == "sent"
    verification_smtp.assert_called_once_with("smtp.example.test", 587, timeout=20)
    server = verification_smtp.return_value.__enter__.return_value
    assert server.starttls.call_args.kwargs["context"].check_hostname
    server.login.assert_called_once_with("sender@example.test", "private-smtp-password")
    server.set_debuglevel.assert_not_called()
    sender, recipient, payload = server.sendmail.call_args.args
    assert (sender, recipient) == ("sender@example.test", EMAIL)
    message = Parser(policy=policy.default).parsestr(payload)
    assert message["To"] == EMAIL
    assert message["Bcc"] is None
    assert "Verifiera" in message["Subject"]
    for subtype in ("plain", "html"):
        content = message.get_body(preferencelist=(subtype,)).get_content()
        assert URL in content
        assert "24 timmar" in content
        assert "kan du logga in" in content
        assert "private-smtp-password" not in content
    for private in (EMAIL, URL, "private-smtp-password"):
        assert private not in caplog.text


def test_verification_mail_escapes_html_link(verification_smtp):
    url = URL + '&extra="<unsafe>"'
    assert gen_func.send_verification_mail(EMAIL, url) == "sent"
    payload = verification_smtp.return_value.__enter__.return_value.sendmail.call_args.args[2]
    message = Parser(policy=policy.default).parsestr(payload)
    assert url in message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert '<unsafe>' not in html
    assert "&amp;extra=&quot;&lt;unsafe&gt;&quot;" in html


def test_verification_mail_disabled_never_connects(verification_smtp, monkeypatch):
    monkeypatch.setenv("send_mail", "0")
    assert gen_func.send_verification_mail(EMAIL, URL) == "disabled"
    verification_smtp.assert_not_called()


@pytest.mark.parametrize(("setting", "value"), [
    ("smtp_email", None), ("smtp_server_address", ""), ("smtp_password", None),
    ("smtp_port", None), ("smtp_port", "not-a-port"), ("smtp_port", "0"),
    ("smtp_port", "65536"),
])
def test_verification_mail_missing_configuration_fails_before_connecting(
    verification_smtp, monkeypatch, setting, value
):
    if value is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, value)
    assert gen_func.send_verification_mail(EMAIL, URL) == "failed"
    verification_smtp.assert_not_called()


@pytest.mark.parametrize("field", ["sender", "recipient"])
def test_verification_mail_rejects_injected_headers(verification_smtp, monkeypatch, field):
    recipient = EMAIL
    injected = EMAIL + "\r\nBcc: attacker@example.test"
    if field == "sender":
        monkeypatch.setenv("smtp_email", injected)
    else:
        recipient = injected
    assert gen_func.send_verification_mail(recipient, URL) == "failed"
    verification_smtp.assert_not_called()


@pytest.mark.parametrize("stage", ["connect", "starttls", "login", "sendmail"])
def test_verification_mail_failure_does_not_claim_delivery(verification_smtp, caplog, stage):
    error = SMTPException(f"{EMAIL} {URL} private-smtp-password")
    if stage == "connect":
        verification_smtp.side_effect = error
    else:
        server = verification_smtp.return_value.__enter__.return_value
        getattr(server, stage).side_effect = error
    with caplog.at_level(logging.DEBUG, logger="gen_func"):
        assert gen_func.send_verification_mail(EMAIL, URL) == "failed"
    for private in (EMAIL, URL, "private-smtp-password"):
        assert private not in caplog.text


@pytest.mark.parametrize("refused", [{}, {EMAIL: (550, b"refused")}])
def test_verification_mail_handles_refusal_and_quit_failure(verification_smtp, refused):
    server = verification_smtp.return_value.__enter__.return_value
    server.sendmail.return_value = refused
    verification_smtp.return_value.__exit__.side_effect = SMTPServerDisconnected("QUIT disconnected")
    assert gen_func.send_verification_mail(EMAIL, URL) == ("failed" if refused else "sent")
