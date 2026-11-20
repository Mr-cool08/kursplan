import os
from html.parser import HTMLParser
from pathlib import Path
from tempfile import gettempdir
from uuid import uuid4

import psycopg
import pytest
from flask import Flask, redirect, session, url_for
from psycopg import sql
from psycopg.conninfo import make_conninfo

import database
from admin import admin_page
from users import user_page


PROJECT_ROOT = Path(__file__).resolve().parents[1]
USER_EMAIL = "route-user@example.com"
USER_PASSWORD = "route-user-password"


class _CsrfTokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "csrf_token":
            self.token = attributes.get("value")


@pytest.fixture(scope="session")
def postgres_dsn():
    if test_url := os.getenv("TEST_DATABASE_URL"):
        yield test_url
        return

    # The embedded server's root mode modifies OS users and directory permissions.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.fail("Run pytest as a regular user or set TEST_DATABASE_URL.", pytrace=False)

    try:
        from embedded_postgres import get_server
    except ModuleNotFoundError as exc:
        if exc.name != "embedded_postgres":
            raise
        pytest.fail("Install test dependencies: pip install -r requirements-test.txt", pytrace=False)

    # On Windows the server drops admin privileges, so inherit the temp folder's ACL.
    data_dir = Path(gettempdir()) / f"kursplan-pg-{uuid4().hex[:16]}"
    data_dir.mkdir(mode=0o755 if os.name == "nt" else 0o700)
    with get_server(data_dir, cleanup_mode="delete") as server:
        yield server.get_uri()


@pytest.fixture
def db_path(postgres_dsn):
    # Database functions open their own connections, so isolate the entire test database.
    name = f"kursplan_test_{uuid4().hex}"
    identifier = sql.Identifier(name)
    test_dsn = make_conninfo(postgres_dsn, dbname=name)
    with psycopg.connect(postgres_dsn, autocommit=True, connect_timeout=5) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(identifier))
        try:
            database.create_database(test_dsn)
            yield test_dsn
        finally:
            conn.execute(sql.SQL("DROP DATABASE {}").format(identifier))


@pytest.fixture
def project_root():
    return PROJECT_ROOT


@pytest.fixture
def csrf_from_html():
    def extract(html):
        parser = _CsrfTokenParser()
        parser.feed(html)
        assert parser.token, "Bokningsformuläret saknar CSRF-token."
        return parser.token

    return extract


@pytest.fixture
def app(db_path, monkeypatch):
    monkeypatch.setenv("send_mail", "0")
    test_app = Flask(
        "kursplan-tests",
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    test_app.config.update(
        DB_PATH=db_path,
        SECRET_KEY="booking-route-test-secret",
        TESTING=True,
    )
    test_app.register_blueprint(user_page)
    test_app.register_blueprint(admin_page)

    @test_app.route("/logout", endpoint="logout")
    def logout():
        session.clear()
        return redirect(url_for("user_page.index"))

    return test_app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(db_path):
    return database.create_user(
        "Route User",
        USER_EMAIL,
        USER_PASSWORD,
        "5566778899",
        "user",
        db_path,
    )


@pytest.fixture
def logged_in_client(client, user):
    client.get("/login")
    with client.session_transaction() as state:
        csrf_token = state["booking_csrf_token"]
    response = client.post(
        "/login",
        data={"email": USER_EMAIL, "password": USER_PASSWORD, "csrf_token": csrf_token},
    )
    assert response.status_code == 302
    assert response.location.endswith("/dashboard")
    return client


@pytest.fixture
def booking_csrf_token(logged_in_client, csrf_from_html):
    response = logged_in_client.get("/booking")
    assert response.status_code == 200
    return csrf_from_html(response.get_data(as_text=True))


@pytest.fixture
def valid_booking_data(booking_csrf_token):
    return {
        "csrf_token": booking_csrf_token,
        "datumtyp": "exakt",
        "onskat_datum": "2027-03-18",
        "onskat_startdatum": "",
        "onskat_slutdatum": "",
        "utbildningskalla": "katalog",
        "utbildning": "HLR",
        "egen_utbildning": "",
        "ort": "Göteborg",
        "antal": "3",
        "lokalansvar": "kund",
        "lokaladress": "Testgatan 1",
    }
