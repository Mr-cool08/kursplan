from datetime import date, timedelta
from email import policy
from email.parser import Parser
import re
from runpy import run_path
from threading import Thread
from unittest.mock import ANY, call, patch

from playwright.sync_api import expect
import pytest
from werkzeug.serving import make_server

import gen_func


ADMIN_EMAIL = "release-admin@example.com"
ADMIN_PASSWORD = "release-admin-test-password"
CUSTOMER_EMAIL = "release-customer@example.com"
CUSTOMER_PASSWORD = "release-customer-test-password"


@pytest.fixture
def app_url(monkeypatch, project_root):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DEV_MODE", "true")
    monkeypatch.setenv("db_path", "")
    monkeypatch.setenv("app_secret_key", "release-browser-test-key")
    monkeypatch.setenv("test_email", ADMIN_EMAIL)
    monkeypatch.setenv("test_password", ADMIN_PASSWORD)
    monkeypatch.setenv("send_mail", "0")

    with patch("dev_accounts.load_dev_accounts", return_value={
        "admin": {"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        "user": {"email": "release-dev-user@example.com", "password": "release-dev-user-test-password"},
    }):
        app_globals = run_path(str(project_root / "app.py"))
    with app_globals["dev_database_file"]:
        with make_server("127.0.0.1", 0, app_globals["app"]) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield f"http://127.0.0.1:{server.server_port}"
            finally:
                server.shutdown()
                thread.join()


def _log_in(page, app_url, email, password):
    page.goto(f"{app_url}/login")
    page.get_by_label("E-post:").fill(email)
    page.get_by_label("Lösenord:").fill(password)
    page.get_by_role("button", name="Logga in").click()


def _submit_request(page, course, start, end):
    page.get_by_role("link", name="Gör ny bokning").click()
    page.locator("#datumtyp-intervall").check()
    page.locator("#onskat-startdatum").fill(start)
    page.locator("#onskat-slutdatum").fill(end)
    page.locator("#utbildningskalla-fritext").check()
    page.locator("#egen-utbildning").fill(course)
    page.locator("#ort").fill("Göteborg")
    page.locator("#antal").fill("4")
    page.locator("#lokalansvar-sakerhetsutbildarna").check()
    page.get_by_role("button", name="Skicka förfrågan").click()
    expect(page).to_have_url(re.compile(r"/dashboard$"))


def test_customer_and_admin_booking_lifecycle_in_browser(page, new_context, app_url):
    health = page.request.get(f"{app_url}/health")
    assert health.ok
    assert health.json() == {"status": "ok"}

    page.goto(f"{app_url}/signup")
    page.get_by_label("Namn:").fill("Testkund")
    page.get_by_label("E-post:").fill(CUSTOMER_EMAIL)
    page.get_by_label("Lösenord:").fill(CUSTOMER_PASSWORD)
    page.get_by_label("Organisationsnummer:").fill("0123456789")
    page.get_by_role("button", name="Skapa konto").click()
    expect(page).to_have_url(f"{app_url}/dashboard")
    page.get_by_role("link", name="Logga ut").click()
    _log_in(page, app_url, CUSTOMER_EMAIL, CUSTOMER_PASSWORD)
    expect(page).to_have_url(f"{app_url}/dashboard")

    start = (date.today() + timedelta(days=30)).isoformat()
    end = (date.today() + timedelta(days=34)).isoformat()
    confirmed_course = "Specialkurs första hjälpen"
    cancelled_course = "Avbokningsbar specialkurs"
    _submit_request(page, confirmed_course, start, end)
    _submit_request(page, cancelled_course, start, end)

    customer_card = page.get_by_role("article").filter(has_text=confirmed_course)
    expect(customer_card.get_by_text("Väntar")).to_be_visible()
    expect(customer_card.get_by_text(f"{start} – {end}")).to_be_visible()
    cancelled_card = page.get_by_role("article").filter(has_text=cancelled_course)
    cancelled_card.get_by_role("button", name="Avbryt").click()
    expect(cancelled_card.get_by_text("Avbruten")).to_be_visible()
    expect(cancelled_card.get_by_role("button", name="Avbryt")).to_have_count(0)

    admin = new_context().new_page()
    _log_in(admin, app_url, ADMIN_EMAIL, ADMIN_PASSWORD)
    expect(admin).to_have_url(f"{app_url}/admin/")
    admin_row = admin.locator("tbody tr").filter(has_text=confirmed_course)
    admin_row.get_by_role("checkbox").check()
    admin.get_by_role("button", name="Boka", exact=True).click()
    dialog = admin.get_by_role("dialog")
    expect(dialog.get_by_text(CUSTOMER_EMAIL)).to_be_visible()
    expect(dialog.get_by_label("Ort")).to_have_value("Göteborg")
    dialog.get_by_label("Datum").fill(end)
    dialog.get_by_label("Ort").fill("Uppsala")
    dialog.locator('button[type="submit"]').click()

    expect(admin_row).to_be_hidden()
    admin.get_by_label("Visa Bokade").check()
    expect(admin_row.get_by_text("Bokad")).to_be_visible()
    expect(admin_row.get_by_role("checkbox")).to_be_disabled()
    admin.get_by_label("Visa avbrutna").check()
    expect(admin.get_by_role("row").filter(has_text=cancelled_course)).to_contain_text("Avbruten")

    page.reload()
    expect(customer_card.get_by_text("Accepterad")).to_be_visible()
    expect(customer_card.get_by_text(end, exact=True)).to_be_visible()
    expect(customer_card.get_by_text("Uppsala")).to_be_visible()
    expect(customer_card.get_by_role("button", name="Avbryt")).to_have_count(0)


@pytest.fixture
def smtp(monkeypatch):
    monkeypatch.setenv("send_mail", "1")
    monkeypatch.setenv("smtp_email", "bookings@example.com")
    monkeypatch.setenv("smtp_server_address", "smtp.example.com")
    monkeypatch.setenv("smtp_port", "587")
    monkeypatch.setenv("smtp_password", "smtp-test-password")
    monkeypatch.setenv("DEBUG", "0")
    with patch.object(gen_func, "SMTP", autospec=True) as connection:
        yield connection


def test_booking_confirmation_sends_intended_email_over_tls(smtp):
    assert gen_func.send_booked_mail(CUSTOMER_EMAIL, "2030-06-15", "Uppsala", 42, "Första hjälpen")

    smtp.assert_called_once_with("smtp.example.com", 587)
    server = smtp.return_value.__enter__.return_value
    assert server.mock_calls == [
        call.set_debuglevel(0),
        call.starttls(),
        call.login("bookings@example.com", "smtp-test-password"),
        call.sendmail("bookings@example.com", CUSTOMER_EMAIL, ANY),
    ]
    message = Parser(policy=policy.default).parsestr(server.sendmail.call_args.args[2])
    assert message["To"] == CUSTOMER_EMAIL
    assert message["From"] == "bookings@example.com"
    assert message["Subject"] == "Bokningsbekräftelse"
    body = message.get_body(preferencelist=("plain",)).get_content()
    assert all(detail in body for detail in ("2030-06-15", "Uppsala", "42", "Första hjälpen"))


@pytest.mark.parametrize(
    ("setting", "value"),
    [("send_mail", "0"), ("smtp_server_address", ""), ("smtp_port", "invalid")],
    ids=["disabled", "missing-server", "invalid-port"],
)
def test_booking_confirmation_skips_smtp_without_usable_settings(smtp, monkeypatch, setting, value):
    monkeypatch.setenv(setting, value)

    assert gen_func.send_booked_mail(CUSTOMER_EMAIL, "2030-06-15", "Uppsala", 42, "Första hjälpen") is False
    smtp.assert_not_called()
