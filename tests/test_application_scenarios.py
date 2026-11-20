"""Real application endpoints and browser error paths on a temporary SQLite DB."""

from runpy import run_path
from threading import Thread

from playwright.sync_api import expect
import pytest
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

import database


@pytest.fixture
def application_client(tmp_path, monkeypatch, project_root):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("db_path", "postgresql://unused.invalid/never-connected")
    monkeypatch.setenv("app_secret_key", "application-scenarios-test-key")
    monkeypatch.setenv("send_mail", "0")
    # Logging has separate tests. Avoid replacing pytest's process-wide handlers.
    monkeypatch.setattr("logging_config.configure_logging", lambda app: None)
    namespace = run_path(str(project_root / "app.py"))
    path = tmp_path / "application.db"
    database.create_database(path)
    app = namespace["app"]
    app.config.update(TESTING=True, DB_PATH=path)
    app.template_folder = str(project_root / "templates")
    app.static_folder = str(project_root / "static")
    return app.test_client()


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_health_is_available_without_login_or_database(application_client, monkeypatch, method):
    def unexpected_access(*args, **kwargs):
        pytest.fail("Hälsokontrollen ska inte ansluta till databasen.")

    monkeypatch.setattr(database, "_cursor", unexpected_access)
    response = application_client.open("/health", method=method)
    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert len(response.headers["X-Request-ID"]) == 32
    if method == "GET":
        assert response.get_json() == {"status": "ok"}
    else:
        assert response.get_data() == b""


def test_unknown_page_renders_swedish_404(application_client):
    response = application_client.get("/missing-page?secret=do-not-echo")
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert "Detta var inte vad du letade efter." in html
    assert "404" in html
    assert "do-not-echo" not in html


@pytest.mark.parametrize(("method", "path"), [
    ("POST", "/health"), ("PUT", "/login"), ("DELETE", "/signup"),
    ("PUT", "/booking"), ("POST", "/dashboard"), ("POST", "/admin/"),
])
def test_unsupported_methods_do_not_create_records(application_client, method, path):
    response = application_client.open(path, method=method, data={
        "name": "unsubmitted", "email": "unsubmitted@example.test",
    })
    assert response.status_code == 405
    db = application_client.application.config["DB_PATH"]
    assert database.get_all_bookings(db) == []
    assert not database.check_user_exists("unsubmitted@example.test", db)


@pytest.mark.parametrize(("path", "mimetype"), [
    ("style/base.css", "text/css"), ("style/public.css", "text/css"),
    ("style/auth.css", "text/css"), ("style/book.css", "text/css"),
    ("js/admin_companies.js", "text/javascript"),
    ("images/favicon/JKA_Logo.png", "image/png"),
])
def test_page_assets_are_served(application_client, path, mimetype):
    response = application_client.get(f"/static/{path}")
    assert response.status_code == 200
    assert response.mimetype == mimetype
    assert response.get_data()


def test_logout_clears_identity_and_invalidates_previously_issued_form(application_client, csrf_from_html):
    path = application_client.application.config["DB_PATH"]
    user = database.create_user("Kund", "logout@example.test", "test-password", "5566778899", "user", path)
    with application_client.session_transaction() as state:
        state.update(user_id=user[0], role="user", user_email=user[3])
    token = csrf_from_html(application_client.get("/booking").get_data(as_text=True))
    response = application_client.get("/logout")
    assert response.status_code == 302 and response.location == "/"
    with application_client.session_transaction() as state:
        assert dict(state) == {}
    assert application_client.get("/dashboard").location == "/login"
    assert application_client.post("/login", data={"csrf_token": token}).status_code == 400
    assert database.get_user_identity(user[0], path)["email"] == "logout@example.test"
    assert database.get_all_bookings(path) == []


@pytest.fixture
def application_server(application_client):
    app = application_client.application
    requests = []

    @app.before_request
    def record_request():
        from flask import request
        requests.append((request.method, request.path))

    with make_server("127.0.0.1", 0, app) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.mark.parametrize("path", ["/login", "/signup"])
def test_browser_required_and_email_validation_prevents_submission(page, application_server, path):
    url, requests = application_server
    page.goto(f"{url}{path}")
    submit = page.locator('input[type="submit"]')
    submit.click()
    assert not page.locator("form").evaluate("form => form.checkValidity()")
    assert ("POST", path) not in requests
    if path == "/signup":
        page.get_by_label("Namn:", exact=True).fill("Testkund")
        page.get_by_label("Organisationsnummer:", exact=True).fill("5566778899")
    page.get_by_label("E-post:", exact=True).fill("invalid-address")
    page.get_by_label("Lösenord:", exact=True).fill("test-password")
    submit.click()
    assert page.locator("#email").evaluate("input => input.validity.typeMismatch")
    assert ("POST", path) not in requests


def test_browser_login_error_can_be_corrected_with_keyboard_and_logout(page, application_client, application_server):
    path = application_client.application.config["DB_PATH"]
    database.create_user("Testkund", "browser@example.test", generate_password_hash("correct-test-password"),
                         "5566778899", "user", path)
    url, _ = application_server
    page.goto(f"{url}/login")
    page.get_by_label("E-post:", exact=True).fill("browser@example.test")
    password = page.get_by_label("Lösenord:", exact=True)
    password.fill("wrong-test-password")
    password.press("Enter")
    expect(page.get_by_role("alert")).to_be_visible()
    assert "wrong-test-password" not in page.content()
    page.get_by_label("E-post:", exact=True).fill("browser@example.test")
    password.fill("correct-test-password")
    password.press("Enter")
    expect(page).to_have_url(f"{url}/dashboard")
    expect(page.get_by_role("link", name="Gör ny bokning")).to_be_visible()
    page.get_by_role("link", name="Logga ut").click()
    expect(page).to_have_url(f"{url}/")
    page.goto(f"{url}/dashboard")
    expect(page).to_have_url(f"{url}/login")
