"""Kontoverifiering i Chromium med lokala Flask-svar och mockade mejl."""

from threading import Thread

from flask import Flask, redirect, session, url_for
from playwright.sync_api import expect
import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

import database
import gen_func
from admin import admin_page
from users import user_page


EMAIL = "verification-ui@example.test"
PASSWORD = "Långt-testlösenord-för-verifiering"


class _QuietRequestHandler(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        # Test access logs must not record verification tokens in query strings.
        pass


@pytest.fixture
def verification_browser_app(project_root, tmp_path, monkeypatch):
    path = tmp_path / "verification-ui.db"
    database.create_database(path)
    app = Flask(
        "verification-ui-tests",
        template_folder=str(project_root / "templates"),
        static_folder=str(project_root / "static"),
    )
    app.config.update(
        TESTING=True, SECRET_KEY="verification-ui-test-key", DB_PATH=path,
    )
    app.register_blueprint(user_page)
    app.register_blueprint(admin_page)

    @app.route("/logout", endpoint="logout")
    def logout():
        session.clear()
        return redirect(url_for("user_page.index"))

    # The route captures the intended recipient and URL without contacting SMTP.
    messages = []

    def capture_mail(email, verification_url):
        messages.append((email, verification_url))
        return "sent"

    monkeypatch.setenv("send_mail", "0")
    monkeypatch.setattr(gen_func, "send_verification_mail", capture_mail)
    server = make_server("127.0.0.1", 0, app, request_handler=_QuietRequestHandler)
    origin = f"http://127.0.0.1:{server.server_port}"
    app.config["PUBLIC_BASE_URL"] = origin
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield path, messages, origin
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def _fill_login(page):
    page.get_by_label("E-post:", exact=True).fill(EMAIL)
    page.get_by_label("Lösenord:", exact=True).fill(PASSWORD)
    page.get_by_role("button", name="Logga in", exact=True).click()


@pytest.mark.parametrize("width", [390, 1280])
def test_signup_verification_and_login_in_browser(page, verification_browser_app, width):
    db_path, messages, origin = verification_browser_app
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(origin + "/signup")
    page.get_by_label("Namn:", exact=True).fill("Åsa Verifiering")
    page.get_by_label("E-post:", exact=True).fill(EMAIL)
    page.get_by_label("Lösenord:", exact=True).fill(PASSWORD)
    page.get_by_label("Organisationsnummer:", exact=True).fill("5566778899")
    page.get_by_role("button", name="Skapa konto", exact=True).click()
    expect(page).to_have_url(origin + "/login")
    banner = page.locator(".verification-banner")
    expect(banner).to_be_visible()
    expect(banner).to_contain_text("Kontot är skapat")
    resend = banner.get_by_role("button", name="Skicka verifieringsmejlet igen", exact=True)
    expect(resend).to_have_attribute("form", "login-form")
    expect(resend).to_have_attribute("formaction", "/verify-email/resend")
    assert len(messages) == 1 and messages[0][0] == EMAIL
    assert database.authenticate_user(EMAIL, PASSWORD, db_path) is None

    _fill_login(page)
    expect(page.get_by_role("alert")).to_contain_text("Felaktig e-postadress eller lösenord")
    expect(page).to_have_url(origin + "/login")
    expect(banner).to_be_visible()

    # A resend uses the login fields and returns to the same login page.
    with database._cursor(db_path) as cursor:
        database._execute(cursor, "UPDATE user_email_verifications SET sent_at = 0")
    page.get_by_label("E-post:", exact=True).fill(EMAIL)
    page.get_by_label("Lösenord:", exact=True).fill(PASSWORD)
    resend.focus()
    expect(resend).to_be_focused()
    resend.press("Enter")
    expect(page).to_have_url(origin + "/login")
    expect(banner).to_be_visible()
    assert len(messages) == 2 and messages[-1][0] == EMAIL
    assert messages[-1][1] != messages[0][1]
    assert database.authenticate_user(EMAIL, PASSWORD, db_path) is None

    page.goto(messages[-1][1])
    expect(page.get_by_role("heading", level=1)).to_have_text("Verifiera e-postadress")
    assert database.authenticate_user(EMAIL, PASSWORD, db_path) is None
    # Opening a mail link does not consume it; confirmation works with a keyboard.
    button = page.get_by_role("button", name="Verifiera min e-postadress", exact=True)
    button.focus()
    expect(button).to_be_focused()
    button.press("Enter")
    expect(page).to_have_url(origin + "/login")
    expect(page.get_by_role("status")).to_contain_text("Din e-postadress är verifierad")
    expect(banner).to_have_count(0)
    _fill_login(page)
    expect(page).to_have_url(origin + "/dashboard")
    assert database.authenticate_user(EMAIL, PASSWORD, db_path)


@pytest.mark.parametrize("width", [390, 1280])
@pytest.mark.parametrize("view", ["pending", "confirmation", "invalid"])
def test_verification_pages_fit_mobile_and_desktop(
    page, verification_browser_app, width, view
):
    db_path, _, origin = verification_browser_app
    database.create_user(
        "Verifieringskund", EMAIL, PASSWORD, "5566778899", "user", db_path,
        email_verified=False,
    )
    token = database.issue_email_verification(EMAIL, PASSWORD, db_path)
    paths = {
        "pending": "/verify-email/resend",
        "confirmation": f"/verify-email?token={token}",
        "invalid": "/verify-email?token=invalid",
    }
    page.set_viewport_size({"width": width, "height": 844})
    page.goto(origin + paths[view])
    expect(page.get_by_role("main")).to_be_visible()
    expect(page.get_by_role("heading", level=1)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        f"Verification page {view} overflows horizontally at {width}px"
    )
    assert page.evaluate(
        "document.querySelector('footer').getBoundingClientRect().top >= "
        "document.querySelector('main').getBoundingClientRect().bottom - 1"
    ), "Footer covers verification content"
    if view == "pending":
        expect(page).to_have_url(origin + "/login")
        expect(page.locator(".verification-banner")).to_be_visible()
        expect(page.get_by_role("button", name="Skicka verifieringsmejlet igen", exact=True)).to_be_visible()
        for name in ("E-post:", "Lösenord:"):
            field = page.get_by_label(name, exact=True)
            expect(field).to_be_visible()
            bounds = field.bounding_box()
            assert bounds and bounds["height"] >= 44
    elif view == "confirmation":
        expect(page.get_by_role("button", name="Verifiera min e-postadress")).to_be_visible()
    else:
        expect(page.get_by_role("alert")).to_contain_text("Länken är ogiltig")
        expect(page.get_by_role("link", name="Skicka en ny verifieringslänk")).to_be_visible()


def test_regular_login_does_not_show_registration_banner(page, verification_browser_app):
    _, _, origin = verification_browser_app
    page.goto(origin + "/login")
    expect(page.get_by_role("heading", level=1)).to_have_text("Logga in")
    expect(page.locator(".verification-banner")).to_have_count(0)
