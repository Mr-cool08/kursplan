"""Mail failure contracts and course loading; SMTP and files are isolated."""

from email import policy
from email.parser import Parser
import json
from smtplib import SMTPAuthenticationError, SMTPException, SMTPRecipientsRefused
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

import gen_func


@pytest.fixture
def mail_setup(monkeypatch):
    for name, value in {
        "send_mail": "1", "smtp_email": "sender@example.test",
        "smtp_server_address": "smtp.example.test", "smtp_port": "587",
        "smtp_password": "test-only-smtp-password",
    }.items():
        monkeypatch.setenv(name, value)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    booking = {"id": 42, "email": "customer@example.test", "utbildning": "HLR", "antal": 3}
    proposal = {"id": 7, "proposed_date": "2030-03-18", "proposed_city": "Göteborg",
                "proposed_venue": "Ågatan 1"}
    return smtp, booking, proposal


def _send_proposal(setup):
    _, booking, proposal = setup
    return gen_func.send_proposal_mail(booking, proposal, "https://kursplan.se/bookings/42")


@pytest.mark.parametrize(("setting", "value"), [
    ("smtp_email", None), ("smtp_server_address", None), ("smtp_password", None),
    ("smtp_port", None), ("smtp_email", "  "), ("smtp_password", "''"),
    ("smtp_port", "0"), ("smtp_port", "-1"), ("smtp_port", "65536"),
    ("smtp_port", "587.0"), ("smtp_port", "not-a-number"),
])
def test_proposal_rejects_incomplete_configuration_before_connecting(mail_setup, monkeypatch, setting, value):
    if value is None:
        monkeypatch.delenv(setting, raising=False)
    else:
        monkeypatch.setenv(setting, value)
    assert _send_proposal(mail_setup) == "failed"
    mail_setup[0].assert_not_called()


@pytest.mark.parametrize(("field", "value"), [
    ("sender", "sender@example.test\r\nBcc: injected@example.test"),
    ("recipient", "customer@example.test\nBcc: injected@example.test"),
])
def test_proposal_rejects_injected_mail_headers(mail_setup, monkeypatch, field, value):
    if field == "sender":
        monkeypatch.setenv("smtp_email", value)
    else:
        mail_setup[1]["email"] = value
    assert _send_proposal(mail_setup) == "failed"
    mail_setup[0].assert_not_called()


@pytest.mark.parametrize("stage", ["connect", "starttls", "login", "sendmail"])
def test_proposal_delivery_failure_never_claims_sent(mail_setup, stage):
    smtp, _, _ = mail_setup
    server = smtp.return_value.__enter__.return_value
    errors = {
        "connect": OSError("SMTP unavailable"), "starttls": SMTPException("TLS unavailable"),
        "login": SMTPAuthenticationError(535, b"test rejection"),
        "sendmail": SMTPRecipientsRefused({"customer@example.test": (550, b"test rejection")}),
    }
    if stage == "connect":
        smtp.side_effect = errors[stage]
    else:
        getattr(server, stage).side_effect = errors[stage]
    assert _send_proposal(mail_setup) == "failed"
    if stage in {"connect", "starttls", "login"}:
        server.sendmail.assert_not_called()
    if stage in {"connect", "starttls"}:
        server.login.assert_not_called()


def test_proposal_trims_quoted_smtp_settings_and_uses_tls_context(mail_setup, monkeypatch):
    smtp, _, _ = mail_setup
    for name, value in {
        "smtp_email": ' "sender@example.test" ', "smtp_server_address": " 'smtp.example.test' ",
        "smtp_port": ' "587" ', "smtp_password": " 'test-only-smtp-password' ",
    }.items():
        monkeypatch.setenv(name, value)
    assert _send_proposal(mail_setup) == "sent"
    smtp.assert_called_once_with("smtp.example.test", 587, timeout=20)
    server = smtp.return_value.__enter__.return_value
    assert server.starttls.call_args.kwargs["context"].check_hostname
    server.login.assert_called_once_with("sender@example.test", "test-only-smtp-password")
    server.set_debuglevel.assert_not_called()


def test_proposal_html_escapes_all_customer_visible_fields(mail_setup):
    smtp, booking, proposal = mail_setup
    unsafe = '<img src=x onerror="alert(1)"> & Å'
    booking["utbildning"] = unsafe
    proposal.update(proposed_city=unsafe, proposed_venue=unsafe)
    assert _send_proposal(mail_setup) == "sent"
    server = smtp.return_value.__enter__.return_value
    sender, recipient, payload = server.sendmail.call_args.args
    assert (sender, recipient) == ("sender@example.test", "customer@example.test")
    message = Parser(policy=policy.default).parsestr(payload)
    assert message.get_content_subtype() == "alternative"
    assert message["Bcc"] is None
    html = message.get_body(preferencelist=("html",)).get_content()
    plain = message.get_body(preferencelist=("plain",)).get_content()
    assert unsafe in plain
    assert unsafe not in html
    assert html.count("&lt;img") == 3
    assert "&amp; Å" in html
    links = [line for line in plain.splitlines() if line.startswith("https://")]
    assert len(links) == 3
    assert {parse_qs(urlsplit(link).query)["action"][0] for link in links} == {"accept", "new_date", "cancel"}
    for link in links:
        assert urlsplit(link).path == "/bookings/42"
        assert parse_qs(urlsplit(link).query)["proposal_id"] == ["7"]


@pytest.mark.parametrize("stage", ["starttls", "login", "sendmail"])
def test_confirmation_propagates_failure_without_attempting_later_steps(mail_setup, stage):
    smtp, booking, _ = mail_setup
    server = smtp.return_value.__enter__.return_value
    getattr(server, stage).side_effect = SMTPException("test rejection")
    with pytest.raises(SMTPException):
        gen_func.send_booked_mail(booking["email"], "2030-03-18", "Göteborg", 42, "HLR")
    if stage != "sendmail":
        server.sendmail.assert_not_called()
    if stage == "starttls":
        server.login.assert_not_called()


@pytest.fixture
def catalog_path(tmp_path, monkeypatch):
    monkeypatch.setattr(gen_func, "__file__", str(tmp_path / "gen_func.py"))
    return tmp_path / "utbildningar.json"


def test_missing_catalog_returns_empty_list(catalog_path):
    assert not catalog_path.exists()
    assert gen_func.read_utbildningar_from_json() == []


@pytest.mark.parametrize("payload", [{}, {"utbildningar": []}, {"utbildningar": ["HLR", "Första hjälpen & brandskydd"]}])
def test_catalog_preserves_swedish_course_names_and_empty_list(catalog_path, payload):
    catalog_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert gen_func.read_utbildningar_from_json() == payload.get("utbildningar", [])


@pytest.mark.parametrize("contents", ["{invalid", "[]", "null"])
def test_corrupt_catalog_is_reported_without_replacing_file(catalog_path, contents):
    catalog_path.write_text(contents, encoding="utf-8")
    with pytest.raises((ValueError, AttributeError)):
        gen_func.read_utbildningar_from_json()
    assert catalog_path.read_text(encoding="utf-8") == contents


def test_unreadable_catalog_propagates_io_error(catalog_path, monkeypatch):
    catalog_path.write_text("{}", encoding="utf-8")
    reader = MagicMock(side_effect=PermissionError("catalog unreadable"))
    monkeypatch.setattr(gen_func, "open", reader, raising=False)
    with pytest.raises(PermissionError):
        gen_func.read_utbildningar_from_json()
