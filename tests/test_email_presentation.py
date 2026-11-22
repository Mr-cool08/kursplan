"""Gemensam mejlpresentation testas med mockad SMTP och utan Flask-kontext."""

from email import policy
from email.parser import Parser
from html.parser import HTMLParser
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import pytest

import gen_func
from email_templates import render_email


EMAIL = "presentation-customer@example.test"
BOOKING_URL = "https://kursplan.se/bookings/42"
VERIFICATION_URL = "https://kursplan.se/verify-email?token=presentation-test-token"


class ParsedEmail(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.text_parts = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text_parts.append(data)

    @property
    def text(self):
        return " ".join(self.text_parts)

    @property
    def links(self):
        return [attrs["href"] for tag, attrs in self.elements if tag == "a" and "href" in attrs]


@pytest.fixture
def presentation_mail(monkeypatch):
    for name, value in {
        "send_mail": "1",
        "smtp_email": "sender@example.test",
        "smtp_server_address": "smtp.example.test",
        "smtp_port": "587",
        "smtp_password": "presentation-test-password",
    }.items():
        monkeypatch.setenv(name, value)
    smtp = MagicMock()
    smtp.return_value.__enter__.return_value.sendmail.return_value = {}
    monkeypatch.setattr(gen_func, "SMTP", smtp)
    booking = {
        "id": 42, "email": EMAIL, "utbildning": "HLR & första hjälpen", "antal": 6,
        "faststallt_datum": "2030-05-03", "faststalld_ort": "Göteborg",
        "faststalld_lokal": "Ågatan 1",
    }
    proposal = {
        "id": 7, "proposed_date": "2030-05-03", "proposed_city": "Göteborg",
        "proposed_venue": "Ågatan 1",
    }
    return smtp, booking, proposal


def captured_message(setup):
    sender, recipient, payload = setup[0].return_value.__enter__.return_value.sendmail.call_args.args
    assert (sender, recipient) == ("sender@example.test", EMAIL)
    return Parser(policy=policy.default).parsestr(payload)


def send_variant(setup, variant):
    _, booking, proposal = setup
    if variant == "booked":
        return gen_func.send_booked_mail(
            EMAIL, "2030-05-03", "Göteborg", 42, booking["utbildning"],
        )
    if variant == "proposal":
        return gen_func.send_proposal_mail(booking, proposal, BOOKING_URL)
    if variant == "verification":
        return gen_func.send_verification_mail(EMAIL, VERIFICATION_URL)
    return gen_func.send_booking_status_mail(booking, variant, BOOKING_URL)


@pytest.mark.parametrize(("variant", "expected"), [
    ("booked", ("42", "HLR & första hjälpen", "2030-05-03", "Göteborg")),
    ("proposal", ("datumförslag", "42", "Inväntar ditt svar", "Ågatan 1", "6")),
    ("accepted", ("Utbildningen är accepterad", "42", "Accepterad", "2030-05-03", "Ågatan 1")),
    ("cancelled", ("Bokningen är avbruten", "42", "Avbruten", "HLR & första hjälpen")),
    ("verification", ("Verifiera", "24 timmar", "kan du logga in", "kan bara användas en gång")),
])
def test_all_mail_variants_have_branded_html_and_complete_utf8_text(presentation_mail, variant, expected):
    result = send_variant(presentation_mail, variant)
    assert result is True if variant == "booked" else result == "sent"
    message = captured_message(presentation_mail)
    assert message.get_content_type() == "multipart/alternative"
    assert message["To"] == EMAIL and message["Bcc"] is None
    parts = [message.get_body(preferencelist=(subtype,)) for subtype in ("plain", "html")]
    assert all(part is not None and part.get_content_charset() == "utf-8" for part in parts)
    plain, html = [part.get_content() for part in parts]
    document = ParsedEmail(html)
    for value in expected:
        assert value in plain
        assert value in document.text
    assert "Kursplan.se" in document.text
    assert "Järnvägskonsulterna i Norden AB" in document.text
    assert any(tag == "h1" for tag, _ in document.elements)
    assert any(tag == "meta" and attrs.get("name") == "viewport" for tag, attrs in document.elements)
    assert any(
        tag == "table" and attrs.get("role") == "presentation" and "max-width" in attrs.get("style", "")
        for tag, attrs in document.elements
    )
    assert not any(tag in {"script", "form", "iframe"} for tag, _ in document.elements)
    assert not any(tag == "link" and attrs.get("rel") == "stylesheet" for tag, attrs in document.elements)
    assert "presentation-test-password" not in plain + html


def test_proposal_actions_keep_booking_and_proposal_ids_and_require_confirmation(presentation_mail):
    assert send_variant(presentation_mail, "proposal") == "sent"
    message = captured_message(presentation_mail)
    plain = message.get_body(preferencelist=("plain",)).get_content()
    document = ParsedEmail(message.get_body(preferencelist=("html",)).get_content())
    action_links = {
        link for link in document.links if "action" in parse_qs(urlsplit(link).query)
    }
    assert len(action_links) == 3
    assert {parse_qs(urlsplit(link).query)["action"][0] for link in action_links} == {
        "accept", "new_date", "cancel",
    }
    for link in action_links:
        assert urlsplit(link).path == "/bookings/42"
        assert parse_qs(urlsplit(link).query)["proposal_id"] == ["7"]
        assert link in plain
    for content in (plain, document.text):
        assert "du bekräftar ditt val efter inloggning" in content


@pytest.mark.parametrize("variant", ["accepted", "cancelled", "verification"])
def test_primary_action_keeps_its_original_destination(presentation_mail, variant):
    assert send_variant(presentation_mail, variant) == "sent"
    message = captured_message(presentation_mail)
    expected = VERIFICATION_URL if variant == "verification" else BOOKING_URL
    assert expected in message.get_body(preferencelist=("plain",)).get_content()
    assert expected in ParsedEmail(message.get_body(preferencelist=("html",)).get_content()).links


@pytest.mark.parametrize("field", ["booking_date", "ort", "booking_id", "utbildning"])
def test_confirmation_escapes_every_booking_field_in_html_only(presentation_mail, field):
    unsafe = '<script>alert("presentation")</script> & Å'
    arguments = {
        "email": EMAIL, "booking_date": "2030-05-03", "ort": "Göteborg",
        "booking_id": 42, "utbildning": "HLR",
    }
    arguments[field] = unsafe
    assert gen_func.send_booked_mail(**arguments) is True
    message = captured_message(presentation_mail)
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    document = ParsedEmail(html)
    assert unsafe in plain and unsafe in document.text
    assert unsafe not in html
    assert not any(tag == "script" for tag, _ in document.elements)


def test_shared_template_escapes_all_text_and_action_attributes_without_flask_context():
    unsafe = '<script>alert("template")</script> & Å'
    url = 'https://kursplan.se/bookings/42?value="<unsafe>"&next=1'
    html = render_email(
        title=unsafe, preheader=unsafe, introduction=unsafe,
        details=((unsafe, unsafe),), explanation=unsafe,
        actions=((unsafe, url),), notice=unsafe, eyebrow=unsafe, status=unsafe,
    )
    document = ParsedEmail(html)
    assert unsafe not in html
    assert unsafe in document.text
    assert url in document.links
    assert not any(tag == "script" for tag, _ in document.elements)
    assert not any(
        key.lower().startswith("on")
        for _, attributes in document.elements for key in attributes
    )
