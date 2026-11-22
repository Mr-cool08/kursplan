"""Inloggningen varar 60 dagar från senaste besöket för kunder och admin."""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from http.cookies import SimpleCookie
from runpy import run_path

from itsdangerous import TimestampSigner
import pytest

import database


LIFETIME = timedelta(days=60)
EMAIL = "session-lifetime@example.test"
PASSWORD = "session-lifetime-test-password"


@pytest.fixture
def session_app(all_db_path, monkeypatch, project_root):
    # Load the actual application's session configuration without touching .env,
    # starting a database or replacing pytest's logging/exception handlers.
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("db_path", "postgresql://example.invalid/session-tests")
    monkeypatch.setenv("app_secret_key", "session-lifetime-test-secret")
    monkeypatch.setenv("send_mail", "0")
    monkeypatch.setattr("logging_config.configure_logging", lambda app: None)
    app = run_path(str(project_root / "app.py"))["app"]
    app.config.update(TESTING=True, DB_PATH=all_db_path, PREFERRED_URL_SCHEME="https")
    return app


@pytest.fixture(params=("user", "admin"))
def session_account(request, all_db_path):
    account = database.create_user(
        "Sessionstest", EMAIL, PASSWORD, "5566778899", request.param, all_db_path,
    )
    destination = "/admin/" if request.param == "admin" else "/dashboard"
    return account, destination


@pytest.fixture
def session_clock(monkeypatch):
    class Clock:
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)

        def advance(self, duration):
            self.now += duration

    clock = Clock()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromtimestamp(clock.now.timestamp(), tz=tz)

    monkeypatch.setattr("flask.sessions.datetime", FrozenDateTime)
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: int(clock.now.timestamp()))
    monkeypatch.setattr("auth_sessions.time", lambda: clock.now.timestamp())
    return clock


def _login(client, *, password=PASSWORD):
    assert client.get("/login").status_code == 200
    with client.session_transaction() as state:
        token = state["booking_csrf_token"]
    return client.post("/login", data={
        "email": EMAIL, "password": password, "csrf_token": token,
    })


def _response_cookie(app, response):
    cookies = SimpleCookie()
    for header in response.headers.getlist("Set-Cookie"):
        cookies.load(header)
    return cookies[app.config["SESSION_COOKIE_NAME"]]


def _assert_expiration(app, response, expected):
    cookie = _response_cookie(app, response)
    assert parsedate_to_datetime(cookie["expires"]) == expected
    assert cookie["httponly"]
    assert cookie["secure"]
    return cookie.value


def _reopen_browser(app, cookie):
    client = app.test_client()
    client.set_cookie(app.config["SESSION_COOKIE_NAME"], cookie)
    return client


def test_login_persists_for_60_days_and_survives_browser_restart(
    session_app, session_account, session_clock,
):
    account, destination = session_account
    client = session_app.test_client()
    response = _login(client)
    assert response.status_code == 302
    assert response.location == destination
    cookie = _assert_expiration(session_app, response, session_clock.now + LIFETIME)
    with client.session_transaction() as state:
        assert state.permanent
        assert state["user_id"] == account[0]

    session_clock.advance(timedelta(days=59))
    reopened = _reopen_browser(session_app, cookie)
    assert reopened.get(destination).status_code == 200


def test_public_visit_renews_login_beyond_60_days_since_authentication(
    session_app, session_account, session_clock,
):
    _, destination = session_account
    client = session_app.test_client()
    assert _login(client).status_code == 302

    session_clock.advance(timedelta(days=59))
    response = client.get("/")
    assert response.status_code == 302
    assert response.location == destination
    cookie = _assert_expiration(session_app, response, session_clock.now + LIFETIME)

    session_clock.advance(timedelta(days=2))
    reopened = _reopen_browser(session_app, cookie)
    assert reopened.get(destination).status_code == 200


@pytest.mark.parametrize(("inactivity", "expected_status"), [
    (LIFETIME - timedelta(seconds=1), 200),
    (LIFETIME + timedelta(seconds=1), 302),
])
def test_session_expires_only_after_60_days_since_last_visit(
    session_app, session_account, session_clock, inactivity, expected_status,
):
    _, destination = session_account
    client = session_app.test_client()
    assert _login(client).status_code == 302
    session_clock.advance(timedelta(days=17))
    response = client.get("/")
    cookie = _assert_expiration(session_app, response, session_clock.now + LIFETIME)

    session_clock.advance(inactivity)
    reopened = _reopen_browser(session_app, cookie)
    response = reopened.get(destination)
    assert response.status_code == expected_status
    if expected_status == 302:
        assert response.location == "/login"
        with reopened.session_transaction() as state:
            assert "user_id" not in state


@pytest.mark.parametrize("permanent", [False, True])
def test_previous_login_format_requires_new_authentication(
    session_app, session_account, session_clock, permanent,
):
    account, destination = session_account
    client = session_app.test_client()
    with client.session_transaction() as state:
        state["user_id"] = account[0]
        state.permanent = permanent

    response = client.get(destination)
    assert response.location == "/login"
    assert _response_cookie(session_app, response)["max-age"] == "0"
    assert _login(client).location == destination


def test_logout_retires_only_current_login(
    session_app, session_account, session_clock, all_db_path, csrf_from_html,
):
    account, destination = session_account
    client = session_app.test_client()
    assert _login(client).status_code == 302
    other_device = session_app.test_client()
    assert _login(other_device).status_code == 302

    dashboard = client.get(destination)
    csrf_token = csrf_from_html(dashboard.get_data(as_text=True))
    with client.session_transaction() as state:
        login_token = state["login_session_token"]
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    assert client.get(destination).status_code == 200
    response = client.post("/logout", data={"csrf_token": csrf_token})
    assert response.status_code == 302
    assert _response_cookie(session_app, response)["max-age"] == "0"
    assert not database.refresh_login_session(
        login_token, account[0], all_db_path,
        now=int(session_clock.now.timestamp()),
        expires_at=int((session_clock.now + LIFETIME).timestamp()),
    )
    assert client.get(destination).location == "/login"
    assert other_device.get(destination).status_code == 200
    with client.session_transaction() as state:
        assert "user_id" not in state
        assert not state.permanent


def test_protected_route_requires_active_database_session(
    session_app, session_account, session_clock, all_db_path,
):
    _, destination = session_account
    client = session_app.test_client()
    assert _login(client).status_code == 302
    assert client.get(destination).status_code == 200
    with client.session_transaction() as state:
        login_token = state["login_session_token"]

    database.revoke_login_session(login_token, all_db_path)

    response = client.get(destination)
    assert response.location == "/login"
    assert _response_cookie(session_app, response)["max-age"] == "0"


def test_anonymous_visits_do_not_create_persistent_login(session_app, session_clock):
    client = session_app.test_client()
    response = client.get("/health")
    assert response.get_json() == {"status": "ok"}
    assert not response.headers.getlist("Set-Cookie")
    response = client.get("/login")
    assert not _response_cookie(session_app, response)["expires"]
    with client.session_transaction() as state:
        assert "user_id" not in state
        assert not state.permanent


def test_failed_login_does_not_create_persistent_login(
    session_app, session_account, session_clock,
):
    client = session_app.test_client()
    response = _login(client, password="incorrect-test-password")
    assert response.status_code == 200
    for header in response.headers.getlist("Set-Cookie"):
        cookies = SimpleCookie(header)
        assert not cookies[session_app.config["SESSION_COOKIE_NAME"]]["expires"]
    with client.session_transaction() as state:
        assert "user_id" not in state
        assert not state.permanent
