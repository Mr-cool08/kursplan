"""Exercise the deployment smoke script against a real, isolated application."""

from runpy import run_path
from threading import Thread

from flask import redirect
import pytest
from werkzeug.serving import make_server


@pytest.fixture
def release_app(all_db_path, monkeypatch, project_root):
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    monkeypatch.setenv("DEV_MODE", "false")
    monkeypatch.setenv("db_path", "postgresql://unused.invalid/never-connected")
    monkeypatch.setenv("app_secret_key", "release-smoke-test-key")
    monkeypatch.setenv("test_email", "release-admin@example.invalid")
    monkeypatch.setenv("test_password", "release-smoke-test-password")
    monkeypatch.setenv("send_mail", "0")
    monkeypatch.setattr("logging_config.configure_logging", lambda app: None)
    namespace = run_path(str(project_root / "app.py"))
    app = namespace["app"]
    app.config.update(TESTING=True, DB_PATH=all_db_path)
    app.template_folder = str(project_root / "templates")
    app.static_folder = str(project_root / "static")
    namespace["initialize_database"]()
    return app


@pytest.fixture
def release_url(release_app):
    with make_server("127.0.0.1", 0, release_app) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def smoke_test(project_root):
    return run_path(str(project_root / "tests" / "release_smoke.py"))["smoke_test"]


def test_release_smoke_against_running_application(release_url, smoke_test):
    smoke_test(release_url)


@pytest.mark.parametrize(("body", "mimetype", "message"), [
    ("Välkommen", "text/html", "huvudrubrik saknas"),
    ('<h1 id="home-heading">Utbildningar</h1><a href="/login">Logga in</a>',
     "text/html", "skapa konto"),
    ('<h1 id="home-heading">Utbildningar</h1><a href="/signup">Skapa konto</a>',
     "text/html", "inloggningslänken"),
    ('<h1 id="home-heading">Utbildningar</h1><a href="/signup">Konto</a><a href="/login">In</a>',
     "text/plain", "returnerar inte HTML"),
])
def test_release_smoke_rejects_incomplete_homepage(
    release_app, release_url, smoke_test, body, mimetype, message,
):
    release_app.view_functions["user_page.index"] = lambda: release_app.response_class(
        body, mimetype=mimetype,
    )

    with pytest.raises(AssertionError, match=message):
        smoke_test(release_url)


def test_release_smoke_rejects_homepage_redirect(release_app, release_url, smoke_test):
    release_app.view_functions["user_page.index"] = lambda: redirect("/login")

    with pytest.raises(AssertionError, match="omdirigerar"):
        smoke_test(release_url)
